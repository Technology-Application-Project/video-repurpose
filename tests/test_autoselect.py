import unittest
from unittest.mock import Mock, patch

import app
from pipeline.autoselect import auto_select, estimated_seconds
from pipeline.contracts import selection_revision


def candidate(cid, start, length, status="estimated", score=80):
    return {"id": cid, "start": start, "end": start + length, "title": f"Title {cid}", "takeaway": f"Takeaway {cid}",
            "editorial_status": status, "score": score, "excerpt": f"Excerpt {cid}", "preview_path": None}


class AutoSelectTests(unittest.TestCase):
    def test_takes_ranked_clips_until_the_budget_is_used(self):
        ranked = [candidate("a", 0, 10), candidate("b", 50, 12), candidate("c", 100, 14), candidate("d", 150, 9)]
        chosen, record = auto_select(ranked, "General public")
        self.assertEqual(chosen, ["a", "b", "c"])
        self.assertEqual([item["id"] for item in record["skipped"]], ["d"])
        self.assertEqual(record["mode"], "automatic")
        self.assertFalse(record["human_review"])
        self.assertEqual(record["ranges"][1], ["b", 50.0, 62.0])

    def test_rejected_and_over_budget_clips_are_skipped_with_a_reason(self):
        ranked = [candidate("a", 0, 20), candidate("x", 30, 10, status="rejected", score=None), candidate("b", 50, 20),
                  candidate("c", 100, 20)]
        chosen, record = auto_select(ranked, "General public")
        self.assertEqual(chosen, ["a", "b"])
        reasons = {item["id"]: item["reason"] for item in record["skipped"]}
        self.assertIn("rejected", reasons["x"])
        self.assertIn("budget", reasons["c"])

    def test_kids_reserve_more_card_time(self):
        self.assertEqual(estimated_seconds([20, 20], "Kids") - estimated_seconds([20, 20], "Researcher"), 4)
        pair = [candidate("a", 0, 21), candidate("b", 50, 21)]
        self.assertEqual(len(auto_select(pair, "Researcher")[0]), 2)
        self.assertEqual(auto_select(pair, "Kids")[0], ["a"])

    def test_unscored_clips_are_included_but_flagged_and_nothing_usable_raises(self):
        chosen, record = auto_select([candidate("a", 0, 10), candidate("b", 50, 10, status="unavailable", score=None)], "Kids")
        self.assertEqual(chosen, ["a", "b"])
        self.assertTrue(any("b" in warning and "editorial score" in warning for warning in record["warnings"]))
        self.assertEqual(record["editorial"]["b"]["status"], "unavailable")
        with self.assertRaisesRegex(ValueError, "fits"):
            auto_select([candidate("long", 0, 80)], "General public")
        with self.assertRaises(ValueError):
            auto_select([candidate("x", 0, 10, status="rejected", score=None)], "General public")

    def test_short_selection_is_warned_about(self):
        _, record = auto_select([candidate("a", 0, 6)], "General public")
        self.assertTrue(any("minimum" in warning for warning in record["warnings"]))


