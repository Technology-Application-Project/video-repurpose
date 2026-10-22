"""Deterministic WhisperX timestamp/word diagnostics; no silent transcript rewriting."""
from __future__ import annotations

import math


def inspect_transcript(segments: list[dict]) -> dict:
    """Return reproducible diagnostics. Severe errors make transcripts unusable.

    Small inter-segment overlaps are warnings, not silently deduplicated: consecutive
    speech, self-correction and overlapping speakers must remain in the source.
    """
    errors, warnings = [], []
    if not isinstance(segments, list) or not segments:
        return {"version": 1, "valid": False, "errors": ["Transcript contains no segments."],
                "warnings": [], "segment_count": 0}
    previous_start = -1.0
    previous_end = -1.0
    for index, segment in enumerate(segments):
        loc = f"segment {index + 1}"
        if not isinstance(segment, dict):
            errors.append(f"{loc}: not an object")
            continue
        try:
            start, end = float(segment["start"]), float(segment["end"])
        except (TypeError, ValueError, KeyError, OverflowError):
            errors.append(f"{loc}: timestamps missing or non-numeric")
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            errors.append(f"{loc}: invalid time range")
            continue
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            errors.append(f"{loc}: empty text")
        if start < previous_start:
            errors.append(f"{loc}: segments are out of time order")
        elif previous_end > start + 0.2:
            warnings.append(f"{loc}: overlaps previous segment by {previous_end - start:.2f}s")
        if end - start > 45:
            warnings.append(f"{loc}: unusually long segment ({end - start:.1f}s)")
        previous_start, previous_end = start, max(previous_end, end)
        for j, word in enumerate(segment.get("words") or []):
            try:
                ws, we = float(word["start"]), float(word["end"])
                valid_word = math.isfinite(ws) and math.isfinite(we) and ws >= start - 0.25 and we <= end + 0.25 and we > ws
            except (TypeError, ValueError, KeyError, OverflowError):
                valid_word = False
            if not valid_word:
                warnings.append(f"{loc}: word {j+1} has invalid/out-of-range timing")
    return {"version": 1, "valid": not errors, "errors": errors, "warnings": warnings,
            "segment_count": len(segments)}
