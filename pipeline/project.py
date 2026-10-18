from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pipeline.utils import WORK_ROOT, to_jsonable


def file_hash(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def data_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".pending", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(to_jsonable(value), stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify_source(state: dict) -> None:
    expected = state.get("source_sha256")
    if expected and file_hash(state["source_video"]) != expected:
        raise ValueError("Source file changed. Start a new run; previous reviews cannot be reused.")


def save_checkpoint(state: dict) -> None:
    if not state.get("source_sha256") or not state.get("work"):
        return
    payload = dict(state)
    payload["transcript_sha256"] = data_hash(payload.get("transcript"))
    payload["saved_at"] = datetime.now(timezone.utc).isoformat()
    atomic_json(Path(state["work"]) / "run.json", {"version": 1, "state": payload})


def load_checkpoint(run_id: str, root: Path = WORK_ROOT) -> dict:
    directory = (root / run_id).resolve()
    if directory.parent != root.resolve():
        raise ValueError("Select a saved run from the list.")
    manifest = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    if manifest.get("version") != 1:
        raise ValueError("Unsupported run version.")
    state = manifest["state"]
    if Path(state["work"]).resolve() != directory or Path(state["source_video"]).resolve().parent != directory:
        raise ValueError("Run paths do not match the saved project.")
    if state.get("run_id") != run_id or not state.get("source_sha256"):
        raise ValueError("Invalid run identity or missing source hash.")
    verify_source(state)
    if data_hash(state.get("transcript")) != state.get("transcript_sha256"):
        raise ValueError("Saved transcript changed. Import a reviewed transcript instead.")
    for candidate in state.get("candidates", []):
        path = candidate.get("preview_path")
        if path and (not Path(path).resolve().is_relative_to(directory) or not Path(path).is_file()):
            candidate.pop("preview_path", None)
    state.update(confirmed=False, approval_revision=None, storyboard_approval=None,
                 storyboard_preview_revision=None, cloud_batch=None)
    from pipeline.storyboard import AUDIENCE_PROFILE_VERSION, DURATION_BUDGET
    if state.get("audience_profile_version") != AUDIENCE_PROFILE_VERSION:
        state.update(storyboard_draft=None, blueprint=None, candidates=[], approved=[], fit_report=None,
                     audience_migration_required=True, duration_budget=dict(DURATION_BUDGET))
    return state