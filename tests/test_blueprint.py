import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from pipeline import blueprint, workflow


def clips(count=3, seconds=10):
    return [{"id": f"c{index}", "start": index * 30, "end": index * 30 + seconds, "title": f"Idea {index} about assumptions",
             "takeaway": f"Takeaway {index}.", "excerpt": f"Excerpt {index}."} for index in range(count)]


def model_reply(items, **overrides):
    result = {"clip_count_reason": "Three clips fit the budget",
              "clips": [{"id": item["id"], "role": "Evidence for the main claim today"} for item in items],
              "opening": {"approach": "contrast", "idea": "Ask why similar nations end up so different"},
              "bridges": [{"before_clip_id": item["id"], "relationship": "example", "idea": f"Show an example after {item['id']}"}
                          for item in items[1:]],
              "ending": {"synthesis": "Institutions and skills shape growth", "action": "Compare two countries"}}
    result.update(overrides)
    return {"message": {"content": json.dumps(result)}, "provider": "portkey"}


class BlueprintTests(unittest.TestCase):
    def test_timing_uses_card_minimums_and_budget(self):
        timing = blueprint.blueprint_timing(clips(3, 10))
        self.assertEqual(timing["speech_seconds"], 30)
        self.assertEqual(timing["estimated_total"], 30 + 3 + 3 + 5)
        self.assertEqual(timing["budget"], [30, 60])
        self.assertTrue(timing["fits"])
        self.assertFalse(blueprint.blueprint_timing(clips(1, 5))["fits"])
        self.assertFalse(blueprint.blueprint_timing(clips(5, 14))["fits"])

    def test_risks_flag_single_clip_and_budget_edges(self):
        single = blueprint.template_blueprint(clips(1, 5), "Kids")
        self.assertTrue(any("no bridges" in risk for risk in single["risks"]))
        self.assertTrue(any("under the 30s" in risk for risk in single["risks"]))
        long = blueprint.template_blueprint(clips(3, 18), "Kids")
        self.assertTrue(any("over the 60s" in risk for risk in long["risks"]))
        close = blueprint.template_blueprint(clips(3, 16), "Kids")
        self.assertTrue(any("Close to the maximum" in risk for risk in close["risks"]))
        self.assertEqual(blueprint.template_blueprint(clips(3, 10), "Kids")["risks"], [])

    def test_template_plan_covers_every_clip_and_join(self):
        plan = blueprint.template_blueprint(clips(3), "General public")
        self.assertEqual(plan["generated"]["status"], "template_plan")
        self.assertEqual(plan["clip_count"], 3)
        self.assertEqual([item["role"] for item in plan["clips"]], ["Setup", "Development", "Payoff"])
        self.assertEqual([(item["from_clip_id"], item["to_clip_id"]) for item in plan["bridges"]], [("c0", "c1"), ("c1", "c2")])
        self.assertEqual(plan["ending"]["synthesis"], "Takeaway 2.")
        again = blueprint.template_blueprint(clips(3), "General public")
        self.assertEqual(plan["revision"], again["revision"])
        self.assertNotEqual(plan["revision"], blueprint.template_blueprint(clips(2), "General public")["revision"])

    def test_model_plan_is_normalised_and_uses_one_request(self):
        items = clips(3)
        request = Mock(return_value=model_reply(items, opening={"approach": "unknown", "idea": " ".join(["word"] * 40)}))
        plan = blueprint.generate_blueprint("model", items, "Researcher", request=request)
        request.assert_called_once()
        self.assertEqual(plan["generated"]["status"], "model_plan")
        self.assertEqual(plan["generated"]["provider"], "portkey")
        self.assertEqual(len(plan["opening"]["idea"].split()), blueprint.IDEA_WORDS)
        self.assertEqual(plan["opening"]["approach"], "question")
        self.assertEqual(plan["clips"][0]["role"], "Evidence for the main")
        self.assertEqual([item["idea"] for item in plan["bridges"]], ["Show an example after c1", "Show an example after c2"])
        self.assertEqual({item["relationship"] for item in plan["bridges"]}, {"example"})
        schema = request.call_args.kwargs["format"]
        self.assertEqual(schema["properties"]["clips"]["items"]["properties"]["id"]["enum"], ["c0", "c1", "c2"])
        self.assertEqual(schema["properties"]["bridges"]["items"]["properties"]["before_clip_id"]["enum"], ["c1", "c2"])
        payload = json.loads(request.call_args.kwargs["messages"][1]["content"])
        self.assertEqual([item["id"] for item in payload["selected_clips_in_order"]], ["c0", "c1", "c2"])
        self.assertEqual(payload["timing"]["estimated_total"], 41)

    def test_single_clip_plan_has_no_bridges_and_an_unconstrained_bridge_schema(self):
        items = clips(1, 20)
        request = Mock(return_value=model_reply(items))
        plan = blueprint.generate_blueprint("model", items, "Kids", request=request)
        self.assertEqual(plan["bridges"], [])
        self.assertEqual(request.call_args.kwargs["format"]["properties"]["bridges"], {"type": "array", "items": {"type": "object"}})

    def test_missing_bridge_keeps_the_template_idea(self):
        items = clips(3)
        plan = blueprint.generate_blueprint("model", items, "Kids", request=Mock(return_value=model_reply(items[:2])))
        self.assertEqual(plan["generated"]["status"], "template_plan")
        reply = model_reply(items)
        content = json.loads(reply["message"]["content"])
        content["bridges"] = content["bridges"][:1]
        reply["message"]["content"] = json.dumps(content)
        plan = blueprint.generate_blueprint("model", items, "Kids", request=Mock(return_value=reply))
        self.assertEqual(plan["generated"]["status"], "model_plan")
        self.assertEqual(plan["bridges"][0]["idea"], "Show an example after c1")
        self.assertTrue(plan["bridges"][1]["idea"].startswith("Move from"))

    def test_failures_degrade_to_a_template_plan_without_raising(self):
        items = clips(3)
        for label, request in (("cloud", Mock(side_effect=RuntimeError("Portkey text request failed"))),
                               ("json", Mock(return_value={"message": {"content": "not json"}})),
                               ("shape", Mock(return_value={"message": {"content": "{}"}})),
                               ("empty opening", Mock(return_value=model_reply(items, opening={"approach": "question", "idea": " "})))):
            with self.subTest(label):
                plan = blueprint.generate_blueprint("model", items, "Kids", request=request)
                self.assertEqual(plan["generated"]["status"], "template_plan")
                self.assertIn("Model plan unavailable", plan["generated"]["note"])
                self.assertEqual(plan["clip_count"], 3)
                request.assert_called_once()

    def test_ending_action_is_the_fixed_cta_unless_a_source_link_is_given(self):
        items = clips(3)
        plain = blueprint.generate_blueprint("model", items, "Kids", request=Mock(return_value=model_reply(items)))
        self.assertEqual(plain["ending"]["action"], "Full lecture in description.")
        linked = blueprint.generate_blueprint("model", items, "Kids", "https://example.org/l", request=Mock(return_value=model_reply(items)))
        self.assertEqual(linked["ending"]["action"], "Compare two countries")
        failed = blueprint.generate_blueprint("model", items, "Kids", request=Mock(side_effect=RuntimeError("down")))
        self.assertEqual(failed["ending"]["action"], "Full lecture in description.")

    def test_rejects_empty_selection_and_unknown_audience(self):
        with self.assertRaisesRegex(ValueError, "Confirm clips"):
            blueprint.generate_blueprint("model", [], "Kids", request=Mock())
        with self.assertRaisesRegex(ValueError, "Select Researcher"):
            blueprint.generate_blueprint("model", clips(2), "Adults", request=Mock())

    def test_final_comparison_does_not_rewrite_the_plan(self):
        plan = blueprint.template_blueprint(clips(3), "Kids")
        final = blueprint.with_final(plan, clips(3)[:2], 38.4)
        self.assertEqual(final["revision"], plan["revision"])
        self.assertEqual(final["clips"], plan["clips"])
        self.assertEqual(final["final"], {"clip_count": 2, "same_clips": False, "measured_total": 38.4,
                                          "difference": round(38.4 - plan["timing"]["estimated_total"], 1)})
        self.assertNotIn("final", plan)
        self.assertIsNone(blueprint.with_final(plan, clips(3), None)["final"]["measured_total"])


