import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
from pipeline.assemble import _synthesize_title
from pipeline.assess import ARTIFACTS, DIMENSIONS, GATES, artifact_hashes, assess_export, review_template, score_review
from pipeline.contracts import selection_revision
from pipeline.export import export_clips
from pipeline.nominate import _describe_candidate, _normalize_candidates, nominate_candidates
from pipeline.rank import rank_candidates
from pipeline.editorial import infer_lecture_context, repair_sentence_boundaries, review_candidate
from pipeline.storyboard import AUDIENCE_PROFILES, draft_storyboard, generate_storyboard, storyboard_revision, storyboard_timeline, validate_storyboard


class CloudQuoteTests(unittest.TestCase):
    def test_local_illustration_quote_is_explicit_and_priced(self):
        from pipeline.cloud import cloud_quote, quote_markdown
        model = "@vertexai-us/gemini-3.5-flash"
        config = {"currency": "USD", "text_provider": "portkey", "media_provider": "local-illustration",
                  "price_source": "operator estimate", "checked_at": "2026-10-11",
                  "models": {"text": model, "image": model, "music": model, "video": "local-ffmpeg-zoompan"},
                  "usd_per_request": {"text": 0.05, "image": 0.05, "music": 0.05, "video": 0}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.json"
            with patch.dict("os.environ", {"CLOUD_PRICING_FILE": str(path)}):
                path.write_text(json.dumps(config))
                quote = cloud_quote()
                self.assertEqual(quote["items"]["image"]["provider"], "local-illustration")
                self.assertNotIn("video", quote["items"])
                for field in ("models", "usd_per_request"):
                    config[field].pop("video")
                path.write_text(json.dumps(config))
                self.assertEqual(cloud_quote(), quote)
                self.assertAlmostEqual(quote["total_usd"], 0.30)
                self.assertNotIn("music", quote["items"])
                self.assertEqual(quote["music_source"], "library")
                self.assertIn("no Google media requests", quote_markdown(quote))
                self.assertIn("bundled CC0 library", quote_markdown(quote))
                path.write_text(json.dumps({**config, "music_source": "generated"}))
                generated = cloud_quote()
                self.assertAlmostEqual(generated["total_usd"], 0.35)
                self.assertEqual(generated["items"]["music"]["provider"], "local-illustration")
                self.assertNotEqual(generated["revision"], quote["revision"])
                path.write_text(json.dumps({**config, "music_source": "stock"}))
                with self.assertRaisesRegex(ValueError, "music_source"):
                    cloud_quote()
                path.write_text(json.dumps(config))
                for change, message in ((lambda c: c.update(text_provider="google"), "requires text_provider portkey"),
                                        (lambda c: c["usd_per_request"].update(image=0), "positive"),
                                        (lambda c: c["models"].update(image="gemini-image"), "@provider/model")):
                    changed = copy.deepcopy(config)
                    change(changed)
                    path.write_text(json.dumps(changed))
                    with self.assertRaisesRegex(ValueError, message):
                        cloud_quote()

    def test_provider_changes_quote_and_requires_reviewed_pricing(self):
        from pipeline.cloud import CAPS, CloudBatch, cloud_quote, quote_markdown
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pricing.json"
            config = {"currency": "USD", "price_source": "https://example.invalid/reviewed-pricing",
                      "checked_at": "2026-10-11", "models": {kind: "fake" for kind in CAPS},
                      "usd_per_request": {kind: 0.1 for kind in CAPS}}
            config["models"]["text"] = "@vertexai-us/gemini-3.5-flash"
            with patch.dict("os.environ", {"CLOUD_PRICING_FILE": str(path)}):
                path.write_text(json.dumps(config))
                original = cloud_quote()
                config["text_provider"] = "portkey"
                path.write_text(json.dumps(config))
                routed = cloud_quote()
                self.assertNotEqual(original["revision"], routed["revision"])
                self.assertIn("Portkey and its configured provider", quote_markdown(routed))
                self.assertEqual(routed["items"]["text"]["base_url"], "https://llm-gateway.xgw.xero-test.com/v1")
                with patch.dict("os.environ", {"XERO_PORTKEY_API_KEY": "test-only"}):
                    with self.assertRaisesRegex(ValueError, "Cloud pricing or models changed"):
                        CloudBatch(Path(directory) / "batch", original, scope="selection")
                config["usd_per_request"]["text"] = None
                path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    cloud_quote()
                config["text_provider"] = "unsupported"
                path.write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, "text_provider"):
                    cloud_quote()


