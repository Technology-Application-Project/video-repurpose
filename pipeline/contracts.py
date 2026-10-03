from __future__ import annotations

import hashlib
import json
import math


def validate_range(start: float, end: float, duration: float) -> None:
    if not all(math.isfinite(value) for value in (start, end, duration)):
        raise ValueError("Clip times and source duration must be finite.")
    if not 0 <= start < end <= duration:
        raise ValueError("Clip times must satisfy 0 <= start < end <= source duration.")


def selection_revision(state: dict, clips: list[dict]) -> str:
    payload = {
        "run_id": state.get("run_id"),
        "source_video": state.get("source_video"),
        "transcript": state.get("transcript"),
        "topic": state.get("topic"),
        "objectives": state.get("objectives"),
        "target_audience": state.get("target_audience"),
        "audience_profile_version": state.get("audience_profile_version"),
        "lecture_context": state.get("lecture_context"),
        "clips": [
            {key: clip.get(key) for key in ("id", "start", "end", "title", "takeaway", "excerpt")}
            for clip in clips
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()