from __future__ import annotations

from pipeline.editorial import _sentence_end, review_candidate
from pipeline.storyboard import AUDIENCE_GOALS, DURATION_BUDGET
from pipeline.utils import excerpt_for_range


def _windows(clip, segments, minimum, maximum):
    windows = []
    for first, segment in enumerate(segments):
        if segment["start"] < clip["start"] or segment["start"] >= clip["end"]:
            continue
        if first and not _sentence_end(segments[first - 1]["text"]):
            continue
        for last in range(first, len(segments)):
            end = segments[last]["end"]
            duration = round(end - segment["start"], 3)
            if end > clip["end"] or duration > maximum:
                break
            if duration >= minimum and _sentence_end(segments[last]["text"]):
                windows.append(dict(clip, start=round(float(segment["start"]), 3), end=round(float(end), 3)))
    unique = {(item["start"], item["end"]): item for item in windows}
    return sorted(unique.values(), key=lambda item: (abs(item["end"] - item["start"] - maximum), item["start"]))[:60]


def fit_selection(clips, segments, *, audience, model, source_duration, minimum=10, maximum=90,
                  lecture_context=None, overhead=None):
    if audience not in AUDIENCE_GOALS:
        raise ValueError("Choose Researcher, General public or Kids before fitting.")
    if not clips or not segments:
        raise ValueError("Select clips with a timestamped transcript before automatic fitting.")
    reserve = (16 if audience == "Kids" else 12) if overhead is None else overhead
    lower, upper = DURATION_BUDGET["min_seconds"], DURATION_BUDGET["max_seconds"]

    def total(items):
        return sum(item["end"] - item["start"] for item in items) + reserve + (len(items) - 1) * 3

    def priority(items):
        return (-len(items), abs(total(items) - (lower + upper) / 2), tuple((item["start"], item["end"]) for item in items))

    plans = [[]]
    for clip in clips:
        options = _windows(clip, segments, minimum, maximum)
        if clip.get("editorial_status") != "rejected" and minimum <= clip["end"] - clip["start"] <= maximum:
            options.insert(0, dict(clip))
        expanded = [plan for plan in plans]
        expanded.extend([*plan, option] for plan in plans for option in options
                        if total([*plan, option]) <= upper)
        plans = sorted(expanded, key=priority)[:2000]
    plans = sorted((plan for plan in plans if plan and lower <= total(plan) <= upper - 0.2
                    and sum(item["end"] - item["start"] for item in plan) + 9 + 1.5 * (len(plan) - 1) >= lower), key=priority)
    originals = {clip["id"]: clip for clip in clips}
    reviewed = {}
    for plan in plans:
        accepted = []
        for proposed in plan:
            original = originals[proposed["id"]]
            if (original["start"], original["end"]) == (proposed["start"], proposed["end"]):
                accepted.append(dict(proposed))
                continue
            key = (proposed["id"], proposed["start"], proposed["end"])
            if key not in reviewed:
                if len(reviewed) >= 12:
                    break
                checked = review_candidate(model, proposed, segments, audience, AUDIENCE_GOALS[audience],
                                           lecture_context or {}, minimum, maximum, source_duration)
                if (checked.get("editorial_status") != "estimated" or not checked.get("context_preserved")
                        or not checked.get("standalone") or (checked["start"], checked["end"]) != key[1:]):
                    reviewed[key] = None
                else:
                    excerpt = excerpt_for_range(segments, checked["start"], checked["end"])
                    checked.update(excerpt=excerpt, quote="", title=" ".join(excerpt.split()[:8]),
                                   takeaway=" ".join(excerpt.split()[:16]), metadata_status="range_edited")
                    reviewed[key] = checked
            if reviewed.get(key) is None:
                break
            accepted.append(dict(reviewed[key]))
        if len(accepted) != len(plan):
            continue
        report = {"original": [{key: clip[key] for key in ("id", "start", "end")} for clip in clips],
                  "fitted": [{key: clip[key] for key in ("id", "start", "end")} for clip in accepted],
                  "removed_ids": [clip["id"] for clip in clips if clip["id"] not in {item["id"] for item in accepted}],
                  "estimated_seconds": round(total(accepted), 3), "reviewed_windows": len(reviewed)}
        return accepted, report
    raise ValueError(f"No coherent selection fits {lower}-{upper} seconds including narration. Select more suitable complete ideas; no paid requests were made by fitting.")