import copy
import json
import tempfile
import unittest
from pathlib import Path

from api.catalog import Catalog
from pipeline import blueprint
from pipeline.storyboard import draft_storyboard, storyboard_timeline
from pipeline.structure import structure_check


def build(count=3, plan=True):
    clips = [{"id": f"c{index}", "start": index * 30, "end": index * 30 + 10, "title": f"Idea {index}",
              "takeaway": f"Takeaway {index}.", "excerpt": f"Excerpt {index}."} for index in range(count)]
    storyboard = draft_storyboard(clips, "Kids")
    storyboard["opening"].update(title="Why do nations differ?", subtitle="Skills and wages")
    storyboard["ending"].update(takeaway="Skills shape growth.", cta="Compare two countries.")
    for index, join in enumerate(storyboard["transitions"]):
        join["label"] = f"How does idea {index + 1} connect?"
    timeline = storyboard_timeline(clips, [10.0] * count, storyboard)
    for item in timeline:
        if item["kind"] == "opening":
            item["narration"] = {"text": storyboard["opening"]["title"]}
        elif item["kind"] == "divider":
            item["narration"] = {"text": item["text"]}
        elif item["kind"] == "ending":
            item["narration"] = {"text": "Skills shape growth. Compare two countries."}
    manifest = {"clips": clips, "storyboard": storyboard, "timeline": timeline, "blueprint": None}
    if plan:
        manifest["blueprint"] = blueprint.template_blueprint(clips, "Kids")
    return manifest


def metric(result, identifier):
    return next(item for item in result["metrics"] if item["id"] == identifier)


