import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
from pipeline.assess import ARTIFACTS, DIMENSIONS, GATES, artifact_hashes, assess_export, review_template, score_review
from pipeline.contracts import selection_revision
from pipeline.export import export_clips
from pipeline.nominate import _describe_candidate, _normalize_candidates, nominate_candidates
from pipeline.rank import rank_candidates
from pipeline.editorial import infer_lecture_context, repair_sentence_boundaries, review_candidate
from pipeline.storyboard import draft_storyboard, generate_storyboard, storyboard_revision, storyboard_timeline, validate_storyboard


class StoryboardTests(unittest.TestCase):
    def test_title_narration_requires_available_local_synthesizer(self):
        from pipeline.assemble import _synthesize_title

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "title.aiff"
            with patch("pipeline.assemble.shutil.which", return_value=None), patch(
                "pipeline.narrate.engine", return_value=None
            ):
                with self.assertRaisesRegex(RuntimeError, "speech synthesizer"):
                    _synthesize_title("Title", output)
            with patch("pipeline.assemble.shutil.which", return_value="/usr/bin/say"), patch(
                "pipeline.assemble.run_cmd"
            ) as run:
                self.assertEqual(_synthesize_title("Check the evidence", output), output)
                self.assertEqual(output.with_suffix(".txt").read_text(), "Check the evidence")
                self.assertEqual(run.call_args.args[0], ["/usr/bin/say", "-v", "Samantha", "-r", "175",
                                                       "-f", str(output.with_suffix(".txt")), "-o", str(output)])

    def test_separate_opening_offsets_speech_and_preserves_legacy_overlay(self):
        clips = self.generation_clips()
        draft = draft_storyboard(clips, "Students")
        timeline = storyboard_timeline(clips, [25, 25], draft)
        self.assertEqual(timeline[0], {"kind": "opening", "start": 0.0, "duration": 3.0})
        self.assertEqual(timeline[1]["start"], 3.0)
        self.assertEqual(timeline[-1]["start"] + timeline[-1]["duration"], 58.0)
        del draft["opening"]["mode"]
        del draft["opening"]["narration"]
        legacy = storyboard_timeline(clips, [25, 25], draft)
        self.assertEqual(legacy[0]["kind"], "clip")
        self.assertEqual(legacy[0]["start"], 0)

    def test_storyboard_controls_follow_draft_and_preview_state(self):
        def enabled(state):
            return tuple(update["interactive"] for update in app.storyboard_control_updates(state))

        self.assertEqual(enabled(None), (False, False))
        state, _ = PipelineTests().selection()
        self.assertEqual(enabled(state), (False, False))
        state, values, _ = self.package()
        self.assertEqual(enabled(state), (True, False))
        _, revision = app._read_storyboard(state, values)
        state["storyboard_preview_revision"] = revision
        self.assertEqual(enabled(state), (True, True))
        self.assertEqual(enabled(app.invalidate_package_storyboard(state)[0]), (True, False))
        self.assertEqual(enabled(dict(state, confirmed=False)), (False, False))
        self.assertEqual(enabled(dict(state, storyboard_selection_revision="stale")), (False, False))
        demo = app.build_app()
        buttons = {component["props"].get("value"): component["props"]
                   for component in demo.config["components"] if component["type"] == "button"}
        self.assertFalse(buttons["Preview storyboard"]["interactive"])
        self.assertFalse(buttons["Approve storyboard"]["interactive"])
        self.assertTrue(any(callback.fn is app.storyboard_control_updates for callback in demo.fns.values()))

    def test_confirmation_generates_storyboard_without_separate_button(self):
        state, rows = PipelineTests().selection()
        generated = draft_storyboard(state["approved"], "Students")
        state.update(confirmed=False, approved=[], approval_revision=None)

        def preview_result(confirmed, *values, **kwargs):
            return confirmed, "Preview ready", "preview.mp4", False, {"interactive": False}

        with patch("app.generate_storyboard", return_value=generated) as generate, patch(
            "app.preview_package_storyboard", side_effect=preview_result
        ) as preview:
            outputs = list(app.confirm_and_generate_storyboard(state, "9:16", "selected-model", *rows, progress=Mock()))
        offset = 2 + 3 * app.MAX_KEEP
        self.assertEqual(len(outputs), 3)
        self.assertTrue(all(output[offset]["confirmed"] for output in outputs))
        self.assertTrue(all(not output[offset + 4]["interactive"] for output in outputs))
        self.assertEqual(outputs[-1][offset + 2], "preview.mp4")
        self.assertEqual(generate.call_args.args[0], "selected-model")
        self.assertEqual(generate.call_args.args[1], outputs[-1][offset]["approved"])
        preview.assert_called_once()
        self.assertFalse(state["confirmed"])
        demo = app.build_app()
        callback = next(callback for callback in demo.fns.values() if callback.fn is app.confirm_and_generate_storyboard)
        self.assertTrue(all(len(output) == len(callback.outputs) for output in outputs))
        self.assertEqual(len(callback.outputs), len({component._id for component in callback.outputs}))
        buttons = [component["props"].get("value") for component in demo.config["components"] if component["type"] == "button"]
        self.assertNotIn("Regenerate storyboard", buttons)

    def test_generation_without_confirmation_reports_next_step(self):
        state, _ = PipelineTests().selection()
        expected = len(app.draft_package_storyboard(state, "Original"))
        state.update(confirmed=False, storyboard_approval={"old": True}, storyboard_preview_revision="old")
        for unconfirmed in (None, state):
            with self.subTest(empty=unconfirmed is None), patch("app.generate_storyboard") as generate, patch(
                "app.preview_package_storyboard"
            ) as preview:
                outputs = list(app.generate_package_storyboard(unconfirmed, "Original", "model", progress=Mock()))
                self.assertEqual(len(outputs), 1)
                self.assertEqual(len(outputs[0]), expected)
                self.assertIn("Review tab", outputs[0][1])
                self.assertIsNone(outputs[0][0]["storyboard_approval"])
                self.assertIsNone(outputs[0][0]["storyboard_preview_revision"])
                self.assertFalse(outputs[0][0]["confirmed"])
                self.assertIsNone(outputs[0][2])
                self.assertFalse(outputs[0][3])
                self.assertFalse(outputs[0][4]["interactive"])
                generate.assert_not_called()
                preview.assert_not_called()

    def test_automatic_generation_populates_fields_and_renders_preview(self):
        state, _ = PipelineTests().selection()
        generated = draft_storyboard(state["approved"], "Students")
        generated["opening"]["title"] = "Generated title"
        generated["ending"]["takeaway"] = "Generated synthesis"
        with patch("app.generate_storyboard", return_value=generated) as generator, patch(
            "app.preview_package_storyboard", return_value=(state, "Preview ready", "preview.mp4", False, {"interactive": False})
        ) as preview:
            outputs = list(app.generate_package_storyboard(state, "9:16", "selected-model", progress=Mock()))
        expected = len(app.draft_package_storyboard(state, "Original"))
        self.assertTrue(all(len(output) == expected for output in outputs))
        self.assertEqual(outputs[1][6], "Generated title")
        self.assertEqual(outputs[-1][2], "preview.mp4")
        self.assertEqual(generator.call_args.args[0], "selected-model")
        self.assertEqual(generator.call_args.args[1], state["approved"])
        self.assertEqual(preview.call_args.args[-1], "9:16")
        self.assertFalse(outputs[-1][3])

    def test_failed_generation_clears_stale_approval_and_does_not_preview(self):
        state, _ = PipelineTests().selection()
        state.update(storyboard_approval={"old": True}, storyboard_preview_revision="old")
        with patch("app.generate_storyboard", side_effect=RuntimeError("offline")), patch("app.preview_package_storyboard") as preview:
            outputs = list(app.generate_package_storyboard(state, "Original", "model", progress=Mock()))
        self.assertIn("offline", outputs[-1][1])
        self.assertIsNone(outputs[-1][0]["storyboard_approval"])
        self.assertIsNone(outputs[-1][0]["storyboard_draft"])
        preview.assert_not_called()

    def test_failed_auto_preview_keeps_generated_fields_without_approval(self):
        state, _ = PipelineTests().selection()
        generated = draft_storyboard(state["approved"], "Students")
        with patch("app.generate_storyboard", return_value=generated), patch(
            "app.preview_package_storyboard", side_effect=RuntimeError("render failed")
        ):
            outputs = list(app.generate_package_storyboard(state, "Original", "model", progress=Mock()))
        self.assertIn("preview failed", outputs[-1][1])
        self.assertEqual(outputs[-1][6], generated["opening"]["title"])
        self.assertIsNone(outputs[-1][0]["storyboard_approval"])
        self.assertIsNone(outputs[-1][2])

    def test_actual_export_duration_is_checked_before_publication(self):
        clips = self.generation_clips()
        draft = draft_storyboard(clips, "Students")
        draft["duration_budget"] = {"min_seconds": 45, "max_seconds": 120}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("pipeline.export.EXPORT_ROOT", root / "exports"), patch(
                "pipeline.export.media_duration", side_effect=[100, 120.1]
            ), patch("pipeline.export.cut_clip"), patch("pipeline.export.assemble_compilation"):
                with self.assertRaisesRegex(ValueError, "45-120"):
                    export_clips(run_id="budget", source_video="source.mp4", segments=[], clips=clips,
                                 source_url="", work=root / "work", cta_text="", storyboard=draft)
            self.assertEqual(list((root / "exports").glob("*/*")), [])

    def test_live_duration_counts_dividers_not_overlays(self):
        state, values, _ = self.package()
        self.assertIn("23.0s", app.storyboard_duration_status(state, *values))
        values[9] = "Overlay"
        self.assertIn("23.0s", app.storyboard_duration_status(state, *values))
        values[9] = "Divider"
        self.assertIn("24.5s", app.storyboard_duration_status(state, *values))

    def generated_response(self):
        return {"opening": {"title": "Examine advice critically", "subtitle": "Examples and limits", "evidence_ids": ["c0", "c1"]},
                "ending": {"takeaway": "Check assumptions before applying advice.", "question": "Where might the advice not apply?",
                           "seconds": 5, "evidence_ids": ["c0", "c1"]},
                "transitions": [{"before_clip_id": "c1", "mode": "Overlay", "label": "Another perspective", "evidence_ids": ["c0", "c1"]}]}

    def generation_clips(self):
        return [dict(clip, end=clip["start"] + 25) for clip in self.clips()[:2]]

    def test_generated_storyboard_uses_all_excerpts_and_audience(self):
        response = {"message": {"content": json.dumps(self.generated_response())}}
        with patch("pipeline.storyboard.ollama.chat", return_value=response) as chat:
            draft = generate_storyboard("model", self.generation_clips(), "Students")
        self.assertEqual(draft["generation"]["status"], "model_draft")
        self.assertEqual(draft["ending"]["evidence_ids"], ["c0", "c1"])
        payload = json.loads(chat.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(payload["audience"], "Students")
        self.assertEqual(len(payload["selected_clips_in_order"]), 2)
        validate_storyboard(draft, self.generation_clips())

    def test_generation_retries_invalid_references_but_never_silently_falls_back(self):
        invalid = self.generated_response()
        invalid["ending"]["evidence_ids"] = ["invented"]
        bad = {"message": {"content": json.dumps(invalid)}}
        good = {"message": {"content": json.dumps(self.generated_response())}}
        with patch("pipeline.storyboard.ollama.chat", side_effect=[bad, good]):
            self.assertEqual(generate_storyboard("model", self.generation_clips(), "Students")["generation"]["attempts"], 2)
        with patch("pipeline.storyboard.ollama.chat", return_value=bad) as chat:
            with self.assertRaisesRegex(ValueError, "two attempts"):
                generate_storyboard("model", self.generation_clips(), "Students")
            self.assertEqual(chat.call_count, 2)

    def test_generated_budget_counts_dividers_and_blocks_impossible_selection(self):
        with patch("pipeline.storyboard.ollama.chat") as chat:
            with self.assertRaisesRegex(ValueError, "cannot fit"):
                generate_storyboard("model", self.clips()[:2], "Students")
            chat.assert_not_called()
        clips = self.generation_clips()
        draft = draft_storyboard(clips, "Students")
        draft["duration_budget"] = {"min_seconds": 45, "max_seconds": 120}
        validate_storyboard(draft, clips)
        clips[1]["end"] += 66
        draft = draft_storyboard(clips, "Students")
        draft["duration_budget"] = {"min_seconds": 45, "max_seconds": 120}
        with self.assertRaisesRegex(ValueError, "45-120"):
            validate_storyboard(draft, clips)

    def test_one_or_two_clips_can_confirm_and_draft_but_zero_is_blocked(self):
        state, rows = PipelineTests().selection()
        rows[5 * app.MAX_REVIEW + 2] = False
        self.assertTrue(app.count_kept(state, True, True)[2]["interactive"])
        confirmed = app.confirm_selection(state, *rows)[0]
        self.assertEqual(len(confirmed["approved"]), 2)
        draft_result = app.draft_package_storyboard(confirmed, "Original")
        draft = draft_result[0]["storyboard_draft"]
        self.assertEqual(len(draft["transitions"]), 1)
        validate_storyboard(draft, confirmed["approved"])
        rows[5 * app.MAX_REVIEW + 1] = False
        confirmed = app.confirm_selection(state, *rows)[0]
        single = app.draft_package_storyboard(confirmed, "Original")
        self.assertEqual(single[0]["storyboard_draft"]["transitions"], [])
        self.assertTrue(all(not update["visible"] for update in single[-(app.MAX_KEEP - 1):]))
        self.assertTrue(app.count_kept(state, True)[2]["interactive"])
        rows[5 * app.MAX_REVIEW] = False
        with self.assertRaises(Exception):
            app.confirm_selection(state, *rows)
        self.assertFalse(app.count_kept(state, False)[2]["interactive"])

    def test_exporter_accepts_one_or_two_and_rejects_invalid_counts(self):
        state, _ = PipelineTests().selection()

        def assemble(raw, dest, **kwargs):
            dest.write_bytes(b"test video")
            kwargs["srt_path"].write_text("captions")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = dict(run_id="two-clips", source_video="source.mp4", segments=[],
                           source_url="", work=root / "work", cta_text="Source")
            with patch("pipeline.export.EXPORT_ROOT", root / "exports"), patch(
                "pipeline.export.media_duration", return_value=40
            ), patch("pipeline.export.cut_clip"), patch("pipeline.export.assemble_compilation", side_effect=assemble):
                for count in (1, 2):
                    result = export_clips(clips=state["approved"][:count], **options)
                    self.assertTrue(Path(result[3]).is_file())
                for clips in ([], state["approved"] * 2):
                    with self.assertRaisesRegex(RuntimeError, "Keep 1"):
                        export_clips(clips=clips, **options)

    def test_single_clip_generation_has_no_transitions_and_keeps_duration_budget(self):
        clips = [dict(self.clips()[0], end=40)]
        response = self.generated_response()
        response["transitions"] = []
        for section in ("opening", "ending"):
            response[section]["evidence_ids"] = ["c0"]
        with patch("pipeline.storyboard.ollama.chat", return_value={"message": {"content": json.dumps(response)}}) as chat:
            draft = generate_storyboard("model", clips, "Students")
        self.assertEqual(draft["transitions"], [])
        transition_schema = chat.call_args.kwargs["format"]["properties"]["transitions"]
        self.assertEqual(transition_schema["maxItems"], 0)
        self.assertNotIn('"enum": []', json.dumps(chat.call_args.kwargs["format"]))
        timeline = storyboard_timeline(clips, [40], draft)
        self.assertEqual([item["kind"] for item in timeline], ["opening", "clip", "ending"])
        self.assertEqual(timeline[-1]["start"] + timeline[-1]["duration"], 48)
        with patch("pipeline.storyboard.ollama.chat") as chat:
            with self.assertRaisesRegex(ValueError, "cannot fit"):
                generate_storyboard("model", [dict(clips[0], end=30)], "Students")
            chat.assert_not_called()

    def package(self):
        state, rows = PipelineTests().selection()
        result = app.draft_package_storyboard(state, "Original")
        values = [*result[6:-(app.MAX_KEEP - 1)], "Original"]
        return result[0], values, rows

    def test_approval_requires_preview_and_named_attestation(self):
        state, values, _ = self.package()
        with self.assertRaisesRegex(Exception, "Preview"):
            app.approve_package_storyboard(state, True, "Test reviewer", *values)
        _, revision = app._read_storyboard(state, values)
        state["storyboard_preview_revision"] = revision
        for attested, reviewer in [(False, "Test"), (True, "")]:
            with self.assertRaisesRegex(Exception, "named human"):
                app.approve_package_storyboard(state, attested, reviewer, *values)
        approved = app.approve_package_storyboard(state, True, "Test reviewer", *values)[0]
        self.assertEqual(approved["storyboard_approval"]["revision"], revision)
        revoked = app.revoke_storyboard_approval(approved)[0]
        self.assertIsNone(revoked["storyboard_approval"])
        self.assertEqual(revoked["storyboard_preview_revision"], revision)

    def test_storyboard_export_blocks_stale_controls_and_live_rows(self):
        state, values, rows = self.package()
        _, revision = app._read_storyboard(state, values)
        state["storyboard_preview_revision"] = revision
        state = app.approve_package_storyboard(state, True, "Test reviewer", *values)[0]
        with patch("app.export_clips", return_value=([], "Post", Path("bundle.zip"), "movie.mp4")) as exporter:
            app.export_package_storyboard(state, True, "Test reviewer", *values, *rows, progress=Mock())
            self.assertEqual(exporter.call_args.kwargs["storyboard_approval"]["revision"], revision)
            self.assertEqual(exporter.call_args.kwargs["storyboard"]["opening"]["title"], values[0])
            for index, replacement in [(0, "Changed title"), (2, 2.5), (-1, "9:16"), (-2, "Middle")]:
                edited = list(values)
                edited[index] = replacement
                with self.assertRaisesRegex(Exception, "changed|approved"):
                    app.export_package_storyboard(state, True, "Test reviewer", *edited, *rows, progress=Mock())
            rows[4 * app.MAX_REVIEW] = 6.0
            with self.assertRaisesRegex(Exception, "changed"):
                app.export_package_storyboard(state, True, "Test reviewer", *values, *rows, progress=Mock())
            self.assertEqual(exporter.call_count, 1)

    def test_preview_cache_and_ui_output_shape(self):
        state, values, _ = self.package()
        with tempfile.TemporaryDirectory() as directory:
            state["work"] = directory

            def render(raw, dest, **kwargs):
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(b"preview")

            with patch("app.cut_clip", return_value=Path("raw.mp4")), patch("app.assemble_compilation", side_effect=render) as assemble:
                result = app.preview_package_storyboard(state, *values, progress=Mock())
                self.assertEqual(len(result), 5)
                self.assertTrue(Path(result[2]).exists())
                self.assertTrue(assemble.call_args.kwargs["preview"])
                app.preview_package_storyboard(result[0], *values, progress=Mock())
                self.assertEqual(assemble.call_count, 1)
            changed = app.invalidate_package_storyboard(result[0])
            self.assertIsNone(changed[0]["storyboard_preview_revision"])
            self.assertIsNone(changed[2])
        demo = app.build_app()
        labels = [component.get("props", {}).get("label") for component in demo.config["components"]]
        self.assertIn("Storyboard reviewer name", labels)
        self.assertIn("Storyboard source evidence", labels)

    def clips(self):
        return [{"id": f"c{index}", "start": index * 10, "end": index * 10 + 8,
                 "title": "Question assumptions", "takeaway": "Examine the assumptions behind advice.",
                 "excerpt": "Examine the assumptions behind advice."} for index in range(3)]

    def test_draft_has_source_evidence_and_neutral_joins(self):
        clips = self.clips()
        draft = draft_storyboard(clips, "Beginners")
        validate_storyboard(draft, clips)
        self.assertEqual(draft["evidence"][0]["quote"], clips[0]["excerpt"])
        self.assertTrue(all(item["mode"] == "Cut" for item in draft["transitions"]))
        self.assertIn("own words", draft["ending"]["question"])

    def test_revision_binds_text_timing_selection_and_aspect(self):
        draft = draft_storyboard(self.clips(), "Beginners")
        original = storyboard_revision("selection-a", draft)
        for section, field, value in [("opening", "title", "Changed"), ("ending", "seconds", 6)]:
            edited = copy.deepcopy(draft)
            edited[section][field] = value
            self.assertNotEqual(original, storyboard_revision("selection-a", edited))
        self.assertNotEqual(original, storyboard_revision("selection-b", draft))
        draft["aspect_ratio"] = "9:16"
        self.assertNotEqual(original, storyboard_revision("selection-a", draft))

    def test_timeline_inserts_only_dividers(self):
        clips = self.clips()
        draft = draft_storyboard(clips, "Beginners")
        draft["transitions"][0].update(mode="Divider", label="Another example")
        draft["transitions"][1].update(mode="Overlay", label="A limitation")
        timeline = storyboard_timeline(clips, [8, 8, 8], draft)
        self.assertEqual([item["start"] for item in timeline if item["kind"] == "clip"], [3, 12.5, 20.5])
        self.assertEqual(timeline[-1]["start"], 28.5)

    def test_dense_text_stale_evidence_and_invalid_times_rejected(self):
        clips = self.clips()
        for mutate in [lambda draft: draft["ending"].update(takeaway="word " * 17),
                       lambda draft: draft["opening"].update(seconds=float("nan")),
                       lambda draft: draft["evidence"][0].update(quote="invented")]:
            draft = draft_storyboard(clips, "Beginners")
            mutate(draft)
            with self.assertRaises(ValueError):
                validate_storyboard(draft, clips)


class AudienceInputTests(unittest.TestCase):
    def test_transcription_needs_no_user_topic_or_objectives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "lecture.mp4"
            source.write_bytes(b"test source")
            work = root / "run"
            work.mkdir()
            transcript = {"language": "en", "segments": [{"start": 0, "end": 20, "text": "Source lecture."}]}
            with patch("app.ffmpeg_bin"), patch("app.run_dir", return_value=work), patch(
                "app.transcribe_video", return_value=transcript
            ), patch("app.media_duration", return_value=30):
                result = app.transcribe_for_audience(str(source), "Beginners", "", 20, 45,
                                                     "Original", "test-model", None, progress=Mock())
            self.assertEqual(result[0]["target_audience"], "Beginners")
            self.assertEqual(result[0]["objectives"], app.AUDIENCE_GOALS["Beginners"])
            self.assertEqual(result[0]["transcript"], transcript)
            self.assertEqual(len(result), 8 + 9 * app.MAX_REVIEW)

    def test_audience_change_preserves_transcript_and_clears_review(self):
        state = app.empty_state()
        state.update(transcript={"segments": [{"text": "Speech"}]}, candidates=[{"id": "c1"}],
                     approved=[{"id": "c1"}], confirmed=True, approval_revision="old")
        result = app.change_target_audience("Professionals", state)
        updated = result[0]
        self.assertEqual(updated["transcript"], state["transcript"])
        self.assertEqual(updated["objectives"], app.AUDIENCE_GOALS["Professionals"])
        self.assertEqual(updated["candidates"], [])
        self.assertEqual(updated["approved"], [])
        self.assertFalse(updated["confirmed"])
        self.assertIsNone(updated["approval_revision"])
        self.assertTrue(state["confirmed"])
        self.assertEqual(len(result), 6 + 9 * app.MAX_REVIEW)

    def test_profiles_are_valid_and_unknown_audiences_rejected(self):
        for audience in app.AUDIENCE_GOALS:
            topic, goals = app.audience_settings(audience)
            self.assertTrue(topic)
            self.assertEqual(len(goals), 3)
            self.assertIsNot(goals, app.AUDIENCE_GOALS[audience])
        with self.assertRaisesRegex(Exception, "target audience"):
            app.audience_settings("Unknown")

    def test_audience_is_part_of_selection_approval(self):
        state = app.empty_state()
        original = selection_revision(state, [])
        state["target_audience"] = "Professionals"
        self.assertNotEqual(selection_revision(state, []), original)

    def test_rerun_uses_current_duration_settings_without_transcription(self):
        with patch("app.nominate_and_rank", return_value="ranked") as nominate:
            self.assertEqual(app.nominate_for_audience(app.empty_state(), "model", "Professionals", 25, 60), "ranked")
        state = nominate.call_args.args[0]
        self.assertEqual((state["min_s"], state["max_s"]), (25, 60))
        self.assertEqual(state["objectives"], app.AUDIENCE_GOALS["Professionals"])
        for minimum, maximum in [(50, 20), (0, 30), (20, float("nan"))]:
            with self.assertRaises(Exception):
                app.nominate_for_audience({}, "model", "Beginners", minimum, maximum)
            state = app.empty_state()
            state.update(confirmed=True, approval_revision="old", transcript={"segments": []})
            changed = app.change_duration_limits(25, 60, "Beginners", state)[0]
            self.assertFalse(changed["confirmed"])
            self.assertIsNone(changed["approval_revision"])
            self.assertEqual(changed["transcript"], state["transcript"])

    def test_audience_goals_reach_nomination_and_ranking(self):
        state = app.empty_state()
        state.update(transcript={"segments": [{"start": 0, "end": 20, "text": "Lecture content"}]},
                     source_video="source.mp4", work="work")
        state = app.change_target_audience("Advanced learners", state)[0]
        with patch("app.list_ollama_models", return_value=(["test-model"], "ready")), patch(
            "app.media_duration", return_value=30
        ), patch("app.nominate_candidates", return_value=[]) as nominate, patch(
            "app.rank_candidates", return_value=[]
        ) as rank, patch("app.infer_lecture_context", return_value={"status": "draft", "concepts": []}):
            app.nominate_and_rank(state, "test-model", progress=Mock())
        self.assertEqual(nominate.call_args.kwargs["objectives"], app.AUDIENCE_GOALS["Advanced learners"])
        self.assertIn("advanced learners", nominate.call_args.kwargs["topic"])
        self.assertEqual(rank.call_args.args[1], app.AUDIENCE_GOALS["Advanced learners"])
        self.assertEqual(rank.call_args.kwargs["target_audience"], "Advanced learners")
        self.assertEqual(nominate.call_args.kwargs["target_audience"], "Advanced learners")


class EditorialTests(unittest.TestCase):
    def segments(self):
        return [{"start": 0, "end": 10, "text": "A problem begins,"},
                {"start": 10, "end": 20, "text": "and the solution follows."},
                {"start": 20, "end": 30, "text": "There is a caveat."}]

    def response(self, start=0, end=20):
        return {"suggested_range": {"start": start, "end": end}, "context_preserved": True,
                "standalone": True, "reason": "Complete explanation.",
                "ratings": {key: {"score": 3, "reason": "Explained with context.", "start": 10, "end": 20,
                                  "quote": "the solution follows."}
                            for key in ("audience_fit", "completeness", "educational_value")}}

    def review(self):
        return review_candidate("model", {"id": "c1", "start": 0, "end": 20}, self.segments(),
                                "Beginners", ["Explain basics"], {}, 10, 30, 30)

    def test_boundaries_extend_without_exceeding_budget(self):
        result = repair_sentence_boundaries({"start": 0, "end": 10}, self.segments(), 10, 30, 30)
        self.assertEqual((result["start"], result["end"]), (0, 20))
        result = repair_sentence_boundaries({"start": 10, "end": 20}, self.segments(), 10, 30, 30)
        self.assertEqual(result["start"], 0)
        with self.assertRaisesRegex(ValueError, "budget"):
            repair_sentence_boundaries({"start": 0, "end": 10}, self.segments(), 10, 15, 30)

    def test_weighted_review_requires_source_evidence(self):
        with patch("pipeline.editorial._ask", return_value=self.response()) as ask:
            result = self.review()
        self.assertEqual(result["score"], 75)
        self.assertEqual(result["editorial_status"], "estimated")
        self.assertEqual(ask.call_args.args[2]["audience"], "Beginners")
        bad = self.response()
        bad["ratings"]["audience_fit"]["quote"] = "Invented claim"
        with patch("pipeline.editorial._ask", return_value=bad):
            result = self.review()
        self.assertIsNone(result["score"])
        self.assertEqual(result["editorial_status"], "unavailable")

    def test_context_gate_overrides_high_scores(self):
        response = self.response()
        response["context_preserved"] = False
        with patch("pipeline.editorial._ask", return_value=response):
            result = self.review()
        self.assertEqual(result["editorial_status"], "rejected")
        self.assertIsNone(result["score"])

    def test_contradictory_model_judgment_is_unavailable(self):
        response = self.response()
        response["standalone"] = False
        with patch("pipeline.editorial._ask", return_value=response):
            result = self.review()
        self.assertEqual(result["editorial_status"], "unavailable")
        self.assertIsNone(result["score"])

    def test_semantic_extension_is_reviewed_again(self):
        with patch("pipeline.editorial._ask", side_effect=[self.response(0, 30), self.response(0, 30)]) as ask:
            result = self.review()
        self.assertEqual(result["end"], 30)
        self.assertEqual(result["score"], 75)
        self.assertEqual(ask.call_count, 2)
        self.assertFalse(ask.call_args.args[2]["extension_allowed"])

    def test_missing_service_does_not_fabricate_a_score(self):
        with patch("pipeline.editorial._ask", side_effect=RuntimeError("offline")):
            self.assertIsNone(self.review()["score"])

    def test_invalid_model_extensions_never_receive_scores(self):
        for start, end in [(1, 20), (-1, 20), (0, 31), (0, 25), (0, float("nan"))]:
            with self.subTest(start=start, end=end), patch("pipeline.editorial._ask", return_value=self.response(start, end)):
                self.assertIsNone(self.review()["score"])

    def test_shortlist_preserves_unscored_status_in_ui(self):
        candidate = {"id": "c1", "start": 0, "end": 20, "editorial_version": "1.0",
                     "score": None, "editorial_status": "unavailable"}
        self.assertIn("Not scored", app._row_from_candidate(candidate)[5])
        self.assertIn("unavailable", app.invalidate_range_score())

    def test_metadata_is_generated_after_editorial_boundary_extension(self):
        candidate = {"id": "c1", "start": 0, "end": 20}
        response = {"message": {"content": json.dumps({"candidates": [candidate] * 3})}}
        reviewed = {**candidate, "end": 30, "editorial_status": "estimated", "score": 75}
        with patch("pipeline.nominate.ollama.chat", return_value=response), patch(
            "pipeline.editorial.review_candidate", return_value=reviewed
        ), patch("pipeline.nominate._describe_candidate", side_effect=lambda model, item, segments: item) as describe:
            result = nominate_candidates("model", "Topic", ["Goal"], self.segments(), 10, 30, 30,
                                         target_audience="Beginners", lecture_context={})
        self.assertTrue(result)
        self.assertTrue(all(call.args[1]["end"] == 30 for call in describe.call_args_list))

    def test_context_concepts_require_timestamped_quotes(self):
        response = {"concepts": [{"label": "Problem solving", "start": 0, "end": 10, "quote": "A problem begins,"}]}
        with patch("pipeline.editorial._ask", return_value=response):
            self.assertEqual(infer_lecture_context("model", self.segments())["status"], "draft")
        response["concepts"][0]["quote"] = "Imaginary concept"
        with patch("pipeline.editorial._ask", return_value=response):
            result = infer_lecture_context("model", self.segments())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["concepts"], [])

    def test_segment_ids_resolve_timestamps_and_preserve_valid_concepts(self):
        response = {"concepts": [{"label": "Problem", "segment_id": 0, "quote": "A problem begins,"},
                                 {"label": "Invented", "segment_id": 99, "quote": "not in the lecture"}]}
        with patch("pipeline.editorial._ask", return_value=response):
            result = infer_lecture_context("model", self.segments())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["concepts"]), 1)
        self.assertEqual((result["concepts"][0]["start"], result["concepts"][0]["end"]), (0, 10))
        response = self.response()
        for rating in response["ratings"].values():
            rating.pop("start")
            rating.pop("end")
            rating["segment_id"] = 1
        with patch("pipeline.editorial._ask", return_value=response):
            result = self.review()
        self.assertEqual(result["score"], 75)
        self.assertEqual(result["editorial_ratings"]["audience_fit"]["evidence"]["start"], 10)
        response["ratings"]["audience_fit"]["segment_id"] = 2
        with patch("pipeline.editorial._ask", return_value=response):
            self.assertIsNone(self.review()["score"])

    def test_segment_references_attach_source_text_not_generated_evidence(self):
        response = self.response()
        for rating in response["ratings"].values():
            rating.pop("start")
            rating.pop("end")
            rating.pop("quote")
            rating["segment_id"] = 1
        with patch("pipeline.editorial._ask", return_value=response):
            result = self.review()
        self.assertEqual(result["score"], 75)
        self.assertEqual(result["editorial_ratings"]["audience_fit"]["evidence"]["quote"], "and the solution follows.")

    def test_editorial_ranking_filters_rejections_and_overlap(self):
        candidates = [{"id": "best", "start": 0, "end": 20, "score": 90, "editorial_status": "estimated"},
                      {"id": "overlap", "start": 0, "end": 20, "score": 80, "editorial_status": "estimated"},
                      {"id": "rejected", "start": 20, "end": 30, "score": 100, "editorial_status": "rejected"},
                      {"id": "unknown", "start": 20, "end": 30, "score": 99, "editorial_status": "unavailable"}]
        with patch("pipeline.rank.get_embedder") as embedder:
            ranked = rank_candidates(candidates, [], self.segments(), target_audience="Beginners")
        self.assertEqual([candidate["id"] for candidate in ranked], ["best", "unknown"])
        self.assertIsNone(ranked[1]["score"])
        embedder.assert_not_called()


