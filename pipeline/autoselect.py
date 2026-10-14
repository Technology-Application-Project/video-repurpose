"""Automatic clip selection: picks candidates without human review and records that it did so."""
from __future__ import annotations

from datetime import datetime, timezone

from pipeline.nominate import PREFERRED_CLIPS
from pipeline.storyboard import DURATION_BUDGET
from pipeline.utils import MAX_KEEP

POLICY = ("ranked order; rejected clips excluded; up to {count} clips whose estimated runtime "
          "(speech + cards) stays inside the duration budget")


def estimated_seconds(lengths: list[float], audience: str | None) -> float:
    """Same planning estimate as fit_selection: speech, a card reserve and 3s per bridge."""
    reserve = 16 if audience == "Kids" else 12
    return sum(lengths) + reserve + 3 * max(0, len(lengths) - 1)


def auto_select(candidates: list[dict], audience: str | None, *, max_clips: int = PREFERRED_CLIPS) -> tuple[list[str], dict]:
    """Return (chosen ids in candidate order, audit record). Raises ValueError when nothing is usable."""
    max_clips = min(max_clips, MAX_KEEP)
    lower, upper = DURATION_BUDGET["min_seconds"], DURATION_BUDGET["max_seconds"]
    chosen, lengths, skipped = [], [], []
    for candidate in candidates:
        length = float(candidate["end"]) - float(candidate["start"])
        if candidate.get("editorial_status") == "rejected":
            skipped.append({"id": candidate["id"], "reason": "rejected by editorial review"})
        elif len(chosen) >= max_clips:
            skipped.append({"id": candidate["id"], "reason": f"already have {max_clips} clips"})
        elif estimated_seconds([*lengths, length], audience) > upper - 0.2:
            skipped.append({"id": candidate["id"], "reason": f"would exceed the {upper}s budget"})
        else:
            chosen.append(candidate)
            lengths.append(length)
    if not chosen:
        raise ValueError("no candidate fits the duration budget; adjust the clip limits and nominate again")
    warnings = []
    if estimated_seconds(lengths, audience) < lower:
        warnings.append(f"Estimated runtime is under the {lower}s minimum; the storyboard may need longer clips.")
    unscored = [item["id"] for item in chosen if item.get("editorial_status") != "estimated"]
    if unscored:
        warnings.append("No editorial score for " + ", ".join(unscored) + "; included on rank order only.")
    record = {"mode": "automatic", "human_review": False, "policy": POLICY.format(count=max_clips),
              "chosen_ids": [item["id"] for item in chosen],
              "ranges": [[item["id"], float(item["start"]), float(item["end"])] for item in chosen],
              "editorial": {item["id"]: {"status": item.get("editorial_status"), "score": item.get("score")} for item in chosen},
              "skipped": skipped, "warnings": warnings, "selected_at": datetime.now(timezone.utc).isoformat()}
    return record["chosen_ids"], record