class AutomaticSelectionAppTests(unittest.TestCase):
    def nominated(self, candidates):
        state = app.empty_state()
        state.update(run_id="r", source_duration=600, target_audience="General public", min_s=6, max_s=15,
                     transcript={"segments": []}, candidates=candidates)
        rows = []
        for index in range(app.MAX_REVIEW):
            rows.extend(app._row_from_candidate(candidates[index]) if index < len(candidates) else app._blank_row())
        return (state, "Nominated", [], "**Kept: 0**", {"interactive": False}, {"interactive": False}, *rows)

    def run_nomination(self, candidates, automatic):
        with patch("app.nominate_and_rank", return_value=self.nominated(candidates)):
            return app.nominate_for_audience(app.empty_state(), "model", "General public", 6, 15, automatic, progress=Mock())

    def test_automatic_nomination_keeps_and_confirms_without_a_human(self):
        candidates = [candidate("a", 0, 10), candidate("b", 100, 12), candidate("c", 200, 9), candidate("d", 300, 9)]
        result = self.run_nomination(candidates, True)
        state, status, kept_md, confirm_button, rows = result[0], result[1], result[3], result[4], result[6:]
        self.assertEqual([clip["id"] for clip in state["approved"]], ["a", "b", "c"])
        self.assertTrue(state["confirmed"])
        self.assertEqual(state["approval_revision"], selection_revision(state, state["approved"]))
        self.assertEqual(state["selection"]["mode"], "automatic")
        self.assertFalse(state["selection"]["human_review"])
        self.assertIn("no human review", status)
        self.assertIn("automatically", kept_md)
        self.assertTrue(confirm_button["interactive"])
        self.assertEqual([rows[9 * index + 8] for index in range(4)], [True, True, True, False])

    def test_manual_mode_is_unchanged_and_nothing_is_confirmed(self):
        result = self.run_nomination([candidate("a", 0, 10)], False)
        self.assertFalse(result[0]["confirmed"])
        self.assertEqual(result[0]["approved"], [])
        self.assertIsNone(result[0]["selection"])

    def test_automatic_selection_survives_confirm_and_fitting_but_not_a_human_edit(self):
        result = self.run_nomination([candidate("a", 0, 10), candidate("b", 100, 12)], True)
        state, rows = result[0], list(result[6:])
        columns = [[rows[9 * index + offset] for index in range(app.MAX_REVIEW)] for offset in (2, 3, 4, 6, 7, 8)]
        confirmed = app.confirm_selection(state, *[value for column in columns for value in column])[0]
        self.assertEqual(confirmed["selection"]["mode"], "automatic")
        self.assertEqual(confirmed["selection"]["confirmed_ranges"][0][0], "a")
        edited = app.count_kept(confirmed, *columns[5])[0]
        self.assertIsNone(edited["selection"])
        self.assertFalse(edited["confirmed"])
        manual = app.confirm_selection(edited, *[value for column in columns for value in column])[0]
        self.assertEqual(manual["selection"], {"mode": "manual", "human_review": True})

    def test_no_usable_clip_leaves_the_selection_to_the_human(self):
        result = self.run_nomination([candidate("long", 0, 80)], True)
        self.assertFalse(result[0]["confirmed"])
        self.assertIn("Automatic selection stopped", result[1])

    def test_selection_mode_is_exported_with_the_selection_context(self):
        import inspect
        self.assertIn('"selection": state.get("selection")', inspect.getsource(app.do_export))

    def test_candidate_table_marks_the_automatic_choice_and_the_reviewer_sees_what_was_automatic(self):
        result = self.run_nomination([candidate("a", 0, 10), candidate("b", 100, 12)], True)
        self.assertEqual(result[1].count("automatically"), 1)
        self.assertIn("selected automatically (a, b)", result[3])
        state = result[0]
        draft = {"opening": {"accent": "richer"}, "ending": {"accent": "societies"}, "transitions": [{"accent": "invested"}],
                 "narration_profile": {"voice": "en-US-AvaNeural"},
                 "media_assets": {"music": {"provider": "library", "title": "Open Road", "license": "CC0-1.0"}}}
        details = app.storyboard_details(state, draft)
        for expected in ("selected automatically", "en-US-AvaNeural", "Open Road (library, CC0-1.0)", "richer, invested, societies"):
            self.assertIn(expected, details)
        self.assertIn("kept manually", app.storyboard_details({"selection": {"mode": "manual"}}, draft))
        self.assertIn("Music: generated", app.storyboard_details(state, {**draft, "media_assets": {"music": {"model": "m"}}}))

    def test_assessment_states_how_clips_were_chosen(self):
        from pipeline.assess import selection_label
        self.assertIn("automatic, no human review", selection_label({"selection": {"mode": "automatic"}}))
        self.assertIn("human reviewer", selection_label({"selection": {"mode": "manual"}}))
        self.assertIn("not recorded", selection_label({}))


if __name__ == "__main__":
    unittest.main()