class StructureCheckTests(unittest.TestCase):
    def test_complete_export_with_blueprint_passes_every_check(self):
        manifest = build()
        result = structure_check(manifest, 40.5)
        self.assertEqual(result["status"], "passed", [item for item in result["metrics"] if item["status"] != "pass"])
        self.assertEqual(result["counts"]["pass"], 12)
        self.assertEqual({item["id"] for item in result["metrics"]} >= {"blueprint_clip_count", "blueprint_timing"}, True)
        rows = result["blueprint"]["rows"]
        self.assertEqual([row["section"] for row in rows],
                         ["Clip 1", "Clip 2", "Clip 3", "Opening", "Bridge 1", "Bridge 2", "Ending", "Ending action"])
        self.assertEqual(rows[3]["actual"], "Why do nations differ? · Skills and wages")
        self.assertEqual(rows[4]["actual"], "How does idea 1 connect?")

    def model_plan(self, **ideas):
        manifest = build()
        plan = manifest["blueprint"]
        plan["generated"]["status"] = "model_plan"
        plan["opening"]["idea"] = "Ask why nations differ in skills and wages"
        plan["bridges"][0]["idea"] = "Ask how idea 1 connects to the next"
        plan["bridges"][1]["idea"] = "Ask how idea 2 connects to the last"
        plan["ending"]["synthesis"] = "Skills shape growth across nations"
        for index, idea in ideas.items():
            plan["bridges"][int(index[-1]) - 1]["idea"] = idea
        return manifest

    def test_wording_that_reuses_the_plan_follows_it(self):
        result = structure_check(self.model_plan(), 40.5)
        rows = {row["section"]: row for row in result["blueprint"]["rows"]}
        self.assertEqual([rows[name]["match"] for name in ("Opening", "Bridge 1", "Bridge 2", "Ending")], ["follows"] * 4)
        self.assertIsNone(rows["Clip 1"]["match"])
        self.assertIsNone(rows["Ending action"]["match"])
        self.assertEqual(metric(result, "blueprint_wording")["status"], "pass")
        self.assertEqual(result["status"], "passed")

    def test_clearly_different_wording_needs_review_and_names_the_section(self):
        result = structure_check(self.model_plan(bridge2="Describe ocean currents and tides"), 40.5)
        rows = {row["section"]: row for row in result["blueprint"]["rows"]}
        self.assertEqual(rows["Bridge 2"]["match"], "differs")
        self.assertEqual(rows["Bridge 1"]["match"], "follows")
        wording = metric(result, "blueprint_wording")
        self.assertEqual(wording["status"], "review")
        self.assertIn("Bridge 2", wording["exported"])
        self.assertEqual(result["counts"]["fail"], 0)

    def test_template_plans_are_not_compared_word_for_word(self):
        result = structure_check(build(), 40.5)
        self.assertTrue(all(row["match"] is None for row in result["blueprint"]["rows"]))
        self.assertNotIn("blueprint_wording", [item["id"] for item in result["metrics"]])

    def test_fixed_cta_is_not_shown_as_a_mismatch(self):
        manifest = build()
        manifest["storyboard"]["ending"]["cta"] = "Full lecture in description."
        row = structure_check(manifest, 40.5)["blueprint"]["rows"][-1]
        self.assertEqual((row["section"], row["planned"]), ("Ending action", row["actual"]))

    def test_export_without_a_blueprint_still_checks_the_contract(self):
        result = structure_check(build(plan=False), 40.5)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(metric(result, "blueprint_recorded")["status"], "not_applicable")
        self.assertIsNone(result["blueprint"])

    def test_legacy_export_without_a_timeline_is_unavailable(self):
        result = structure_check({"clips": [], "storyboard": None, "timeline": None})
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["metrics"], [])
        self.assertIn("no storyboard timeline", result["note"])

    def test_structural_violations_fail(self):
        cases = {}
        swapped = build()
        clip_items = [item for item in swapped["timeline"] if item["kind"] == "clip"]
        clip_items[0]["clip_id"], clip_items[1]["clip_id"] = clip_items[1]["clip_id"], clip_items[0]["clip_id"]
        cases["clip_order"] = swapped
        missing = build()
        missing["timeline"] = [item for item in missing["timeline"] if item["kind"] != "divider"][:]
        cases["section_order"] = missing
        label = build()
        next(item for item in label["timeline"] if item["kind"] == "divider")["text"] = "Something else"
        cases["bridges_match"] = label
        spoken = build()
        spoken["timeline"][0]["narration"]["text"] = "Different hook"
        cases["narration_copy"] = spoken
        long = build()
        long["timeline"][-1]["duration"] = 40
        cases["duration_budget"] = long
        uncited = build()
        uncited["storyboard"]["evidence"] = uncited["storyboard"]["evidence"][:2]
        cases["evidence_coverage"] = uncited
        for identifier, manifest in cases.items():
            with self.subTest(identifier):
                result = structure_check(manifest, sum(item["duration"] for item in manifest["timeline"]))
                self.assertEqual(metric(result, identifier)["status"], "fail")
                self.assertEqual(result["status"], "needs_attention")

    def test_video_length_must_match_the_timeline_and_may_be_unavailable(self):
        manifest = build()
        self.assertEqual(metric(structure_check(manifest, 47.0), "video_matches_timeline")["status"], "fail")
        unknown = structure_check(manifest, None)
        self.assertEqual(metric(unknown, "video_matches_timeline")["status"], "unavailable")
        self.assertEqual(unknown["status"], "passed")

    def test_blueprint_deviations_need_review_but_do_not_fail(self):
        manifest = build()
        manifest["blueprint"] = blueprint.template_blueprint(manifest["clips"][:2], "Kids")
        result = structure_check(manifest, 40.5)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(result["counts"]["fail"], 0)
        for identifier in ("blueprint_clip_count", "blueprint_clip_set", "blueprint_bridges"):
            self.assertEqual(metric(result, identifier)["status"], "review")
        late = build()
        late["blueprint"] = copy.deepcopy(late["blueprint"])
        late["blueprint"]["timing"]["estimated_total"] = 20
        self.assertEqual(metric(structure_check(late, 40.5), "blueprint_timing")["status"], "review")

    def test_every_check_explains_itself_in_plain_language(self):
        result = structure_check(build(), 40.5)
        for item in result["metrics"]:
            with self.subTest(item["id"]):
                self.assertTrue(item["checks"].strip() and item["exported"].strip())
                self.assertIsNone(item["fix"], "passing checks have no action")
        self.assertEqual(metric(result, "section_order")["exported"], "Opening → Clip 1 → Bridge → Clip 2 → Bridge → Clip 3 → Ending")
        self.assertEqual(metric(result, "clip_order")["exported"], "Idea 0 → Idea 1 → Idea 2")
        self.assertEqual(metric(result, "bridges_match")["exported"], "All 2 bridges match")
        self.assertEqual(metric(result, "duration_budget")["exported"], "40.5 seconds")
        self.assertEqual(metric(result, "video_matches_timeline")["exported"], "Video 40.5 s · recorded plan 40.5 s")
        self.assertEqual(metric(result, "blueprint_timing")["exported"], "Estimated 41.0 s · exported 40.5 s (-0.5 s)")
        manifest = build()
        manifest["blueprint"] = blueprint.template_blueprint(manifest["clips"][:2], "Kids")
        review = structure_check(manifest, 40.5)
        self.assertEqual(metric(review, "blueprint_clip_count")["exported"], "Planned 2 · exported 3")
        self.assertIn("Idea 0, Idea 1", metric(review, "blueprint_clip_set")["exported"])
        self.assertIn("Idea 0 → Idea 1", metric(review, "blueprint_bridges")["exported"])
        self.assertIn("on purpose", metric(review, "blueprint_clip_count")["fix"])
        failing = build()
        failing["timeline"][-1]["duration"] = 40
        broken = structure_check(failing, 75.5)
        self.assertEqual(metric(broken, "duration_budget")["exported"], "75.5 seconds")
        self.assertIn("shorter cards", metric(broken, "duration_budget")["fix"])
        self.assertEqual(metric(structure_check(build(), None), "video_matches_timeline")["exported"], "The video length could not be measured")

    def test_technical_checks_get_the_same_plain_fields(self):
        from pipeline.assess import metric_story
        speed = {"id": "caption_speed", "label": "Sidecar reading speed", "status": "fail", "threshold": "Maximum 20",
                 "value": {"max_characters_per_second": 39.0, "limit_characters_per_second": 20, "audience_policy": "Researcher",
                           "cues_above_limit": 6, "fastest_cue_start_seconds": 19.48, "fastest_cue_text": "Singapore would have been"}}
        story = metric_story(speed)
        self.assertIn("reading-speed", story["checks"])
        self.assertIn("6 caption(s) too fast", story["exported"])
        self.assertIn("Review the flagged passage", story["fix"])
        passing = metric_story({"id": "loudness", "label": "Integrated loudness", "status": "pass", "threshold": "t", "value": -16.2})
        self.assertEqual(passing, {"checks": "Overall volume is in a comfortable range.", "exported": "-16.2 LUFS", "fix": None})
        unknown = metric_story({"id": "future_metric", "label": "Future metric", "status": "fail", "threshold": "t", "value": 3})
        self.assertEqual(unknown, {"checks": "Future metric", "exported": "3", "fix": None})

    def test_catalog_detail_includes_the_structure_check(self):
        manifest = build()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            folder = root / "exports" / "run" / "20261017-000000_kids_16x9_abcd"
            folder.mkdir(parents=True)
            (folder / "compilation.mp4").write_bytes(b"not a real video")
            (folder / "selection.json").write_text(json.dumps({**manifest, "intro_title": "Why do nations differ?",
                                                               "selection_context": {"target_audience": "Kids"}}))
            detail = Catalog(root).describe(folder, detailed=True)
        self.assertEqual(metric(detail["structure"], "video_matches_timeline")["status"], "unavailable")
        self.assertEqual(detail["structure"]["status"], "passed")
        self.assertEqual(detail["structure"]["blueprint"]["status"], "template_plan")


if __name__ == "__main__":
    unittest.main()