class PipelineTests(unittest.TestCase):
    def test_metadata_uses_only_final_excerpt(self):
        candidate = {"id": "c1", "start": 20, "end": 40, "title": "Invented Nike claim",
                     "takeaway": "Old claim", "quote": "Old quote", "source": "window"}
        segments = [{"start": 0, "end": 20, "text": "Outside before"},
                    {"start": 20, "end": 40, "text": "We focus on problems."},
                    {"start": 40, "end": 60, "text": "Outside after"}]
        metadata = {"title": "Problem-first thinking", "takeaway": "The speaker focuses on problems.",
                    "quote": "We focus on problems."}
        response = SimpleNamespace(message=SimpleNamespace(content=json.dumps(metadata)))
        with patch("pipeline.nominate.ollama.chat", return_value=response) as chat:
            result = _describe_candidate("test-model", candidate, segments)
        self.assertEqual(json.loads(chat.call_args.kwargs["messages"][1]["content"]),
                         {"excerpt": "We focus on problems."})
        self.assertEqual(result["title"], metadata["title"])
        self.assertEqual(result["metadata_status"], "draft")
        self.assertEqual(result["source"], "window")
        self.assertEqual((result["id"], result["start"], result["end"]), ("c1", 20, 40))
        self.assertEqual(candidate["title"], "Invented Nike claim")

    def test_metadata_failure_does_not_retain_unverified_claims(self):
        candidate = {"id": "c1", "start": 0, "end": 20, "title": "Invented claim",
                     "takeaway": "Invented takeaway", "quote": "Invented quote"}
        segments = [{"start": 0, "end": 20, "text": "Actual speech."}]
        invalid = ["not json", "[]", '{"title": "Only title"}',
                   json.dumps({"title": "Title", "takeaway": "Claim", "quote": "Outside speech"}),
                   json.dumps({"title": 123, "takeaway": "Claim", "quote": "Actual speech."}),
                   json.dumps({"title": "Title", "takeaway": " ", "quote": "Actual speech."})]
        for content in invalid:
            with self.subTest(content=content), patch("pipeline.nominate.ollama.chat", return_value={"message": {"content": content}}):
                result = _describe_candidate("test-model", candidate, segments)
                self.assertEqual(result["metadata_status"], "unavailable")
                self.assertEqual(result["title"], "Clip at 0.000s")
                self.assertEqual((result["takeaway"], result["quote"]), ("", ""))
        with patch("pipeline.nominate.ollama.chat", side_effect=RuntimeError("Offline")):
            self.assertEqual(_describe_candidate("test-model", candidate, segments)["metadata_status"], "unavailable")
        with patch("pipeline.nominate.ollama.chat") as chat:
            self.assertEqual(_describe_candidate("test-model", candidate, [])["metadata_status"], "unavailable")
            chat.assert_not_called()

    def test_nomination_describes_only_normalized_candidates(self):
        segments = [{"start": start, "end": start + 20, "text": f"Speech at {start}"}
                    for start in (0, 20, 40)]
        raw = [{"start": 1, "end": 19}, {"start": 20, "end": 40},
               {"start": 40, "end": 60}, {"start": 0, "end": 60}]
        response = {"message": {"content": json.dumps({"candidates": raw})}}
        with patch("pipeline.nominate.ollama.chat", return_value=response), patch(
            "pipeline.nominate._describe_candidate", side_effect=lambda model, candidate, segments: candidate
        ) as describe:
            result = nominate_candidates("test-model", "Topic", ["Goal"], segments, 20, 45, 60)
        self.assertEqual(len(result), 3)
        self.assertEqual(describe.call_count, 3)
        self.assertEqual((result[0]["start"], result[0]["end"]), (0, 20))

    def test_ranking_uses_only_selected_speech(self):
        embedder = Mock()
        similarity = Mock()
        similarity.max.return_value.item.return_value = 0.4
        fake_transformers = SimpleNamespace(util=SimpleNamespace(cos_sim=Mock(return_value=similarity)))
        candidate = {"id": "c1", "start": 0, "end": 10, "title": "Invented claim", "quote": "Invented quote"}
        segments = [{"start": 0, "end": 10, "text": "Actual source speech"}]
        with patch("pipeline.rank.get_embedder", return_value=embedder), patch.dict(sys.modules, {"sentence_transformers": fake_transformers}):
            rank_candidates([candidate], ["Goal"], segments)
        self.assertEqual(embedder.encode.call_args_list[1].args[0], "Actual source speech")

    def test_window_provenance_survives_normalization(self):
        segments = [{"start": 0, "end": 20, "text": "Actual speech"}]
        result = _normalize_candidates([{"start": 0, "end": 20, "source": "window"}], segments, 10, 30, 20)
        self.assertEqual(result[0]["source"], "window")

    def test_normalization_rejects_baseline_duration_violations(self):
        for start, end in [(247.388, 261.232), (644.394, 693.644), (469.024, 518.692)]:
            for source in ("ollama", "window"):
                with self.subTest(start=start, end=end, source=source):
                    segments = [{"start": start, "end": end, "text": "Actual speech"}]
                    result = _normalize_candidates(
                        [{"start": start, "end": end, "source": source}], segments, 20, 45, 919.574
                    )
                    self.assertEqual(result, [])

    def test_normalization_checks_duration_after_snapping(self):
        segments = [{"start": 0, "end": 50, "text": "One complete segment"}]
        result = _normalize_candidates([{"start": 5, "end": 40}], segments, 20, 45, 60)
        self.assertEqual(result, [])

    def test_normalization_preserves_exact_duration_boundaries(self):
        for start, end in [(0, 20), (30, 75), (1.001, 21.001), (1.001, 46.001)]:
            with self.subTest(start=start, end=end):
                segments = [{"start": start, "end": end, "text": "Actual speech"}]
                result = _normalize_candidates([{"start": start, "end": end}], segments, 20, 45, 100)
                self.assertEqual(len(result), 1)
                self.assertEqual((result[0]["start"], result[0]["end"]), (start, end))

    def test_normalization_checks_serialized_ranges(self):
        for start, end, duration in [(0.00049, 45.00051, 60), (0, 20.0006, 20.0006)]:
            with self.subTest(start=start, end=end, duration=duration):
                result = _normalize_candidates([{"start": start, "end": end}], [], 20, 45, duration)
                self.assertEqual(result, [])

    def selection(self):
        clips = [
            {"id": f"c{index}", "start": float(index * 10), "end": float(index * 10 + 5),
             "title": f"Title {index}", "takeaway": "Point", "excerpt": "Speech"}
            for index in range(3)
        ]
        state = app.empty_state()
        state.update({"candidates": clips, "approved": copy.deepcopy(clips), "confirmed": True,
                      "run_id": "test-run", "source_video": "source.mp4", "work": "work",
                      "source_duration": 40, "transcript": {"segments": [
                          {"start": clip["start"], "end": clip["end"], "text": "Speech"} for clip in clips]}})
        state["approval_revision"] = selection_revision(state, clips)
        rows = []
        for field in ("id", "title", "takeaway", "start", "end", "keep"):
            rows.extend([True if field == "keep" else clip[field] for clip in clips])
            rows.extend([False if field == "keep" else None] * (app.MAX_REVIEW - len(clips)))
        return state, rows

    def test_unchanged_approved_selection_exports(self):
        state, rows = self.selection()
        with patch("app.export_clips", return_value=([], "Post", Path("bundle.zip"), "movie.mp4")) as exporter:
            result = app.do_export(state, "CTA", "Title", "", 1.5, "Point", 3, "Original", *rows, progress=Mock())
        self.assertEqual(result[2], "movie.mp4")
        self.assertEqual(exporter.call_args.kwargs["clips"], state["approved"])

    def test_range_edit_clears_old_metadata_evidence(self):
        state, _ = self.selection()
        state["candidates"][0].update(quote="Old supporting quote", metadata_status="draft", score=0.9)
        result = app._read_rows(["c0"], ["Edited title"], ["Edited takeaway"], [10], [15], [True], state)
        self.assertEqual(result[0]["quote"], "")
        self.assertEqual(result[0]["metadata_status"], "range_edited")
        self.assertEqual(result[0]["excerpt"], "Speech")
        self.assertNotIn("score", result[0])
        self.assertEqual(state["candidates"][0]["quote"], "Old supporting quote")

    def test_editorial_range_edit_clears_score_evidence_and_checks_duration(self):
        state, _ = self.selection()
        state.update(min_s=5, max_s=10)
        state["candidates"][0].update(editorial_version="1.0", editorial_ratings={"old": {}},
                                      context_preserved=True, standalone=True, score=90)
        result = app._read_rows(["c0"], ["Title"], ["Point"], [1], [6], [True], state)[0]
        self.assertNotIn("score", result)
        self.assertNotIn("editorial_ratings", result)
        self.assertNotIn("context_preserved", result)
        self.assertEqual(result["editorial_status"], "range_edited")
        with self.assertRaisesRegex(Exception, "duration limits"):
            app._read_rows(["c0"], ["Title"], ["Point"], [0], [20], [True], state)

    def test_changed_source_invalidates_approval(self):
        state, rows = self.selection()
        state["source_video"] = "different.mp4"
        with self.assertRaisesRegex(Exception, "[Cc]onfirm|changed"):
            app.do_export(state, "CTA", "Title", "", 1.5, "Point", 3, "Original", *rows)

    def test_export_rejects_changed_approved_selection(self):
        state, rows = self.selection()
        rows[4 * app.MAX_REVIEW] = 6.0
        with patch("app.export_clips") as exporter:
            with self.assertRaisesRegex(Exception, "[Cc]onfirm|changed"):
                app.do_export(state, "CTA", "Title", "", 1.5, "Point", 3, "Original", *rows)
            exporter.assert_not_called()

    def test_export_cannot_approve_unconfirmed_rows(self):
        state, rows = self.selection()
        state["confirmed"] = False
        with self.assertRaisesRegex(Exception, "[Cc]onfirm"):
            app.do_export(state, "CTA", "Title", "", 1.5, "Point", 3, "Original", *rows)

    def test_unknown_candidate_is_rejected(self):
        state, _ = self.selection()
        with self.assertRaisesRegex(Exception, "candidate|[Nn]ominate"):
            app._read_rows(["stale"], ["Title"], ["Point"], [0], [5], [True], state)

    def test_invalid_edited_ranges_are_rejected(self):
        state, _ = self.selection()
        for start, end in [(-1, 5), (0, 41), (0, float("inf")), (float("nan"), 5)]:
            with self.subTest(start=start, end=end), self.assertRaises(Exception):
                app._read_rows(["c0"], ["Title"], ["Point"], [start], [end], [True], state)

    def test_successful_and_failed_exports_preserve_previous_files(self):
        state, _ = self.selection()

        def assemble(raw_clips, dest, **kwargs):
            dest.write_bytes(b"test video")
            kwargs["srt_path"].write_text("captions", encoding="utf-8")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            previous = root / "test-run" / "compilation.mp4"
            previous.parent.mkdir()
            previous.write_bytes(b"previous export")
            options = dict(run_id="test-run", source_video="source.mp4", segments=[],
                           clips=state["approved"], source_url="", work=root / "work", cta_text="CTA",
                           selection_context={"target_audience": "Beginners", "audience_profile_version": "1.0"})
            with patch("pipeline.export.EXPORT_ROOT", root), patch("pipeline.export.media_duration", return_value=40), patch("pipeline.export.cut_clip"), patch("pipeline.export.assemble_compilation", side_effect=assemble):
                first = export_clips(**options)
                second = export_clips(**options)
                self.assertNotEqual(first[3], second[3])
                self.assertTrue(first[2].exists())
                self.assertTrue(second[2].exists())
                selection = json.loads(Path(first[3]).with_name("selection.json").read_text())
                self.assertEqual(selection["selection_context"]["target_audience"], "Beginners")
                with patch("pipeline.export.assemble_compilation", side_effect=RuntimeError("render failed")):
                    with self.assertRaisesRegex(RuntimeError, "render failed"):
                        export_clips(**options)
                self.assertEqual(Path(first[3]).read_bytes(), b"test video")
                self.assertEqual(previous.read_bytes(), b"previous export")
                self.assertEqual(list(previous.parent.glob(".pending-*")), [])