class CloudWorkflowTests(unittest.TestCase):
    def test_generation_opens_fresh_scoped_batch_without_cost_approval(self):
        clip = {"id": "c1", "start": 0, "end": 8, "title": "Check assumptions", "takeaway": "Check assumptions."}
        state = app.empty_state()
        state.update(run_id="test", work=str(self.root), approved=[clip])
        with patch("app.cloud_quote", return_value=self.quote):
            first = app.open_cloud_batch(state, "16:9")
            first_config = dict(state["cloud_batch"])
            second = app.open_cloud_batch(state, "9:16")
        self.assertNotEqual(first.directory, second.directory)
        self.assertNotEqual(first.scope, second.scope)
        self.assertEqual(state["cloud_batch"]["aspect"], "9:16")
        self.assertEqual(state["cloud_batch"]["current_revision"], selection_revision(state, [clip]))
        self.assertEqual(first_config["quote"], self.quote)
        self.assertEqual(first._load()["requests"], {})
        self.assertEqual(second._load()["requests"], {})
        first.close()
        second.close()

    def test_confirmation_fits_generates_and_synchronizes_review_rows(self):
        state = app.empty_state()
        segments = [{"start": index * 10, "end": (index + 1) * 10, "text": "Check assumptions before applying advice."}
                    for index in range(12)]
        clip = {"id": "c1", "start": 0, "end": 118.89, "title": "Check assumptions", "takeaway": "Check assumptions."}
        state.update(run_id="test", work=str(self.root), source_duration=120, transcript={"segments": segments},
                     candidates=[clip], min_s=20, max_s=90)
        rows = []
        for value, empty in (("c1", ""), (clip["title"], ""), (clip["takeaway"], ""), (0, 0), (118.89, 0), (True, False)):
            rows.extend([value, *([empty] * (app.MAX_REVIEW - 1))])
        response = {"opening": {"title": "Which assumptions matter?", "subtitle": "Check before applying advice", "evidence_ids": ["c1"]},
                    "ending": {"takeaway": "Check assumptions before applying advice.", "question": "Which assumption would you check?",
                               "cta": "Explain one assumption.", "seconds": 5, "evidence_ids": ["c1"]}, "transitions": []}
        request = Mock(return_value={"message": {"content": json.dumps(response)}, "provider": "portkey"})
        with patch("app.cloud_quote", return_value=self.quote), patch("pipeline.cloud.CloudBatch.text", request), patch(
            "pipeline.cloud.CloudBatch.close"
        ) as close_batch, patch("pipeline.fit.review_candidate",
            side_effect=lambda model, item, *args: dict(item, editorial_status="estimated", context_preserved=True, standalone=True)), patch(
                "app.prepare_narration", return_value=45), patch("app.prepare_media"), patch(
                "app.preview_package_storyboard", side_effect=lambda current, *args, **kwargs:
                    (current, "Preview ready", "preview.mp4", False, {"interactive": False})):
            outputs = list(app.confirm_and_generate_with_rows(state, "16:9", "local-model", *rows, progress=Mock()))
        final = outputs[-1]
        current = final[2 + 3 * app.MAX_KEEP]
        self.assertEqual(current["storyboard_draft"]["generation"]["provider"], "portkey")
        self.assertLess(current["approved"][0]["end"], 118.89)
        updated = list(final[-(5 * app.MAX_REVIEW + 1):-1])
        checked = app.confirm_selection(current, *rows[:app.MAX_REVIEW], *updated)[0]
        self.assertEqual(checked["approval_revision"], current["approval_revision"])
        self.assertEqual(final[3], current["approved"][0]["title"])
        self.assertEqual(len(final), len(outputs[0]))
        request.assert_called_once()
        close_batch.assert_called_once()
        self.assertTrue((self.root / "cloud" / current["cloud_batch"]["batch_id"] / "requests.json").is_file())
        self.assertEqual(current["cloud_batch"]["current_revision"], current["approval_revision"])
        self.assertNotIn("cloud_approval", current)

    def setUp(self):
        from pipeline.cloud import CAPS
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.quote = {"revision": "test", "items": {kind: {"model": "fake", "limit": limit, "usd_per_request": 1}
                                                   for kind, limit in CAPS.items()}}
        for fixture in (patch("pipeline.cloud.cloud_quote", return_value=self.quote),
                        patch.dict("os.environ", {"GEMINI_API_KEY": "test-only"})):
            fixture.start()
            self.addCleanup(fixture.stop)

    def batch(self):
        from pipeline.cloud import CloudBatch
        return CloudBatch(self.root, self.quote, scope="selection")

    def test_batch_needs_no_consent_and_does_not_submit_on_creation(self):
        batch = self.batch()
        self.assertEqual(batch._load()["requests"], {})
        self.assertIsNone(batch._client)
        self.assertIsNone(batch._portkey_client)
        batch.close()

    def test_ui_retains_preview_approval_but_has_no_cost_consent(self):
        with app.build_app() as demo:
            config = demo.get_config_file()
        checkboxes = [item["props"].get("label", "") for item in config["components"] if item["type"] == "checkbox"]
        self.assertFalse(any("cost" in label.lower() or "cloud" in label.lower() for label in checkboxes))
        self.assertFalse(hasattr(app, "approve_cloud_budget"))
        self.assertTrue(any("review" in label.lower() or "watch" in label.lower() for label in checkboxes))

    def test_completed_text_is_reused_and_tampering_blocks_regeneration(self):
        batch = self.batch()
        client = Mock()
        client.interactions.create.return_value = SimpleNamespace(output_text='{"opening": "example"}', usage=None)
        batch._client = client
        arguments = dict(model="fake", format={"type": "object"}, options={}, messages=[{"role": "user", "content": "Excerpt"}])
        first = batch.text(**arguments)
        self.assertEqual(batch.text(**arguments), first)
        client.interactions.create.assert_called_once()
        self.assertFalse(client.interactions.create.call_args.kwargs["store"])
        self.assertEqual(client.interactions.create.call_args.kwargs["response_format"]["schema"], arguments["format"])
        for path in self.root.glob("*.json"):
            if path.name != "requests.json":
                path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            batch.text(**arguments)
        client.interactions.create.assert_called_once()

    def test_failed_text_is_not_silently_resubmitted(self):
        batch = self.batch()
        batch._client = Mock()
        batch._client.interactions.create.side_effect = TimeoutError("secret must not be shown")
        arguments = dict(model="fake", format={}, options={}, messages=[])
        with self.assertRaisesRegex(RuntimeError, "TimeoutError") as error:
            batch.text(**arguments)
        self.assertNotIn("secret", str(error.exception))
        with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
            batch.text(**arguments)
        batch._client.interactions.create.assert_called_once()

    def test_request_limits_survive_reopening_batch(self):
        batch = self.batch()
        batch._reserve("image", {"prompt": "one"})
        batch._reserve("image", {"prompt": "two"})
        with self.assertRaisesRegex(ValueError, "cap reached"):
            self.batch()._reserve("image", {"prompt": "three"})

    def test_unavailable_model_reports_status_without_provider_secrets(self):
        batch = self.batch()
        error = RuntimeError("private-key-and-provider-details")
        setattr(error, "code", 404)
        batch._client = Mock()
        batch._client.interactions.create.side_effect = error
        with self.assertRaisesRegex(RuntimeError, "HTTP 404.*unavailable to this account") as caught:
            batch.text(model="fake", format={}, options={}, messages=[])
        self.assertNotIn("private-key", str(caught.exception))
        request = next(iter(json.loads(batch.path.read_text())["requests"].values()))
        self.assertEqual(request["http_status"], 404)
        self.assertNotIn("private-key", batch.path.read_text())

    def test_depleted_credits_reports_interactions_status_without_secrets(self):
        batch = self.batch()
        error = RuntimeError("private-key-and-provider-details")
        setattr(error, "status_code", 402)
        batch._client = Mock()
        batch._client.interactions.create.side_effect = error
        with self.assertRaisesRegex(RuntimeError, "HTTP 402.*prepaid credits are depleted") as caught:
            batch.text(model="fake", format={}, options={}, messages=[])
        self.assertNotIn("private-key", str(caught.exception))
        request = next(iter(json.loads(batch.path.read_text())["requests"].values()))
        self.assertEqual(request["http_status"], 402)
        self.assertNotIn("private-key", batch.path.read_text())

    def test_portkey_sdk_request_and_cache_without_google_client(self):
        import httpx
        from portkey_ai import Portkey
        self.quote["items"]["text"].update(provider="portkey", model="@vertexai-us/gemini-3.5-flash")
        requests = []

        def respond(request):
            self.assertEqual(str(request.url), "https://llm-gateway.xgw.xero-test.com/v1/chat/completions")
            self.assertEqual(request.headers["x-portkey-api-key"], "portkey-test-only")
            self.assertEqual(json.loads(request.headers["x-portkey-config"]), {"retry": {"attempts": 0}})
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 0,
                "model": "gemini-3.5-flash", "choices": [{"index": 0, "finish_reason": "stop",
                "message": {"role": "assistant", "content": '{"ok": true}'}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})

        def client_factory(**kwargs):
            self.assertEqual(kwargs["max_retries"], 0)
            self.assertEqual(kwargs["config"], {"retry": {"attempts": 0}})
            return Portkey(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond)))

        arguments = dict(model="ignored", format={"type": "object"}, options={},
                         messages=[{"role": "user", "content": "Return JSON."}])
        with patch.dict("os.environ", {"XERO_PORTKEY_API_KEY": "portkey-test-only",
                           "PORTKEY_API_KEY": "wrong-environment-key"}), patch(
                "portkey_ai.Portkey", side_effect=client_factory):
            batch = self.batch()
            try:
                response = batch.text(**arguments)
                self.assertEqual(response["provider"], "portkey")
                self.assertEqual(batch.text(**arguments), response)
                self.assertIsNone(batch._client)
            finally:
                batch.close()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["model"], self.quote["items"]["text"]["model"])
        self.assertEqual(requests[0]["max_tokens"], 4096)
        self.assertEqual(requests[0]["response_format"]["json_schema"]["schema"], arguments["format"])
        self.assertEqual(requests[0]["messages"], arguments["messages"])
        self.assertNotIn("portkey-test-only", batch.path.read_text())

    def test_portkey_missing_key_does_not_fall_back_to_google(self):
        self.quote["items"]["text"]["provider"] = "portkey"
        with patch.dict("os.environ", {"XERO_PORTKEY_API_KEY": "", "PORTKEY_API_KEY": "wrong-environment-key"}):
            with self.assertRaisesRegex(ValueError, "XERO_PORTKEY_API_KEY"):
                self.batch()
        self.assertFalse((self.root / "requests.json").exists())

    def local_batch(self):
        for kind in ("image", "music"):
            self.quote["items"][kind]["provider"] = "local-illustration"
        self.quote["items"]["text"]["provider"] = "portkey"
        with patch.dict("os.environ", {"XERO_PORTKEY_API_KEY": "test-only", "GEMINI_API_KEY": ""}):
            batch = self.batch()
        self.addCleanup(batch.close)
        batch._portkey_client = Mock()
        return batch

    @staticmethod
    def model_reply(content):
        usage = Mock(model_dump=Mock(return_value={"total_tokens": 3}))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))], usage=usage)

    @staticmethod
    def scene():
        shapes = [{"type": "rect", "fill": "#2a9d8f", "opacity": 1, "line_width": 0, "points": [0, 0.7, 1, 1]},
                  {"type": "ellipse", "fill": "#ff856b", "opacity": 0.9, "line_width": 0, "points": [0.1, 0.1, 0.3, 0.3]},
                  {"type": "polygon", "fill": "#264653", "opacity": 1, "line_width": 0, "points": [0.6, 0.7, 0.8, 0.3, 0.95, 0.7]},
                  {"type": "line", "fill": "#e9c46a", "opacity": 1, "line_width": 0.01, "points": [0.05, 0.9, 0.95, 0.85]}]
        shapes += [{"type": "rect", "fill": "#f4a261", "opacity": 1, "line_width": 0, "points": [0.1 * i, 0.5, 0.1 * i + 0.05, 0.7]} for i in range(1, 4)]
        return {"background_top": "#1d3557", "background_bottom": "#457b9d", "shapes": shapes}

    def test_local_illustration_uses_portkey_text_and_no_google_client(self):
        batch = self.local_batch()
        create = batch._portkey_client.chat.completions.create
        create.side_effect = [self.model_reply(self.scene()), self.model_reply({"tempo_bpm": 80, "notes": [
            {"midi": 48 + 4 * (i % 4), "start_beat": i, "beats": 1} for i in range(12)]})]
        image = batch.asset("image", "Brief", "16:9")
        self.assertEqual(batch.asset("image", "Brief", "16:9"), image)
        music = batch.asset("music", "Soft loop")
        self.assertEqual(create.call_count, 2)
        self.assertIsNone(batch._client)
        arguments = create.call_args_list[0].kwargs
        self.assertEqual(arguments["max_tokens"], 12000)
        self.assertEqual(arguments["response_format"]["json_schema"]["name"], "scene")
        self.assertEqual(image["provider"], "local-illustration")
        from PIL import Image
        with Image.open(image["path"]) as picture:
            self.assertEqual(picture.size, (1280, 720))
        self.assertTrue(music["path"].endswith(".wav"))
        self.assertNotIn("video", {item["kind"] for item in json.loads(batch.path.read_text())["requests"].values()})

    def test_local_illustration_invalid_model_data_fails_once_without_resubmission(self):
        batch = self.local_batch()
        create = batch._portkey_client.chat.completions.create
        create.return_value = self.model_reply({"background_top": "red", "background_bottom": "#000000", "shapes": []})
        with self.assertRaisesRegex(RuntimeError, "Local image request incomplete"):
            batch.asset("image", "Brief")
        with self.assertRaisesRegex(RuntimeError, "previous request failed"):
            batch.asset("image", "Brief")
        create.assert_called_once()

    def test_local_illustration_truncated_model_output_is_reported(self):
        batch = self.local_batch()
        reply = self.model_reply({"shapes": []})
        reply.choices[0].finish_reason = "length"
        batch._portkey_client.chat.completions.create.return_value = reply
        with self.assertRaisesRegex(RuntimeError, "Local image request incomplete \\(ValueError\\)"):
            batch.asset("image", "Brief")
        request = next(iter(json.loads(batch.path.read_text())["requests"].values()))
        self.assertEqual(request["error_type"], "ValueError")

    def test_video_cannot_be_requested_as_generated_asset(self):
        batch = self.batch()
        for provider in ("google", "local-illustration"):
            with self.subTest(provider=provider):
                batch.quote["items"]["image"]["provider"] = provider
                with self.assertRaisesRegex(ValueError, "Unsupported asset kind"):
                    batch.asset("video", "Brief")
        self.assertIsNone(batch._client)
        self.assertIsNone(batch._portkey_client)
        self.assertEqual(json.loads(batch.path.read_text())["requests"], {})

    def test_prepare_media_only_requests_card_images_and_optional_music(self):
        from pipeline.media import prepare_media
        clips = [{"id": "c1", "start": 0, "end": 15, "title": "Check assumptions",
                  "takeaway": "Check assumptions.", "excerpt": "Check assumptions."}]
        for audience in AUDIENCE_PROFILES:
            for generated_music in (False, True):
                with self.subTest(audience=audience, generated_music=generated_music):
                    draft = draft_storyboard(clips, audience)
                    batch = Mock()
                    batch.quote = {"items": {"music": {}} if generated_music else {}}
                    batch.asset.side_effect = lambda kind, *args: {"kind": kind}
                    with patch("pipeline.media.verify_media"), patch("pipeline.music.library_asset", return_value={"kind": "music"}), patch.dict(
                        "os.environ", {"LOCAL_SFX_MANIFEST": ""}
                    ):
                        prepare_media(draft, batch, "16:9")
                    self.assertEqual(set(draft["media_assets"]), {"opening", "ending", "music"})
                    self.assertEqual([call.args[0] for call in batch.asset.call_args_list],
                                     ["image", "image"] + (["music"] if generated_music else []))
                    batch.local_broll.assert_not_called()
                    self.assertEqual(draft["creative_version"], "2.0")

    def test_legacy_broll_requires_regeneration_before_render_or_export(self):
        from pipeline.media import verify_media
        for assets in ({"broll": {}}, {"legacy": {"kind": "video"}}):
            with self.subTest(assets=assets), self.assertRaisesRegex(ValueError, "Regenerate and approve"):
                verify_media({"media_assets": assets})

    def test_missing_google_media_key_does_not_consume_request_cap(self):
        self.quote["items"]["text"]["provider"] = "portkey"
        with patch.dict("os.environ", {"XERO_PORTKEY_API_KEY": "test-only", "GEMINI_API_KEY": ""}):
            batch = self.batch()
            with self.assertRaisesRegex(ValueError, "GEMINI_API_KEY"):
                batch.asset("image", "prompt")
        self.assertEqual(json.loads(batch.path.read_text())["requests"], {})

    def test_portkey_failure_is_redacted_and_not_retried(self):
        self.quote["items"]["text"]["provider"] = "portkey"
        with patch.dict("os.environ", {"XERO_PORTKEY_API_KEY": "portkey-test-only"}):
            batch = self.batch()
            batch._portkey_client = Mock()
            batch._portkey_client.chat.completions.create.side_effect = TimeoutError("private-portkey-key")
            arguments = dict(model="fake", format={}, options={}, messages=[])
            with self.assertRaisesRegex(RuntimeError, "Portkey text request failed") as caught:
                batch.text(**arguments)
            self.assertNotIn("private-portkey-key", str(caught.exception))
            with self.assertRaisesRegex(RuntimeError, "unknown outcome"):
                batch.text(**arguments)
            batch._portkey_client.chat.completions.create.assert_called_once()
            self.assertIsNone(batch._client)

    def test_media_hashes_are_verified(self):
        from pipeline.media import verify_media
        from pipeline.project import file_hash
        path = self.root / "image.png"
        path.write_bytes(b"original")
        draft = {"media_assets": {"opening": {"path": str(path), "sha256": file_hash(path), "kind": "image"}}}
        verify_media(draft)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "missing or changed"):
            verify_media(draft)