class BlueprintWorkflowTests(unittest.TestCase):
    def test_workflow_publishes_the_blueprint_once_before_generation_ends(self):
        plan = blueprint.template_blueprint(clips(2), "Kids")
        stages = [({"approved": []},), ({"approved": [], "blueprint": plan},), ({"approved": [], "blueprint": blueprint.with_final(plan, clips(2), 40)},),
                  ({"approved": [], "blueprint": blueprint.with_final(plan, clips(2), 40), "storyboard_preview_path": "preview.mp4"},)]
        published = []
        state = {"approved": [], "aspect_ratio": "16:9", "model": "m"}
        with unittest.mock.patch("pipeline.workflow.select", side_effect=lambda current, _: current), unittest.mock.patch(
                "app.generate_package_storyboard", return_value=iter(stages)), unittest.mock.patch("pipeline.workflow.save_checkpoint"):
            result = workflow.generate(state, Mock(), publish=lambda early: published.append(early))
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["blueprint"]["revision"], plan["revision"])
        self.assertEqual(result["storyboard_preview_path"], "preview.mp4")

    def test_selection_change_clears_the_blueprint(self):
        plan = blueprint.template_blueprint(clips(2), "Kids")
        state = {"approved": clips(2), "blueprint": plan, "storyboard_draft": {"x": 1}}
        with unittest.mock.patch("app.confirm_selection", side_effect=lambda current, *rows: (current,)), unittest.mock.patch(
                "pipeline.workflow.save_checkpoint"):
            kept = workflow.select(state, [dict(clip) for clip in clips(2)])
            self.assertEqual(kept["blueprint"], plan)
            changed = workflow.select(state, [dict(clip, end=clip["end"] + 1) for clip in clips(2)])
        self.assertIsNone(changed["blueprint"])
        self.assertIsNone(changed["storyboard_draft"])

    def test_snapshot_exposes_the_blueprint(self):
        from api.studio import Studio
        plan = blueprint.template_blueprint(clips(2), "Kids")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lecture.mp4"
            source.write_bytes(b"video")
            studio = Studio(root / "studio")
            record = studio.add(source, "lecture.mp4", 60)
            self.assertIsNone(studio.snapshot(record)["blueprint"])
            record = studio.update(record["id"], {"blueprint": plan})
            self.assertEqual(studio.snapshot(record)["blueprint"]["revision"], plan["revision"])


if __name__ == "__main__":
    unittest.main()