class AssessmentTests(unittest.TestCase):
    def review(self):
        return {
            "reviewer": {"kind": "human", "name": "Test reviewer"},
            "ratings": {key: {"score": 3, "evidence": ["Rendered video 00:01-00:05 checked against source."]}
                        for key in DIMENSIONS},
            "gates": {key: {"status": "pass", "evidence": "Reviewed the final artifact."} for key in GATES},
        }

    def test_complete_assessment_uses_weights(self):
        review = self.review()
        review["ratings"]["source_fidelity"]["score"] = 4
        result = score_review(review)
        self.assertEqual(result["overall_score"], 81.25)
        self.assertEqual(result["assessed_weight_percent"], 100)
        self.assertEqual(result["publication_status"], "approved")

    def test_missing_rating_does_not_become_zero_or_normalized_score(self):
        review = self.review()
        del review["ratings"]["captions"]
        result = score_review(review)
        self.assertIsNone(result["overall_score"])
        self.assertEqual(result["earned_points"], 63.75)
        self.assertEqual(result["assessed_weight_percent"], 85)
        self.assertEqual(result["publication_status"], "blocked")

    def test_high_total_cannot_override_failed_or_unassessed_gate(self):
        for status in ("fail", "not_assessed"):
            with self.subTest(status=status):
                review = self.review()
                for rating in review["ratings"].values():
                    rating["score"] = 4
                review["gates"]["cuts_preserve_context"]["status"] = status
                result = score_review(review)
                self.assertEqual(result["overall_score"], 100)
                self.assertEqual(result["publication_status"], "blocked")

    def test_weak_dimension_blocks_publication(self):
        review = self.review()
        review["ratings"]["learning_value"]["score"] = 2
        self.assertEqual(score_review(review)["publication_status"], "blocked")

    def test_assistant_cannot_claim_human_review(self):
        review = self.review()
        review["reviewer"]["kind"] = "assistant"
        with self.assertRaisesRegex(ValueError, "human final review"):
            score_review(review)

    def test_invalid_or_unsupported_ratings_are_rejected(self):
        for score in (-1, 5, True, 2.5, float("nan"), "3"):
            with self.subTest(score=score):
                review = self.review()
                review["ratings"]["captions"]["score"] = score
                with self.assertRaises(ValueError):
                    score_review(review)
        review = self.review()
        review["ratings"]["captions"]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "requires evidence"):
            score_review(review)

    def test_assessments_preserve_exports_and_reject_stale_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            export_dir = Path(directory)
            for name in ARTIFACTS:
                (export_dir / name).write_bytes(b"{}" if name == "selection.json" else b"original")
            review = self.review()
            review["artifact_sha256"] = artifact_hashes(export_dir)
            first = assess_export(export_dir, review)
            second = assess_export(export_dir, review)
            self.assertNotEqual(first, second)
            self.assertTrue((first / "assessment.md").is_file())
            self.assertEqual((export_dir / "compilation.mp4").read_bytes(), b"original")
            (export_dir / "post.txt").write_bytes(b"changed claim")
            with self.assertRaisesRegex(ValueError, "stale"):
                assess_export(export_dir, review)

    def test_template_is_unassessed_and_bound_to_current_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            export_dir = Path(directory)
            for name in ARTIFACTS:
                (export_dir / name).write_bytes(b"{}" if name == "selection.json" else b"original")
            review = review_template(export_dir)
            self.assertEqual(review["artifact_sha256"], artifact_hashes(export_dir))
            review["reviewer"]["name"] = "Reviewer"
            result = score_review(review)
            self.assertIsNone(result["overall_score"])
            self.assertEqual(result["assessed_weight_percent"], 0)
            self.assertEqual(result["publication_status"], "blocked")

    def test_ui_load_save_and_stale_review(self):
        with tempfile.TemporaryDirectory() as directory, patch("app.EXPORT_ROOT", Path(directory)):
            export_dir = Path(directory) / "run" / "export"
            export_dir.mkdir(parents=True)
            for name in ARTIFACTS:
                (export_dir / name).write_text("{}" if name == "selection.json" else "original")
            loaded = app.load_assessment_export("run/export")
            self.assertIn("Not assessed.", loaded[3])
            self.assertIn("Unknown (legacy export)", loaded[3])
            fields = [*(["3"] * 5), *(["Output 00:01-00:05 reviewed."] * 5),
                      *(["not_assessed"] * 4), *([""] * 4)]
            summary, files = app.save_ui_assessment(loaded[0], "run/export", "Reviewer", *fields)
            self.assertIn("75.00/100", summary)
            self.assertIn("**blocked**", summary)
            self.assertTrue(all(Path(path).is_file() for path in files))
            reloaded = app.load_assessment_export("run/export")
            self.assertIn("75.00/100", reloaded[3])
            self.assertEqual(reloaded[5], "")
            self.assertEqual(reloaded[6:11], ("not_assessed",) * 5)
            (export_dir / "post.txt").write_text("changed claim")
            with self.assertRaisesRegex(Exception, "stale"):
                app.save_ui_assessment(loaded[0], "run/export", "Reviewer", *fields)
            self.assertIn("stale", app.load_assessment_export("run/export")[3])

    def test_ui_rejects_missing_context_and_evidence(self):
        with self.assertRaisesRegex(Exception, "Select an exported"):
            app.save_ui_assessment(None, None, "Reviewer")
        with tempfile.TemporaryDirectory() as directory, patch("app.EXPORT_ROOT", Path(directory)):
            export_dir = Path(directory) / "run" / "export"
            export_dir.mkdir(parents=True)
            for name in ARTIFACTS:
                (export_dir / name).write_text("{}" if name == "selection.json" else "original")
            context = app.load_assessment_export("run/export")[0]
            fields = [*(["3"] * 5), *([""] * 5), *(["not_assessed"] * 4), *([""] * 4)]
            with self.assertRaisesRegex(Exception, "requires evidence"):
                app.save_ui_assessment(context, "run/export", "Reviewer", *fields)
            with self.assertRaisesRegex(Exception, "Export changed"):
                app.save_ui_assessment(context, "other/export", "Reviewer", *fields)
            with self.assertRaisesRegex(Exception, "Select a versioned"):
                app.load_assessment_export("../outside")

    def test_ui_empty_and_invalidated_review(self):
        loaded = app.load_assessment_export(None)
        self.assertIsNone(loaded[0])
        self.assertIsNone(loaded[1])
        self.assertEqual(loaded[4], [])
        self.assertEqual(app.invalidate_ui_assessment()[1], [])

    def test_assessment_uses_exported_audience_not_review_override(self):
        with tempfile.TemporaryDirectory() as directory:
            export_dir = Path(directory)
            for name in ARTIFACTS:
                (export_dir / name).write_text("original")
            (export_dir / "selection.json").write_text(json.dumps({
                "selection_context": {"target_audience": "Beginners", "audience_profile_version": "1.0"}
            }))
            review = self.review()
            review["artifact_sha256"] = artifact_hashes(export_dir)
            review["selection_context"] = {"target_audience": "Professionals"}
            destination = assess_export(export_dir, review)
            result = json.loads((destination / "assessment.json").read_text())
            self.assertEqual(result["selection_context"]["target_audience"], "Beginners")

    def test_assessment_tab_builds(self):
        demo = app.build_app()
        config = demo.get_config_file()
        labels = [component["props"].get("label") for component in config["components"]]
        self.assertIn("5. Assessment", labels)
        self.assertIn("Source fidelity (25%)", labels)
        self.assertIn("Human final review", labels)
        self.assertIn("Target audience", labels)
        self.assertNotIn("Teaching topic", labels)
        self.assertFalse(any(label and label.startswith("Learning objectives") for label in labels))


if __name__ == "__main__":
    unittest.main()