class StoryboardTests(unittest.TestCase):
    def setUp(self):
        isolated_callbacks = {
            "test_confirmation_generates_in_the_same_callback",
            "test_automatic_generation_populates_fields_and_renders_preview",
            "test_failed_generation_clears_stale_approval_and_does_not_preview",
            "test_confirmation_reports_generation_failure_on_review_tab",
            "test_failed_auto_preview_keeps_generated_fields_without_approval",
        }
        if self._testMethodName in isolated_callbacks:
            batch = SimpleNamespace(quote={"items": {"text": {"model": "selected-model"}}}, text=Mock(), close=Mock())
            for fixture in (patch("app.open_cloud_batch", return_value=batch),
                            patch("app.fit_selection", side_effect=lambda clips, *args, **kwargs: (clips, {})),
                            patch("app.prepare_narration", return_value=45), patch("app.prepare_media")):
                fixture.start()
                self.addCleanup(fixture.stop)

    def test_fitter_trims_long_selection_at_reviewed_sentence_boundaries(self):
        from pipeline.fit import fit_selection
        segments = [{"start": index * 10, "end": (index + 1) * 10, "text": "A complete supported idea."}
                    for index in range(12)]
        clip = {"id": "c1", "start": 0, "end": 118.89, "title": "Original", "takeaway": "Original"}
        with patch("pipeline.fit.review_candidate", side_effect=lambda model, item, *args: dict(
            item, editorial_status="estimated", context_preserved=True, standalone=True)):
            fitted, report = fit_selection([clip], segments, audience="General public", model="local",
                                           source_duration=120, maximum=90)
        self.assertTrue(30 <= report["estimated_seconds"] <= 60)
        self.assertLess(fitted[0]["end"], 118.89)
        self.assertEqual(fitted[0]["end"] % 10, 0)
        self.assertLessEqual(report["reviewed_windows"], 12)

    def test_fitter_rejects_insufficient_evidence_without_review(self):
        from pipeline.fit import fit_selection
        with patch("pipeline.fit.review_candidate") as review:
            with self.assertRaisesRegex(ValueError, "No coherent selection"):
                fit_selection([{"id": "c1", "start": 0, "end": 10}],
                              [{"start": 0, "end": 10, "text": "Short idea."}],
                              audience="Kids", model="local", source_duration=10)
            review.assert_not_called()

    def test_versioned_duration_boundaries(self):
        from pipeline.storyboard import validate_compilation_duration, LEGACY_DURATION_BUDGET, PREVIOUS_DURATION_BUDGET
        for seconds in (30, 45, 60):
            validate_compilation_duration(seconds)
        for seconds in (29.999, 60.001, float("nan")):
            with self.assertRaisesRegex(ValueError, "30-60"):
                validate_compilation_duration(seconds)
        for seconds in (60, 75, 90):
            validate_compilation_duration(seconds, PREVIOUS_DURATION_BUDGET)
        with self.assertRaisesRegex(ValueError, "60-90"):
            validate_compilation_duration(59.999, PREVIOUS_DURATION_BUDGET)
        validate_compilation_duration(118.89, LEGACY_DURATION_BUDGET)

    def test_current_audience_taxonomy(self):
        from pipeline.storyboard import AUDIENCE_GOALS, DEFAULT_AUDIENCE
        self.assertEqual(set(AUDIENCE_GOALS), {"Researcher", "General public", "Kids"})
        self.assertEqual(set(AUDIENCE_GOALS), set(AUDIENCE_PROFILES))
        self.assertEqual(DEFAULT_AUDIENCE, "General public")

    def test_reviewed_narration_is_reused_and_tampering_blocks_export(self):
        from pipeline.assemble import narration_audio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = AUDIENCE_PROFILES["Kids"]
            with patch("pipeline.assemble._synthesize_title", side_effect=lambda text, path, **kwargs: path.write_bytes(b"audio")) as synth, patch(
                "pipeline.assemble.media_duration", return_value=2
            ):
                asset = narration_audio("Example", root / "preview.aiff", profile, root / "cache")
                approved = {asset["cache_key"]: asset["sha256"]}
                self.assertEqual(asset, narration_audio("Example", root / "export.aiff", profile, root / "cache", approved))
                self.assertEqual(synth.call_count, 1)
                (root / "cache" / f"{asset['cache_key']}.aiff").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "Approved narration"):
                    narration_audio("Example", root / "export.aiff", profile, root / "cache", approved)
                self.assertEqual(synth.call_count, 1)

    def test_unchanged_generated_controls_do_not_clear_automatic_preview(self):
        state, values, _ = self.package()
        _, revision = app._read_storyboard(state, values)
        state.update(storyboard_preview_revision=revision, storyboard_approval={"revision": revision})
        self.assertEqual(app.invalidate_edited_storyboard(state, *values), tuple(app.gr.skip() for _ in range(5)))
        values[0] = "Actually edited hook"
        changed = app.invalidate_edited_storyboard(state, *values)
        self.assertIsNone(changed[0]["storyboard_preview_revision"])
        self.assertIsNone(changed[0]["storyboard_approval"])
        self.assertIsNone(changed[2])

    def test_confirmation_generates_in_the_same_callback(self):
        state, rows = PipelineTests().selection()
        generated = draft_storyboard(state["approved"], "General public")
        with patch("app.generate_storyboard", return_value=generated), patch(
            "app.preview_package_storyboard", side_effect=lambda state, *args, **kwargs: (
                state, "Preview ready", "preview.mp4", False, {"interactive": False})
        ):
            outputs = list(app.confirm_and_generate_storyboard(state, "Original", "model", *rows, progress=Mock()))
        self.assertTrue(all(len(output) == 2 + 3 * app.MAX_KEEP + len(app.draft_package_storyboard(state, "Original"))
                            for output in outputs))
        self.assertEqual(outputs[-1][2 + 3 * app.MAX_KEEP]["storyboard_draft"], generated)
        self.assertFalse(app.confirm_selection(state, *rows)[3]["interactive"])

    def test_preview_auto_generates_missing_or_stale_draft(self):
        state, values, _ = self.package()
        for draft, revision in [(None, None), (state["storyboard_draft"], "stale")]:
            pending = dict(state, storyboard_draft=draft, storyboard_selection_revision=revision)
            with patch("app.generate_package_storyboard", return_value=iter([("generated",)])) as generate:
                self.assertEqual(list(app.preview_or_generate_storyboard(pending, "model", *values)), [("generated",)])
            generate.assert_called_once_with(pending, values[-1], "model", progress=unittest.mock.ANY)

    def test_preview_preserves_existing_draft_and_live_edits(self):
        state, values, _ = self.package()
        values[0] = "Edited hook"
        with patch("app.generate_package_storyboard") as generate, patch(
            "app.preview_package_storyboard", return_value=(state, "ready", "preview.mp4", False, {"interactive": False})
        ) as preview:
            outputs = list(app.preview_or_generate_storyboard(state, "model", *values))
        generate.assert_not_called()
        self.assertEqual(preview.call_args.args[1], "Edited hook")
        self.assertEqual(len(outputs[-1]), len(app.draft_package_storyboard(state, "Original")))

    def test_voice_profiles_reach_speech_command_and_revision(self):
        self.assertEqual(set(app.AUDIENCE_GOALS), set(AUDIENCE_PROFILES))
        with tempfile.TemporaryDirectory() as directory:
            for audience, profile in AUDIENCE_PROFILES.items():
                self.assertEqual(profile["provider"], "kokoro")
                with self.subTest(audience=audience), patch("pipeline.kokoro_tts.synthesize") as synth, patch(
                    "pipeline.assemble.run_cmd"
                ) as command:
                    draft = draft_storyboard(self.clips(), audience)
                    self.assertEqual(draft["narration_profile"], profile)
                    dest = Path(directory) / "speech.aiff"
                    _synthesize_title("Check an example.", dest, voice=profile["voice"], rate=profile["rate"],
                                      speed=profile["speed"], pitch_semitones=profile["pitch_semitones"])
                    synth.assert_called_once_with("Check an example.", dest, voice=profile["voice"],
                                                  speed=profile["speed"], pitch_semitones=profile["pitch_semitones"],
                                                  ffmpeg=unittest.mock.ANY)
                    command.assert_not_called()
                    original = storyboard_revision("selection", draft)
                    draft["ending"]["narration"] = False
                    self.assertNotEqual(original, storyboard_revision("selection", draft))
        self.assertEqual(AUDIENCE_PROFILES["Kids"]["pitch_semitones"], 2)

    def test_legacy_say_voices_still_reach_local_speech(self):
        """Retired audience profiles keep their macOS voice so old drafts still render."""
        from pipeline.storyboard import LEGACY_AUDIENCE_PROFILES

        with tempfile.TemporaryDirectory() as directory:
            for audience in ("Students", "Professionals"):
                profile = LEGACY_AUDIENCE_PROFILES[audience]
                self.assertIn(profile["voice"], ("Samantha", "Daniel"))
                with self.subTest(audience=audience), patch("pipeline.assemble.shutil.which",
                                                            return_value="/usr/bin/say"), patch(
                    "pipeline.assemble.run_cmd"
                ) as command:
                    dest = Path(directory) / "legacy.aiff"
                    _synthesize_title("Check an example.", dest, voice=profile["voice"], rate=profile["rate"])
                    command.assert_called_once_with(["/usr/bin/say", "-v", profile["voice"], "-r", str(profile["rate"]),
                                                     "-f", str(dest.with_suffix(".txt")), "-o", str(dest)])

    def test_unavailable_speech_fails_explicitly(self):
        with patch("pipeline.assemble.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "macOS speech"):
                _synthesize_title("Example", Path("unused.aiff"), voice="Samantha")

    def test_kids_automatically_use_kokoro_and_reject_old_overrides(self):
        draft = draft_storyboard(self.clips(), "Kids")
        self.assertEqual(draft["narration_profile"]["voice"], "af_sky")
        self.assertEqual(draft["narration_profile"]["speed"], 0.98)
        validate_storyboard(draft, self.clips())
        draft["narration_voice"] = "Karen"
        with self.assertRaisesRegex(ValueError, "Manual voice overrides"):
            validate_storyboard(draft, self.clips())
        draft.pop("narration_voice")
        for old_voice in ("Kevin", "Junior", "Samantha", "en-US-AvaNeural", "en-US-AnaNeural"):
            draft["narration_profile"]["voice"] = old_voice
            with self.assertRaisesRegex(ValueError, "Narration profile changed"):
                validate_storyboard(draft, self.clips())

    def test_kokoro_passes_plain_text_and_explicit_settings_to_worker(self):
        def worker(command, **kwargs):
            job = json.loads(kwargs["input"])
            Path(job["destination"]).write_bytes(b"test audio")

        with tempfile.TemporaryDirectory() as directory, patch("pipeline.kokoro_tts.Path.is_file", return_value=True), patch(
            "pipeline.kokoro_tts.subprocess.run", side_effect=worker
        ) as command:
            dest = Path(directory) / "child.aiff"
            text = "Why is 1 < 2 & what comes next?"
            profile = draft_storyboard(self.clips(), "Kids")["narration_profile"]
            self.assertEqual(_synthesize_title(text, dest, voice=profile["voice"], rate=profile["rate"],
                                              speed=profile["speed"], pitch_semitones=profile["pitch_semitones"]), dest)
            job = json.loads(command.call_args.kwargs["input"])
            self.assertEqual((job["text"], job["voice"], job["speed"], job["pitch_semitones"]),
                             (text, "af_sky", 0.98, 2))
            self.assertEqual(dest.with_suffix(".txt").read_text(), text)
            self.assertEqual(dest.read_bytes(), b"test audio")
            self.assertEqual(command.call_args.kwargs["timeout"], 180)

    def test_kokoro_missing_environment_never_falls_back(self):
        with patch("pipeline.kokoro_tts.Path.is_file", return_value=False), patch(
            "pipeline.assemble.run_cmd"
        ) as command:
            with self.assertRaisesRegex(RuntimeError, "Local Kokoro is not installed"):
                _synthesize_title("Example", Path("unused.aiff"))
            command.assert_not_called()

    def test_kokoro_worker_failure_preserves_existing_audio(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory, patch("pipeline.kokoro_tts.Path.is_file", return_value=True), patch(
            "pipeline.kokoro_tts.subprocess.run", side_effect=subprocess.TimeoutExpired("kokoro", 180)
        ):
            destination = Path(directory) / "speech.aiff"
            destination.write_bytes(b"existing")
            with self.assertRaisesRegex(RuntimeError, "No fallback voice was used"):
                _synthesize_title("Example", destination)
            self.assertEqual(destination.read_bytes(), b"existing")

    def test_retired_edge_voice_requires_regeneration(self):
        with patch("pipeline.assemble.run_cmd") as command:
            with self.assertRaisesRegex(RuntimeError, "Regenerate the storyboard"):
                _synthesize_title("Example", Path("unused.aiff"), voice="en-US-AnaNeural")
            command.assert_not_called()

    def test_kokoro_cache_tracks_processing_and_blocks_old_approval(self):
        from pipeline.assemble import narration_audio
        with tempfile.TemporaryDirectory() as directory, patch(
            "pipeline.assemble._synthesize_title", side_effect=lambda text, path, **kwargs: path.write_bytes(b"audio")
        ) as synth, patch("pipeline.assemble.media_duration", return_value=2):
            root = Path(directory)
            profile = AUDIENCE_PROFILES["Kids"]
            original = narration_audio("Example", root / "one.aiff", profile, root / "cache")
            approved = {original["cache_key"]: original["sha256"]}
            keys = {original["cache_key"]}
            for field, value in (("speed", 0.9), ("pitch_semitones", 4), ("voice", "af_heart")):
                changed = {**profile, field: value}
                with self.assertRaisesRegex(ValueError, "Approved narration"):
                    narration_audio("Example", root / "two.aiff", changed, root / "cache", approved)
                asset = narration_audio("Example", root / "two.aiff", changed, root / "cache")
                keys.add(asset["cache_key"])
            with patch("pipeline.kokoro_tts.RENDER_VERSION", "new-renderer"):
                asset = narration_audio("Example", root / "two.aiff", profile, root / "cache")
                keys.add(asset["cache_key"])
            self.assertEqual(len(keys), 5)
            self.assertEqual(synth.call_count, 5)
            with self.assertRaisesRegex(ValueError, "requires its cache"):
                narration_audio("Example", root / "two.aiff", profile, approved_assets=approved)
            self.assertEqual(synth.call_count, 5)

    def test_legacy_storyboard_and_profile_validation(self):
        draft = draft_storyboard(self.clips(), "General public")
        draft.pop("narration_profile")
        draft["ending"].pop("narration")
        validate_storyboard(draft, self.clips())
        draft = draft_storyboard(self.clips(), "Kids")
        draft["narration_profile"]["rate"] = 300
        with self.assertRaisesRegex(ValueError, "Narration profile changed"):
            validate_storyboard(draft, self.clips())

    def test_regeneration_uses_audience_and_discards_old_voice_selection(self):
        state, values, _ = self.package()
        state["narration_voice"] = "Karen"
        generated = draft_storyboard(state["approved"], "Kids")
        generated["narration_voice"] = "Karen"
        generated["narration_profile"]["voice"] = "Karen"
        previous = copy.deepcopy(generated)
        regenerated = app.draft_package_storyboard(state, "Original", generated=generated)[0]
        self.assertEqual(generated, previous)
        self.assertEqual(regenerated["storyboard_draft"]["narration_profile"]["voice"], "af_sky")
        self.assertNotIn("narration_voice", regenerated)
        self.assertNotIn("narration_voice", regenerated["storyboard_draft"])
        self.assertIsNone(regenerated["storyboard_preview_revision"])
        self.assertIsNone(regenerated["storyboard_approval"])

    def test_automatic_generation_populates_fields_and_renders_preview(self):
        state, _ = PipelineTests().selection()
        generated = draft_storyboard(state["approved"], "General public")
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
        self.assertEqual(outputs[-1][0]["storyboard_error"], "offline")
        with self.assertRaisesRegex(Exception, "generation failed: offline"):
            app._read_storyboard(outputs[-1][0], [])
        preview.assert_not_called()

    def test_confirmation_reports_generation_failure_on_review_tab(self):
        state, rows = PipelineTests().selection()
        with patch("app.generate_storyboard", side_effect=RuntimeError("model unavailable")):
            outputs = list(app.confirm_and_generate_storyboard(state, "Original", "model", *rows, progress=Mock()))
        self.assertIn("model unavailable", outputs[-1][0])
        self.assertEqual(outputs[-1][0], outputs[-1][3 + 3 * app.MAX_KEEP])

    def test_confirmation_and_preview_event_wiring(self):
        demo = app.build_app()
        for name in ("confirm_and_generate_with_rows", "preview_or_generate_storyboard"):
            dependency = next(item for item in demo.config["dependencies"] if item["api_name"] == name)
            self.assertIsNone(dependency["trigger_after"])
            self.assertTrue(dependency["types"]["generator"])

    def test_failed_auto_preview_keeps_generated_fields_without_approval(self):
        state, _ = PipelineTests().selection()
        generated = draft_storyboard(state["approved"], "General public")
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
        draft = draft_storyboard(clips, "General public")
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
        values[9:9 + app.MAX_KEEP - 1] = ["Cut"] * (app.MAX_KEEP - 1)
        self.assertIn("22.5s", app.storyboard_duration_status(state, *values))
        values[9] = "Overlay"
        self.assertIn("22.5s", app.storyboard_duration_status(state, *values))
        values[9] = "Divider"
        self.assertIn("24.0s", app.storyboard_duration_status(state, *values))

    def generated_response(self):
        return {"opening": {"title": "Which advice deserves a second look?", "subtitle": "Examples and limits", "evidence_ids": ["c0", "c1"]},
                "ending": {"takeaway": "Check assumptions before applying advice.", "question": "Where might the advice not apply?",
                           "cta": "Test one assumption in your next example.", "seconds": 5, "evidence_ids": ["c0", "c1"]},
                "transitions": [{"before_clip_id": "c1", "mode": "Divider", "label": "Another perspective", "evidence_ids": ["c0", "c1"]}]}

    def generation_clips(self):
        return [dict(clip, end=clip["start"] + 15) for clip in self.clips()[:2]]

    def test_generated_storyboard_uses_all_excerpts_and_audience(self):
        response = {"message": {"content": json.dumps(self.generated_response())}}
        with patch("pipeline.storyboard.request_text", return_value=response) as chat:
            draft = generate_storyboard("model", self.generation_clips(), "General public")
        self.assertEqual(draft["generation"]["status"], "model_draft")
        self.assertEqual(draft["opening"]["mode"], "Card")
        self.assertTrue(draft["opening"]["narration"])
        self.assertTrue(all(transition["narration"] for transition in draft["transitions"]))
        self.assertEqual(chat.call_args.kwargs["format"]["properties"]["transitions"]["items"]["properties"]["mode"]["enum"], ["Divider"])
        self.assertEqual(draft["ending"]["evidence_ids"], ["c0", "c1"])
        self.assertTrue(draft["ending"]["narration"])
        self.assertEqual(draft["ending"]["cta"], self.generated_response()["ending"]["cta"])
        payload = json.loads(chat.call_args.kwargs["messages"][1]["content"])
        self.assertEqual(payload["audience"], "General public")
        self.assertEqual(payload["audience_profile"], AUDIENCE_PROFILES["General public"])
        instruction = chat.call_args.kwargs["messages"][0]["content"]
        self.assertIn("audience-specific hook", instruction)
        self.assertIn("FOLLOWING excerpt", instruction)
        self.assertIn("CTA must name a specific idea", instruction)
        self.assertEqual(len(payload["selected_clips_in_order"]), 2)
        validate_storyboard(draft, self.generation_clips())

    def test_generation_retries_invalid_references_but_never_silently_falls_back(self):
        invalid = self.generated_response()
        invalid["ending"]["evidence_ids"] = ["invented"]
        bad = {"message": {"content": json.dumps(invalid)}}
        good = {"message": {"content": json.dumps(self.generated_response())}}
        with patch("pipeline.storyboard.request_text", side_effect=[bad, good]):
            self.assertEqual(generate_storyboard("model", self.generation_clips(), "General public")["generation"]["attempts"], 2)
        with patch("pipeline.storyboard.request_text", return_value=bad) as chat:
            with self.assertRaisesRegex(ValueError, "two attempts"):
                generate_storyboard("model", self.generation_clips(), "General public")
            self.assertEqual(chat.call_count, 2)

    def test_generation_retries_generic_cta(self):
        generic = self.generated_response()
        generic["ending"]["cta"] = "Try it now!"
        bad = {"message": {"content": json.dumps(generic)}}
        good = {"message": {"content": json.dumps(self.generated_response())}}
        with patch("pipeline.storyboard.request_text", side_effect=[bad, good]):
            self.assertEqual(generate_storyboard("model", self.generation_clips(), "Kids")["generation"]["attempts"], 2)
        with patch("pipeline.storyboard.request_text", return_value=bad):
            with self.assertRaisesRegex(ValueError, "CTA is generic"):
                generate_storyboard("model", self.generation_clips(), "Kids")

    def test_generated_joins_cannot_silently_omit_transition_frames(self):
        result = self.generated_response()
        result["transitions"][0].update(mode="Cut", label="")
        with patch("pipeline.storyboard.request_text", return_value={"message": {"content": json.dumps(result)}}):
            with self.assertRaisesRegex(ValueError, "Divider transition frame"):
                generate_storyboard("model", self.generation_clips(), "General public")

    def test_generated_budget_counts_dividers_and_blocks_impossible_selection(self):
        with patch("pipeline.storyboard.request_text") as chat:
            with self.assertRaisesRegex(ValueError, "cannot fit"):
                generate_storyboard("model", self.clips()[:2], "General public")
            chat.assert_not_called()
        clips = self.generation_clips()
        draft = draft_storyboard(clips, "General public")
        clips[1]["end"] += 20
        draft = draft_storyboard(clips, "General public")
        draft["duration_budget"] = {"min_seconds": 45, "max_seconds": 120}
        validate_storyboard(draft, clips)
        clips[1]["end"] += 100
        draft = draft_storyboard(clips, "General public")
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
        clips = [dict(self.clips()[0], end=30)]
        response = self.generated_response()
        response["transitions"] = []
        for section in ("opening", "ending"):
            response[section]["evidence_ids"] = ["c0"]
        with patch("pipeline.storyboard.request_text", return_value={"message": {"content": json.dumps(response)}}) as chat:
            draft = generate_storyboard("model", clips, "General public")
        self.assertEqual(draft["transitions"], [])
        transition_schema = chat.call_args.kwargs["format"]["properties"]["transitions"]
        self.assertEqual(transition_schema["maxItems"], 0)
        self.assertNotIn('"enum": []', json.dumps(chat.call_args.kwargs["format"]))
        timeline = storyboard_timeline(clips, [30], draft)
        self.assertEqual([item["kind"] for item in timeline], ["opening", "clip", "ending"])
        self.assertEqual(timeline[-1]["start"] + timeline[-1]["duration"], 38)
        with patch("pipeline.storyboard.request_text") as chat:
            with self.assertRaisesRegex(ValueError, "cannot fit"):
                generate_storyboard("model", [dict(clips[0], end=15)], "General public")
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
            for index, replacement in [(0, "Changed title"), (2, 3), (-1, "9:16"), (-2, "Middle")]:
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

    def test_draft_has_source_evidence_narrated_intro_and_transition_frames(self):
        clips = self.clips()
        draft = draft_storyboard(clips, "General public")
        validate_storyboard(draft, clips)
        self.assertEqual(draft["evidence"][0]["quote"], clips[0]["excerpt"])
        self.assertEqual(draft["opening"]["mode"], "Card")
        self.assertTrue(draft["opening"]["narration"])
        self.assertTrue(all(item["mode"] == "Divider" and item["label"] for item in draft["transitions"]))
        self.assertTrue(draft["ending"]["question"].endswith("?"))

    def test_revision_binds_text_timing_selection_and_aspect(self):
        draft = draft_storyboard(self.clips(), "General public")
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
        draft = draft_storyboard(clips, "General public")
        draft["transitions"][0].update(mode="Divider", label="Another example")
        draft["transitions"][1].update(mode="Overlay", label="A limitation")
        timeline = storyboard_timeline(clips, [8, 8, 8], draft)
        self.assertEqual([item["start"] for item in timeline if item["kind"] == "clip"], [2.5, 12, 20])
        self.assertEqual(timeline[-1]["start"], 28)

    def test_dense_text_stale_evidence_and_invalid_times_rejected(self):
        clips = self.clips()
        for mutate in [lambda draft: draft["ending"].update(takeaway="word " * 17),
                       lambda draft: draft["opening"].update(seconds=float("nan")),
                       lambda draft: draft["evidence"][0].update(quote="invented")]:
            draft = draft_storyboard(clips, "General public")
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
                result = app.transcribe_for_audience(str(source), "General public", "", 20, 45,
                                                     "Original", "test-model", None, progress=Mock())
            self.assertEqual(result[0]["target_audience"], "General public")
            self.assertEqual(result[0]["objectives"], app.AUDIENCE_GOALS["General public"])
            self.assertEqual(result[0]["transcript"], transcript)
            self.assertEqual(len(result), 8 + 9 * app.MAX_REVIEW)

    def test_audience_change_preserves_transcript_and_clears_review(self):
        state = app.empty_state()
        state.update(transcript={"segments": [{"text": "Speech"}]}, candidates=[{"id": "c1"}],
                     approved=[{"id": "c1"}], confirmed=True, approval_revision="old")
        result = app.change_target_audience("Researcher", state)
        updated = result[0]
        self.assertEqual(updated["transcript"], state["transcript"])
        self.assertEqual(updated["objectives"], app.AUDIENCE_GOALS["Researcher"])
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
        state["target_audience"] = "Researcher"
        self.assertNotEqual(selection_revision(state, []), original)

    def test_rerun_uses_current_duration_settings_without_transcription(self):
        with patch("app.nominate_and_rank", return_value="ranked") as nominate:
            self.assertEqual(app.nominate_for_audience(app.empty_state(), "model", "Researcher", 25, 60), "ranked")
        state = nominate.call_args.args[0]
        self.assertEqual((state["min_s"], state["max_s"]), (25, 60))
        self.assertEqual(state["objectives"], app.AUDIENCE_GOALS["Researcher"])
        for minimum, maximum in [(50, 20), (0, 30), (20, float("nan"))]:
            with self.assertRaises(Exception):
                app.nominate_for_audience({}, "model", "General public", minimum, maximum)
            state = app.empty_state()
            state.update(confirmed=True, approval_revision="old", transcript={"segments": []})
            changed = app.change_duration_limits(25, 60, "General public", state)[0]
            self.assertFalse(changed["confirmed"])
            self.assertIsNone(changed["approval_revision"])
            self.assertEqual(changed["transcript"], state["transcript"])

    def test_audience_goals_reach_nomination_and_ranking(self):
        state = app.empty_state()
        state.update(transcript={"segments": [{"start": 0, "end": 20, "text": "Lecture content"}]},
                     source_video="source.mp4", work="work")
        state = app.change_target_audience("Researcher", state)[0]
        with patch("app.list_ollama_models", return_value=(["test-model"], "ready")), patch(
            "app.media_duration", return_value=30
        ), patch("app.nominate_candidates", return_value=[]) as nominate, patch(
            "app.rank_candidates", return_value=[]
        ) as rank, patch("app.infer_lecture_context", return_value={"status": "draft", "concepts": []}):
            app.nominate_and_rank(state, "test-model", progress=Mock())
        self.assertEqual(nominate.call_args.kwargs["objectives"], app.AUDIENCE_GOALS["Researcher"])
        self.assertIn("researcher", nominate.call_args.kwargs["topic"])
        self.assertEqual(rank.call_args.args[1], app.AUDIENCE_GOALS["Researcher"])
        self.assertEqual(rank.call_args.kwargs["target_audience"], "Researcher")
        self.assertEqual(nominate.call_args.kwargs["target_audience"], "Researcher")


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
                                "General public", ["Explain basics"], {}, 10, 30, 30)

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
        self.assertEqual(ask.call_args.args[2]["audience"], "General public")
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
                                         target_audience="General public", lecture_context={})
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
            ranked = rank_candidates(candidates, [], self.segments(), target_audience="General public")
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

    def test_nomination_corrects_short_model_ranges_without_padding_valid_selection(self):
        segments = [{"start": start, "end": start + 10, "text": "One part of an explanation."}
                    for start in (0, 10, 20, 30, 40, 50)]
        bad = {"message": {"content": json.dumps({"candidates": [{"start": 0, "end": 10}]})}}
        good = {"message": {"content": json.dumps({"candidates": [{"start": 0, "end": 50}]})}}
        with patch("pipeline.nominate.ollama.chat", side_effect=[bad, good]) as chat, patch(
            "pipeline.nominate._window_fallback", return_value=[]
        ), patch("pipeline.nominate._describe_candidate", side_effect=lambda model, candidate, segments: candidate):
            result = nominate_candidates("model", "Topic", ["Goal"], segments, 20, 90, 60)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(chat.call_args.kwargs["options"]["num_ctx"], 16384)
        self.assertIn("Check end minus start", chat.call_args.kwargs["messages"][-1]["content"])
        self.assertEqual([(item["start"], item["end"]) for item in result], [(0, 50)])

    def test_nomination_recovers_when_all_model_candidates_are_rejected(self):
        segments = [{"start": start, "end": start + 20, "text": "A complete source example."}
                    for start in (0, 20, 40)]
        response = {"message": {"content": json.dumps({"candidates": [{"start": 0, "end": 20}]})}}
        alternative = {"start": 20, "end": 40, "source": "window"}

        def review(model, candidate, *args):
            return {**candidate, "editorial_status": "estimated" if candidate["source"] == "window" else "rejected"}

        with patch("pipeline.nominate.ollama.chat", return_value=response), patch(
            "pipeline.nominate._window_fallback", return_value=[alternative]
        ) as fallback, patch("pipeline.editorial.review_candidate", side_effect=review), patch(
            "pipeline.nominate._describe_candidate", side_effect=lambda model, candidate, segments: candidate
        ):
            result = nominate_candidates("model", "Topic", ["Goal"], segments, 20, 45, 60,
                                         target_audience="General public")
        fallback.assert_called_once()
        usable = [candidate for candidate in result if candidate["editorial_status"] != "rejected"]
        self.assertEqual([(candidate["start"], candidate["end"]) for candidate in usable], [(20, 40)])
        self.assertEqual(len({candidate["id"] for candidate in result}), len(result))

    def test_fallback_covers_full_source_with_complete_duration_bounded_sentences(self):
        from pipeline.nominate import _window_fallback

        segments = [{"start": start, "end": start + 10,
                     "text": "A sentence begins" if start % 20 == 0 else "and finishes here."}
                    for start in range(0, 1000, 10)]
        candidates = _window_fallback(segments, 20, 45)
        self.assertEqual(len(candidates), 12)
        self.assertEqual(candidates[0]["start"], 0)
        self.assertEqual(candidates[-1]["end"], 1000)
        self.assertTrue(all(candidate["end"] - candidate["start"] == 40 for candidate in candidates))
        self.assertTrue(all(candidate["quote"].endswith(".") for candidate in candidates))
        self.assertEqual(_window_fallback(segments[:1], 20, 45), [])

    def test_recovery_preserves_rejections_and_skips_usable_nominations(self):
        segments = [{"start": 0, "end": 20, "text": "First explanation."},
                    {"start": 20, "end": 40, "text": "Second explanation."}]
        response = {"message": {"content": json.dumps({"candidates": [{"start": 0, "end": 20}]})}}
        for status in ("estimated", "rejected"):
            with self.subTest(status=status), patch("pipeline.nominate.ollama.chat", return_value=response), patch(
                "pipeline.nominate._window_fallback", return_value=[{"start": 20, "end": 40, "source": "window"}]
            ) as fallback, patch(
                "pipeline.editorial.review_candidate", side_effect=lambda model, candidate, *args: {
                    **candidate, "editorial_status": status, "score": 80 if status == "estimated" else None}
            ), patch("pipeline.nominate._describe_candidate", side_effect=lambda model, candidate, segments: candidate):
                candidates = nominate_candidates("model", "Topic", ["Goal"], segments, 20, 45, 40,
                                                 target_audience="General public")
            ranked = rank_candidates(candidates, ["Goal"], segments, target_audience="General public")
            fallback.assert_called_once()
            if status == "rejected":
                self.assertEqual(ranked, [])
                self.assertTrue(all(candidate["editorial_status"] == "rejected" for candidate in candidates))
            else:
                self.assertEqual(len(ranked), 2)
                self.assertEqual([item.get("recovery") for item in candidates], [None, "top_up"])

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
                           selection_context={"target_audience": "General public", "audience_profile_version": "1.0"})
            with patch("pipeline.export.EXPORT_ROOT", root), patch("pipeline.export.media_duration", return_value=40), patch("pipeline.export.cut_clip"), patch("pipeline.export.assemble_compilation", side_effect=assemble):
                first = export_clips(**options)
                second = export_clips(**options)
                self.assertNotEqual(first[3], second[3])
                self.assertTrue(first[2].exists())
                self.assertTrue(second[2].exists())
                selection = json.loads(Path(first[3]).with_name("selection.json").read_text())
                self.assertEqual(selection["selection_context"]["target_audience"], "General public")
                with patch("pipeline.export.assemble_compilation", side_effect=RuntimeError("render failed")):
                    with self.assertRaisesRegex(RuntimeError, "render failed"):
                        export_clips(**options)
                self.assertEqual(Path(first[3]).read_bytes(), b"test video")
                self.assertEqual(previous.read_bytes(), b"previous export")
                self.assertEqual(list(previous.parent.glob(".pending-*")), [])


