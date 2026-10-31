import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app


class StudioApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = self.root / "exports" / "run" / "version"
        self.folder.mkdir(parents=True)
        (self.folder / "selection.json").write_text(json.dumps({"intro_title": "Education matters", "clips": [],
            "selection_context": {"target_audience": "Kids"}, "timeline": []}))
        (self.folder / "compilation.mp4").write_bytes(b"0123456789")
        (self.folder / "post.txt").write_text("A source-grounded post.")
        self.client = TestClient(create_app(self.root), base_url="http://127.0.0.1")
        self.addCleanup(self.client.app.state.jobs.executor.shutdown, wait=True)
        self.addCleanup(self.client.close)
        self.identity = self.client.get("/api/exports").json()["exports"][0]["id"]

    def test_catalog_and_byte_range(self):
        detail = self.client.get(f"/api/exports/{self.identity}").json()
        self.assertEqual(detail["audience"], "Kids")
        self.assertFalse(detail["approved"])
        response = self.client.get(detail["video"], headers={"Range": "bytes=2-5"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"2345")

    def test_files_are_allowlisted_and_symlinks_cannot_escape(self):
        secret = self.root / "private.txt"
        secret.write_text("private")
        self.assertEqual(self.client.get(f"/api/exports/{self.identity}/files/selection.json").status_code, 404)
        (self.folder / "post.txt").unlink()
        (self.folder / "post.txt").symlink_to(secret)
        with self.assertRaises(ValueError):
            self.client.app.state.catalog.file(self.folder, "post.txt")

    def test_untrusted_host_is_rejected(self):
        self.assertEqual(self.client.get("/api/exports", headers={"Host": "evil.example"}).status_code, 400)

    def headers(self):
        return {"X-Studio-Token": self.client.get("/api/config").json()["token"]}

    def test_cross_origin_and_missing_token_rejected(self):
        self.assertEqual(self.client.post("/api/runs").status_code, 403)
        self.assertEqual(self.client.get("/api/config", headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_failed_generation_invalidates_approval_and_is_idempotent(self):
        studio = self.client.app.state.studio
        current = studio.add(self.folder / "compilation.mp4", "lecture.mp4", 45)
        current = studio.update(current["id"], {"storyboard_approval": {"kind": "human"}, "storyboard_preview_revision": "old"})
        body = {"revision": current["revision"], "key": "test-request-1"}
        with patch("api.main.workflow.generate", side_effect=ValueError("Test failure")) as generate:
            first = self.client.post(f"/api/runs/{current['id']}/preview", json=body, headers=self.headers())
            self.client.app.state.jobs.executor.shutdown(wait=True)
            second = self.client.post(f"/api/runs/{current['id']}/preview", json=body, headers=self.headers())
            self.assertEqual(first.json()["id"], second.json()["id"])
            generate.assert_called_once()
        restored = self.client.get(f"/api/runs/{current['id']}").json()
        self.assertFalse(restored["approved"])
        self.assertFalse(restored["preview_current"])
        conflicting = self.client.post(f"/api/runs/{current['id']}/preview", json={**body, "revision": 99}, headers=self.headers())
        self.assertEqual(conflicting.status_code, 409)

    def test_stale_revision_and_invalid_upload(self):
        studio = self.client.app.state.studio
        current = studio.add(self.folder / "compilation.mp4", "lecture.mp4", 45)
        response = self.client.post(f"/api/runs/{current['id']}/preview", json={"revision": 5, "key": "stale-request"}, headers=self.headers())
        self.assertEqual(response.status_code, 409)
        invalid = self.client.post("/api/runs", files={"file": ("bad.mp4", b"not a video", "video/mp4")}, headers=self.headers())
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(list((studio.directory / "uploads").glob("*")), [])

    def test_restart_validates_source(self):
        from api.studio import Studio
        studio = self.client.app.state.studio
        current = studio.add(self.folder / "compilation.mp4", "lecture.mp4", 45)
        restored = Studio(studio.directory).get(current["id"])
        self.assertGreater(restored["revision"], current["revision"])
        (self.folder / "compilation.mp4").write_bytes(b"changed")
        self.assertEqual(Studio(studio.directory).sessions, {})

    def test_adapter_preserves_approval_and_export_contract(self):
        import app
        from pipeline import workflow
        from test_pipeline import StoryboardTests
        state, values, rows = StoryboardTests().package()
        self.assertEqual(workflow.controls(state), values)
        self.assertEqual(len(workflow.rows(state["approved"])), len(rows))
        with self.assertRaisesRegex(Exception, "Preview"):
            workflow.approve(state, "Test reviewer", True)
        _, revision = app._read_storyboard(state, values)
        state["storyboard_preview_revision"] = revision
        with self.assertRaisesRegex(Exception, "named human"):
            workflow.approve(state, "", True)
        approved = workflow.approve(state, "Test reviewer", True)
        with patch("app.export_clips", return_value=([], "Post", self.folder / "bundle.zip", str(self.folder / "compilation.mp4"))) as export:
            _, destination = workflow.export(approved, lambda *args, **kwargs: None)
            self.assertEqual(destination, self.folder)
            self.assertEqual(export.call_args.kwargs["storyboard_approval"]["revision"], revision)
        changed = workflow.select(approved, [{**state["approved"][0], "title": "Changed title"}])
        self.assertIsNone(changed["storyboard_approval"])
        self.assertIsNone(changed["storyboard_draft"])
        with self.assertRaisesRegex(ValueError, "named human"):
            workflow.export(changed, lambda *args, **kwargs: None)

    def test_analyze_job_uses_existing_workflow_and_records_result(self):
        import app
        from pipeline.project import file_hash
        studio = self.client.app.state.studio
        source = self.root / "source.mp4"
        source.write_bytes(b"fixture-video")
        current = studio.add(source, source.name, 120)
        directory = self.root / "work" / "analyzed-run"
        directory.mkdir()
        copied = directory / "source.mp4"
        copied.write_bytes(source.read_bytes())
        state = app.empty_state()
        state.update(run_id="analyzed-run", work=str(directory), source_video=str(copied),
                     source_duration=120, source_sha256=file_hash(copied), transcript={"segments": []})
        with patch("app.transcribe_for_audience", return_value=(state,)) as transcribe, patch(
            "app.nominate_for_audience", side_effect=lambda state, *args, **kwargs: (state,)
        ) as nominate:
            response = self.client.post(f"/api/runs/{current['id']}/analyze", headers=self.headers(),
                json={"revision": 0, "key": "analyze-once", "options": {"audience": "Kids"}})
            self.assertEqual(response.status_code, 200)
            self.client.app.state.jobs.executor.submit(lambda: None).result(timeout=10)
            job = self.client.get(f"/api/jobs/{response.json()['id']}").json()
            self.assertEqual(job["status"], "succeeded", job.get("message"))
            self.assertEqual(transcribe.call_args.args[1], "Kids")
            nominate.assert_called_once()
        restored = studio.get(current["id"])
        self.assertEqual(restored["source"], str(copied))
        self.assertTrue((directory / "run.json").is_file())

    def test_restart_marks_pending_jobs_interrupted(self):
        from api.jobs import Jobs
        from pipeline.project import atomic_json
        directory = self.root / "work" / "interrupted-jobs"
        atomic_json(directory / "pending.json", {"id": "pending", "status": "running"})
        jobs = Jobs(directory)
        self.addCleanup(jobs.executor.shutdown, wait=True)
        self.assertEqual(jobs.items["pending"]["status"], "interrupted")