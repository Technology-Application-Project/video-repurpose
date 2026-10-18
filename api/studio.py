import copy
import json
import threading
import uuid
from pathlib import Path

from pipeline import workflow
from pipeline.project import atomic_json, file_hash, load_checkpoint, verify_source


class Studio:
    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.RLock()
        self.sessions = {}
        for path in (directory / "sessions").glob("*.json"):
            try:
                record = json.loads(path.read_text())
                state = record.get("state") or {}
                source = Path(record["source"]).resolve()
                if not source.is_relative_to(directory.resolve().parent.parent) or not source.is_file():
                    continue
                if state.get("source_sha256"):
                    state = load_checkpoint(state["run_id"], directory.parent)
                elif record.get("source_sha256") != file_hash(source):
                    continue
                state.update(confirmed=False, approval_revision=None, storyboard_approval=None,
                             storyboard_preview_revision=None, cloud_batch=None)
                record["state"] = state
                record["revision"] += 1
                if path.stem != record["id"]:
                    continue
                self.save(record)
            except (ValueError, KeyError, OSError):
                continue

    def save(self, record):
        with self.lock:
            self.sessions[record["id"]] = record
            atomic_json(self.directory / "sessions" / f"{record['id']}.json", record)

    def add(self, source, filename, duration):
        record = {"id": uuid.uuid4().hex, "revision": 0, "filename": filename, "source": str(source),
                  "duration": duration, "state": {}, "export_id": None, "source_sha256": file_hash(source)}
        self.save(record)
        return record

    def get(self, identity):
        with self.lock:
            if identity not in self.sessions:
                raise KeyError("Run not found")
            return copy.deepcopy(self.sessions[identity])

    def update(self, identity, state, **values):
        with self.lock:
            record = self.get(identity)
            record.update(values, state=state, revision=record["revision"] + 1)
            if state.get("source_video"):
                record["source"] = state["source_video"]
                record["source_sha256"] = state.get("source_sha256")
            self.save(record)
            return record

    def snapshot(self, record):
        state = record.get("state") or {}
        draft = state.get("storyboard_draft") or {}
        clips = state.get("candidates") or []
        approved = {clip["id"]: clip for clip in state.get("approved", [])}
        visible = [approved.get(clip["id"], clip) for clip in clips]
        for clip in approved.values():
            if clip["id"] not in {item["id"] for item in visible}:
                visible.append(clip)
        identity = record["id"]
        preview = state.get("storyboard_preview_path")
        return {"id": identity, "revision": record["revision"], "filename": record["filename"],
                "duration": record["duration"], "source": f"/api/runs/{identity}/media/source",
                "audience": state.get("target_audience"), "aspect": state.get("aspect_ratio", "16:9"),
                "options": {"audience": state.get("target_audience", "General public"),
                            "aspect": state.get("aspect_ratio", "16:9"), "model": state.get("model", "qwen2.5:latest"),
                            "whisper_model": ((state.get("transcript") or {}).get("asr") or {}).get("model", "small"),
                            "min_seconds": state.get("min_s", 6), "max_seconds": state.get("max_s", 15),
                            "source_url": state.get("source_url", ""),
                            "automatic": (state.get("selection") or {}).get("mode", "automatic") == "automatic"},
                "analyzed": bool(state.get("transcript")), "confirmed": bool(state.get("confirmed")),
                "clips": [{key: clip.get(key) for key in ("id", "title", "takeaway", "start", "end", "excerpt", "score", "editorial_status", "editorial_reason")}
                          for clip in visible], "selected": list(approved),
                "segments": (state.get("transcript") or {}).get("segments", []),
                "preview": f"/api/runs/{identity}/media/preview?v={record['revision']}" if preview and Path(preview).is_file() else None,
                "preview_current": bool(state.get("storyboard_preview_revision")),
                "approved": bool(state.get("storyboard_approval")),
                "reviewer": (state.get("storyboard_approval") or {}).get("reviewer"),
                "timeline": workflow.timeline(state), "export_id": record.get("export_id"),
                "storyboard": {key: draft.get(key) for key in ("opening", "ending", "transitions", "evidence")},
                "fit_report": state.get("fit_report"), "selection_mode": (state.get("selection") or {}).get("mode"),
                "blueprint": state.get("blueprint")}

    def validate(self, record, revision):
        if revision != record["revision"]:
            raise ValueError("This run changed. Refresh before continuing.")
        state = record.get("state") or {}
        if state.get("source_sha256"):
            verify_source(state)
        elif record.get("source_sha256") != file_hash(record["source"]):
            raise ValueError("Source file changed. Start a new project.")