class AssessmentTests(unittest.TestCase):
    def objective_export(self, root):
        selection = {"aspect_ratio": "16:9", "selection_context": {"target_audience": "Kids"},
                     "clips": [{"id": "c1", "start": 10, "end": 65}],
                     "timeline": [{"kind": "clip", "clip_id": "c1", "start": 0, "duration": 55},
                                  {"kind": "ending", "start": 55, "duration": 5}],
                     "storyboard": {"opening": {"mode": "Overlay"}, "transitions": [], "ending": {"narration": False}}}
        root.mkdir(parents=True, exist_ok=True)
        (root / "compilation.mp4").write_bytes(b"test video")
        (root / "selection.json").write_text(json.dumps(selection))
        (root / "post.txt").write_text("Test post.")
        (root / "captions.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\nA short caption.\n")
        return selection

    def objective_command(self, command):
        if "-show_streams" in command:
            return SimpleNamespace(stdout=json.dumps({"format": {"duration": "60"}, "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}]}))
        if "-af" in command:
            return SimpleNamespace(stderr='FFmpeg logs\n{"input_i":"-18.0","input_tp":"-2.0"}\nTrailing logs')
        return SimpleNamespace(stdout="", stderr="")

    def test_objective_measurements_use_media_values_without_publication_approval(self):
        from pipeline.assess import measure_export

        with tempfile.TemporaryDirectory() as directory, patch("pipeline.assess.ffmpeg_bin", return_value="ffmpeg"), patch(
            "pipeline.assess.ffprobe_bin", return_value="ffprobe"
        ), patch("pipeline.assess.run_cmd", side_effect=self.objective_command):
            root = Path(directory)
            self.objective_export(root)
            report = measure_export(root)
        self.assertEqual(report["technical_status"], "passed")
        self.assertEqual(report["publication_status"], "not_assessed")
        self.assertEqual(report["counts"]["unavailable"], 0)
        self.assertEqual(report["selection_context"]["target_audience"], "Kids")
        speed = next(item for item in report["metrics"] if item["id"] == "caption_speed")
        self.assertEqual(speed["value"]["limit_characters_per_second"], 17)
        self.assertEqual(next(item for item in report["metrics"] if item["id"] == "loudness")["value"], -18)
        self.assertNotIn("reviewer", report)
        self.assertNotIn("overall_score", report)

    def test_caption_policy_uses_saved_export_audience(self):
        from pipeline.assess import measure_export

        with tempfile.TemporaryDirectory() as directory, patch("pipeline.assess.run_cmd", side_effect=self.objective_command):
            root = Path(directory)
            selection = self.objective_export(root)
            (root / "captions.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\n123456789012345678\n")
            for audience in ("Kids", "Researcher", "General public", None):
                with self.subTest(audience=audience):
                    selection["selection_context"] = {"target_audience": audience}
                    (root / "selection.json").write_text(json.dumps(selection))
                    report = measure_export(root)
                    speed = next(item for item in report["metrics"] if item["id"] == "caption_speed")
                    self.assertEqual(speed["status"], "fail" if audience in ("Kids", None) else "pass")

    def test_objective_assessment_reports_missing_audio_and_inconsistent_timeline(self):
        from pipeline.assess import measure_export, _timeline_metrics

        def command(arguments):
            result = self.objective_command(arguments)
            if "-show_streams" in arguments:
                probe = json.loads(result.stdout)
                probe["streams"].pop()
                result.stdout = json.dumps(probe)
            if "-af" in arguments:
                raise RuntimeError("No audio stream")
            return result

        with tempfile.TemporaryDirectory() as directory, patch("pipeline.assess.ffmpeg_bin", return_value="ffmpeg"), patch(
            "pipeline.assess.ffprobe_bin", return_value="ffprobe"
        ), patch("pipeline.assess.run_cmd", side_effect=command):
            root = Path(directory)
            selection = self.objective_export(root)
            report = measure_export(root)
            selection["timeline"][1]["start"] = 58
            self.assertEqual(_timeline_metrics(selection, 60)[0]["status"], "fail")
            self.assertEqual(_timeline_metrics({}, 60)[0]["status"], "unavailable")
        self.assertEqual(report["technical_status"], "failed")
        self.assertEqual(next(item for item in report["metrics"] if item["id"] == "streams")["status"], "fail")
        self.assertEqual(next(item for item in report["metrics"] if item["id"] == "audio_levels")["status"], "unavailable")

    def test_objective_cache_and_ui_are_bound_to_unchanged_artifacts(self):
        from pipeline.assess import assess_export_automatically, measure_export

        with tempfile.TemporaryDirectory() as directory, patch("app.EXPORT_ROOT", Path(directory)), patch(
            "pipeline.assess.ffmpeg_bin", return_value="ffmpeg"
        ), patch("pipeline.assess.ffprobe_bin", return_value="ffprobe"), patch(
            "pipeline.assess.run_cmd", side_effect=self.objective_command
        ), patch("pipeline.assess.measure_export", wraps=measure_export) as measure:
            root = Path(directory) / "run" / "export"
            self.objective_export(root)
            hashes = artifact_hashes(root)
            first = assess_export_automatically(root)
            self.assertEqual(assess_export_automatically(root), first)
            self.assertEqual(measure.call_count, 1)
            loaded = app.load_objective_assessment("run/export", progress=Mock())
            self.assertEqual(len(loaded), 5)
            self.assertIn("ALL CHECKS PASSED", loaded[3])
            self.assertIn("badge-pass", loaded[3])
            self.assertTrue(all(Path(path).is_file() for path in loaded[4]))
            self.assertEqual(artifact_hashes(root), hashes)
            (root / "post.txt").write_text("Changed post.")
            with self.assertRaisesRegex(ValueError, "changed"):
                assess_export_automatically(root, expected_hashes=hashes)
            with self.assertRaisesRegex(Exception, "changed"):
                app.rerun_objective_assessment(loaded[0], "run/export", progress=Mock())
            second = assess_export_automatically(root)
            self.assertNotEqual(first, second)
            self.assertEqual(measure.call_count, 2)
        self.assertEqual(app.load_objective_assessment(None), (None, None, "", "No export selected.", []))
        with self.assertRaisesRegex(Exception, "Select an export"):
            app.rerun_objective_assessment(None, "run/export")

    def test_objective_assessment_rejects_files_changed_during_measurement(self):
        from pipeline.assess import assess_export_automatically

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.objective_export(root)

            def change_artifact(export_dir):
                (export_dir / "post.txt").write_text("Modified during analysis.")
                return {}

            with patch("pipeline.assess.measure_export", side_effect=change_artifact):
                with self.assertRaisesRegex(ValueError, "changed during assessment"):
                    assess_export_automatically(root)
            self.assertFalse((root / "assessments").exists())

    def test_objective_caption_metrics_detect_bad_timing_and_reading_speed(self):
        from pipeline.assess import _caption_metrics

        good = "1\n00:00:01,000 --> 00:00:03,000\nA short caption.\n"
        metrics, cues = _caption_metrics(good, 45)
        self.assertEqual(len(cues), 1)
        self.assertTrue(all(metric["status"] == "pass" for metric in metrics))
        bad = good + "\n2\n00:00:02,000 --> 00:00:02,100\nThis text is too fast.\n"
        metrics, _ = _caption_metrics(bad, 2.5)
        outcomes = {metric["id"]: metric["status"] for metric in metrics}
        self.assertEqual(outcomes["caption_timing"], "fail")
        self.assertEqual(outcomes["caption_speed"], "fail")
        self.assertEqual(_caption_metrics("not SRT", 45)[0][0]["status"], "fail")
        self.assertEqual(_caption_metrics("", 45)[0][0]["status"], "fail")

    def test_caption_speed_uses_audience_boundaries_and_conservative_fallback(self):
        from pipeline.assess import _caption_metrics

        for audience, limit in (("Kids", 17), ("Researcher", 20), ("General public", 20),
                                (None, 17), ("Legacy audience", 17)):
            for length in (limit, limit + 1):
                with self.subTest(audience=audience, length=length):
                    captions = f"1\n00:00:01,000 --> 00:00:02,000\n{'A' * length}\n"
                    metrics, _ = _caption_metrics(captions, 45, audience)
                    speed = next(item for item in metrics if item["id"] == "caption_speed")
                    self.assertEqual(speed["status"], "pass" if length == limit else "fail")
                    self.assertEqual(speed["value"]["limit_characters_per_second"], limit)
                    self.assertEqual(speed["value"]["cues_above_limit"], int(length > limit))
                    if audience in (None, "Legacy audience"):
                        self.assertIn("conservative fallback", speed["threshold"])

    def test_caption_speed_counts_normalized_spaces_and_punctuation(self):
        from pipeline.assess import _caption_metrics, _friendly_value

        captions = "1\n00:00:01,000 --> 00:00:01,990\nA   short\ncaption!\n"
        metrics, _ = _caption_metrics(captions, 45, "Kids")
        speed = next(item for item in metrics if item["id"] == "caption_speed")
        self.assertEqual(speed["value"]["max_characters_per_second"], 16.16)
        self.assertEqual(speed["value"]["fastest_cue_text"], "A short caption!")
        metrics, _ = _caption_metrics(captions.replace("01,990", "01,940"), 45, "Kids")
        speed = next(item for item in metrics if item["id"] == "caption_speed")
        self.assertEqual(speed["status"], "fail")
        self.assertIn("17.02 characters/second", _friendly_value(speed))
        self.assertIn('Worst: "A short caption!"', _friendly_value(speed))

    def test_objective_cache_is_invalidated_when_metric_version_changes(self):
        from pipeline.assess import assess_export_automatically, measure_export

        with tempfile.TemporaryDirectory() as directory, patch("pipeline.assess.run_cmd", side_effect=self.objective_command), patch(
            "pipeline.assess.measure_export", wraps=measure_export
        ) as measure:
            root = Path(directory)
            self.objective_export(root)
            hashes = artifact_hashes(root)
            with patch("pipeline.assess.OBJECTIVE_VERSION", "2.1"):
                old_report = assess_export_automatically(root)
            new_report = assess_export_automatically(root)
            self.assertNotEqual(old_report, new_report)
            self.assertEqual(measure.call_count, 2)
            self.assertEqual(artifact_hashes(root), hashes)

    def test_objective_html_puts_failures_first_and_escapes_text(self):
        from pipeline.assess import objective_html

        metrics = [{"id": "a", "label": "Good check", "status": "pass", "value": 1, "threshold": "any"},
                   {"id": "b", "label": "Bad <check>", "status": "fail", "value": {"max_cps": 39.0}, "threshold": "<= 20"}]
        page = objective_html({"metric_version": "2.0", "technical_status": "failed", "selection_context": {},
                               "counts": {"pass": 1, "fail": 1, "unavailable": 0, "not_applicable": 0},
                               "metrics": metrics, "limitations": ["Scope note."]})
        self.assertIn("1 OF 2 CHECKS FAILED", page)
        self.assertIn("banner-fail", page)
        self.assertLess(page.index("Bad &lt;check&gt;"), page.index("Good check"))
        self.assertNotIn("<check>", page)
        self.assertIn("max cps: 39.0", page)

    def test_objective_html_explains_a_failed_caption_check_in_plain_words(self):
        from pipeline.assess import _caption_metrics, objective_html

        bad = "1\n00:00:01,000 --> 00:00:02,000\nThis caption is far too long to read.\n"
        metrics, _ = _caption_metrics(bad, 45)
        page = objective_html({"metric_version": "2.1", "technical_status": "failed", "selection_context": {},
                               "counts": {"pass": 2, "fail": 1, "unavailable": 0, "not_applicable": 0},
                               "metrics": metrics, "limitations": []})
        self.assertIn("Needs attention: Sidecar reading speed", page)
        self.assertIn("What to do:", page)
        self.assertIn("This caption is far too long to read.", page)
        self.assertIn("1 caption(s) too fast", page)
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
                "selection_context": {"target_audience": "General public", "audience_profile_version": "1.0"}
            }))
            review = self.review()
            review["artifact_sha256"] = artifact_hashes(export_dir)
            review["selection_context"] = {"target_audience": "Researcher"}
            destination = assess_export(export_dir, review)
            result = json.loads((destination / "assessment.json").read_text())
            self.assertEqual(result["selection_context"]["target_audience"], "General public")

    def test_assessment_tab_builds(self):
        demo = app.build_app()
        config = demo.get_config_file()
        labels = [component["props"].get("label") for component in config["components"]]
        self.assertIn("5. Assessment", labels)
        self.assertNotIn("Source fidelity (25%)", labels)
        self.assertNotIn("Human final review", labels)
        self.assertNotIn("Reviewer name (new human assessment)", labels)
        self.assertTrue(any(component["props"].get("value") == "Re-run checks" for component in config["components"]))
        self.assertIn("Target audience", labels)
        self.assertNotIn("Teaching topic", labels)
        self.assertFalse(any(label and label.startswith("Learning objectives") for label in labels))


if __name__ == "__main__":
    unittest.main()