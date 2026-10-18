"""Structure check of an exported compilation: its storyboard contract and, when recorded, its blueprint."""
from __future__ import annotations

import re

from pipeline.storyboard import DURATION_BUDGET, FIXED_CTA
from pipeline.utils import MAX_KEEP, MIN_KEEP

STRUCTURE_VERSION = "1.0"
VIDEO_TOLERANCE = 0.5
PLAN_TIMING_TOLERANCE = 10.0  # the plan uses card minimums; narration extends the cards
FOLLOW_OVERLAP = 0.3  # share of the exported key words that also appear in the plan
LOW_OVERLAP = 0.15  # below this the wording has clearly left the plan and needs review
_STOPWORDS = frozenset("""a an the and or but if so of to in on at by for with from as is are was were be been being do does did
can could will would should may might must have has had it its this that these those there here you your we our us they their
them i me my he she his her what why how when where who which whom than then too very just not no yes about into over out up
down more most some any each all also actually really""".split())
_SUFFIXES = ("iest", "ier", "ing", "ed", "es", "ly", "s", "y")
_STATUSES = ("pass", "fail", "review", "not_applicable", "unavailable")
_KIND_NAMES = {"opening": "Opening", "divider": "Bridge", "ending": "Ending"}


def _metric(identifier: str, label: str, status: str, threshold: str, value, checks: str, exported: str, fix: str = "") -> dict:
    """checks/exported/fix are the customer-facing wording; threshold and value stay as the technical record."""
    return {"id": identifier, "label": label, "status": status, "threshold": threshold, "value": value,
            "checks": checks, "exported": exported, "fix": fix if fix and status in {"fail", "review", "unavailable"} else None}


def _names(clips: list[dict]):
    titles = {clip["id"]: (clip.get("title") or clip["id"]) for clip in clips}
    return lambda identifier: titles.get(identifier, identifier)


def _sequence(kinds: list[str]) -> str:
    counts = {"clip": 0}
    parts = []
    for kind in kinds:
        if kind == "clip":
            counts["clip"] += 1
            parts.append(f"Clip {counts['clip']}")
        else:
            parts.append(_KIND_NAMES.get(kind, kind))
    return " → ".join(parts)


def _expected_kinds(storyboard: dict, count: int) -> list[str] | None:
    transitions = storyboard.get("transitions") or []
    if len(transitions) != max(0, count - 1):
        return None
    kinds = ["opening"] if storyboard["opening"].get("mode") == "Card" else []
    for index in range(count):
        if index and transitions[index - 1]["mode"] == "Divider":
            kinds.append("divider")
        kinds.append("clip")
    return [*kinds, "ending"]


def _bridge_problems(storyboard: dict, timeline: list[dict]) -> list[str]:
    by_clip = {item["before_clip_id"]: item for item in storyboard.get("transitions") or []}
    problems = []
    for index, item in enumerate(timeline):
        if item["kind"] != "divider":
            continue
        before = timeline[index - 1] if index else None
        after = timeline[index + 1] if index + 1 < len(timeline) else None
        if not (before and after and before["kind"] == "clip" and after["kind"] == "clip"):
            problems.append("a bridge is not between two clips")
            continue
        transition = by_clip.get(after["clip_id"])
        if transition is None or transition["mode"] != "Divider" or transition["label"] != item.get("text"):
            problems.append(f"the bridge before {after['clip_id']} differs from the storyboard")
    return problems


def _narration_problems(storyboard: dict, timeline: list[dict]) -> list[str]:
    by_clip = {item["before_clip_id"]: item for item in storyboard.get("transitions") or []}
    problems = []
    for index, item in enumerate(timeline):
        text = (item.get("narration") or {}).get("text")
        if item["kind"] == "opening" and storyboard["opening"].get("narration") and text != storyboard["opening"]["title"]:
            problems.append("opening narration differs from the opening title")
        elif item["kind"] == "divider":
            after = timeline[index + 1] if index + 1 < len(timeline) else {}
            transition = by_clip.get(after.get("clip_id"))
            if transition and transition.get("narration") and text != transition["label"]:
                problems.append(f"bridge narration differs from '{transition['label']}'")
        elif item["kind"] == "ending" and storyboard["ending"].get("narration"):
            ending = storyboard["ending"]
            if not text or ending["takeaway"].rstrip(".?! ") not in text or ending["cta"].rstrip(".?! ") not in text:
                problems.append("ending narration does not contain the takeaway and call to action")
    return problems


def _blueprint_metrics(plan: dict, clips: list[dict], total: float) -> list[dict]:
    name = _names([*({"id": item["id"], "title": item["title"]} for item in plan["clips"]), *clips])
    clip_ids = [clip["id"] for clip in clips]
    planned_ids = [item["id"] for item in plan["clips"]]
    planned_pairs = [[item["from_clip_id"], item["to_clip_id"]] for item in plan["bridges"]]
    pairs = [[a, b] for a, b in zip(clip_ids, clip_ids[1:])]
    timing = plan["timing"]
    lower, upper = timing["budget"]
    difference = round(total - timing["estimated_total"], 1)
    close = abs(difference) <= PLAN_TIMING_TOLERANCE and lower <= total <= upper
    review = "A difference is fine if you changed the clips on purpose; otherwise check the selection."
    join = lambda ids: ", ".join(name(item) for item in ids) or "none"
    arrows = lambda items: "; ".join(" → ".join(name(part) for part in pair) for pair in items) or "none"
    return [
        _metric("blueprint_clip_count", "Clip count matches the blueprint", "pass" if plan["clip_count"] == len(clip_ids) else "review",
                "The export uses the number of clips the blueprint planned.", {"planned": plan["clip_count"], "exported": len(clip_ids)},
                "The video uses the number of clips the blueprint planned.", f"Planned {plan['clip_count']} · exported {len(clip_ids)}", review),
        _metric("blueprint_clip_set", "Clips match the blueprint", "pass" if planned_ids == clip_ids else "review",
                "The same clips in the same order as planned; clips can change after fitting or manual edits.",
                {"planned": planned_ids, "exported": clip_ids},
                "The video plays the same clips, in the same order, as the blueprint.",
                f"Planned: {join(planned_ids)}. Exported: {join(clip_ids)}.", review),
        _metric("blueprint_bridges", "Bridges follow the blueprint", "pass" if planned_pairs == pairs else "review",
                "Each planned bridge joins the same two clips as exported.", {"planned": planned_pairs, "exported": pairs},
                "Each bridge joins the same two clips the blueprint planned.",
                f"Planned: {arrows(planned_pairs)}. Exported: {arrows(pairs)}.", review),
        _metric("blueprint_timing", "Length is close to the blueprint estimate", "pass" if close else "review",
                f"Within {PLAN_TIMING_TOLERANCE:.0f}s of the estimate and inside {lower}-{upper}s.",
                {"estimated": timing["estimated_total"], "exported": round(total, 1), "difference": difference},
                f"The video is within {PLAN_TIMING_TOLERANCE:.0f} seconds of the blueprint's estimate and inside {lower}-{upper} seconds.",
                f"Estimated {timing['estimated_total']:.1f} s · exported {total:.1f} s ({difference:+.1f} s)",
                "Narration usually makes the cards a little longer than the estimate."),
    ]


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + 2:
            word = word[:-len(suffix)]
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


def _keywords(text: str) -> set[str]:
    return {_stem(word) for word in re.findall(r"[a-z]+", text.lower()) if len(word) > 2 and word not in _STOPWORDS}


def _overlap(planned: str, actual: str) -> float | None:
    """Share of the exported key words found in the plan; the exported copy is the shortened one."""
    wanted = _keywords(actual)
    return len(wanted & _keywords(planned)) / len(wanted) if wanted else None


def _plan_rows(plan: dict, storyboard: dict, clips: list[dict]) -> list[dict]:
    titles = {clip["id"]: clip.get("title", "") for clip in clips}
    labels = {item["before_clip_id"]: item["label"] for item in storyboard.get("transitions") or []}
    opening = storyboard["opening"]
    rows = [{"section": f"Clip {index + 1}", "planned": f"{item['role']}: {item['title']}",
             "actual": titles.get(item["id"], "Not in the export")} for index, item in enumerate(plan["clips"])]
    rows.append({"section": "Opening", "planned": plan["opening"]["idea"],
                 "actual": " · ".join(part for part in (opening["title"], opening.get("subtitle")) if part)})
    rows += [{"section": f"Bridge {index + 1}", "planned": item["idea"], "actual": labels.get(item["to_clip_id"], "No bridge in the export")}
             for index, item in enumerate(plan["bridges"])]
    cta = storyboard["ending"]["cta"]
    rows += [{"section": "Ending", "planned": plan["ending"]["synthesis"], "actual": storyboard["ending"]["takeaway"]},
             # The fixed CTA is never planned, so plans made before that was recorded must not look mismatched.
             {"section": "Ending action", "planned": cta if cta == FIXED_CTA else plan["ending"]["action"], "actual": cta}]
    for row in rows:
        worded = row["section"].split()[0] in {"Opening", "Bridge", "Ending"} and row["section"] != "Ending action"
        share = _overlap(row["planned"], row["actual"]) if worded and plan["generated"]["status"] == "model_plan" else None
        row["overlap"] = None if share is None else round(share, 2)
        row["match"] = None if share is None else "follows" if share >= FOLLOW_OVERLAP else "differs"
    return rows


def _wording_metrics(rows: list[dict]) -> list[dict]:
    checked = [row for row in rows if row["match"]]
    if not checked:
        return []
    differs = [row["section"] for row in checked if row["match"] == "differs"]
    clear = [row["section"] for row in checked if row["overlap"] < LOW_OVERLAP]
    return [_metric("blueprint_wording", "Wording follows the blueprint", "review" if clear else "pass",
                    f"Key words of the opening, bridges and ending found in the plan: at least {FOLLOW_OVERLAP:.0%} to follow it, below {LOW_OVERLAP:.0%} to review.",
                    {row["section"]: row["overlap"] for row in checked},
                    "The opening, bridges and ending reuse the key ideas of the plan. Wording is shortened on purpose, so only clear drift is flagged.",
                    ("The opening, bridges and ending all follow the plan" if not differs
                     else f"Differs from the plan: {', '.join(differs)}. The rest follow it."),
                    "Compare the wording below; regenerate the preview if the story drifted from the plan.")]


def structure_check(manifest: dict, video_seconds: float | None = None) -> dict:
    storyboard, timeline = manifest.get("storyboard"), manifest.get("timeline")
    if not storyboard or not timeline:
        return {"version": STRUCTURE_VERSION, "status": "unavailable", "video_seconds": video_seconds, "metrics": [],
                "counts": {key: 0 for key in _STATUSES}, "blueprint": None,
                "note": "This export has no storyboard timeline, so its structure cannot be checked."}
    clips = manifest.get("clips") or []
    name = _names(clips)
    clip_ids = [clip["id"] for clip in clips]
    timeline_clips = [item["clip_id"] for item in timeline if item["kind"] == "clip"]
    kinds = [item["kind"] for item in timeline]
    total = sum(float(item.get("duration", 0)) for item in timeline)
    budget = storyboard.get("duration_budget") or DURATION_BUDGET
    evidence = {item["clip_id"] for item in storyboard.get("evidence", [])}
    cited = {cid for section in (storyboard["opening"], storyboard["ending"], *(storyboard.get("transitions") or []))
             for cid in section.get("evidence_ids", [])}
    narrated = bool(storyboard["opening"].get("narration") or storyboard["ending"].get("narration")
                    or any(item.get("narration") for item in storyboard.get("transitions") or []))
    bridges, narration = _bridge_problems(storyboard, timeline), _narration_problems(storyboard, timeline)
    expected = _expected_kinds(storyboard, len(clips))
    bridge_count = kinds.count("divider")
    again = "Regenerate the preview, review it, and export again."
    metrics = [
        _metric("clip_count", "Clip count is allowed", "pass" if MIN_KEEP <= len(clips) <= MAX_KEEP else "fail",
                f"{MIN_KEEP}-{MAX_KEEP} clips.", {"clips": len(clips)},
                f"The video uses between {MIN_KEEP} and {MAX_KEEP} clips.", f"{len(clips)} clip{'s' if len(clips) != 1 else ''}",
                f"Select {MIN_KEEP}-{MAX_KEEP} clips and export again."),
        _metric("section_order", "Opening, clips, bridges and ending are in order", "pass" if kinds == expected else "fail",
                "Opening first, a bridge between every pair of clips, ending last.", {"expected": expected, "exported": kinds},
                "The video starts with the opening card, has a bridge between each pair of clips and finishes with the ending card.",
                _sequence(kinds), again),
        _metric("clip_order", "Clips keep the approved order", "pass" if timeline_clips == clip_ids == storyboard.get("clip_ids") else "fail",
                "The exported clips match the approved selection and the storyboard.",
                {"approved": clip_ids, "storyboard": storyboard.get("clip_ids"), "exported": timeline_clips},
                "The clips play in the order you approved.", " → ".join(name(item) for item in timeline_clips) or "No clips found",
                "Export again from the approved selection."),
        _metric("bridges_match", "Bridges sit between their clips with the approved label", "pass" if not bridges else "fail",
                "Every bridge joins the clips the storyboard names and carries its label.", bridges or "matches",
                "Each bridge sits between the two clips it introduces and shows the approved wording.",
                (f"All {bridge_count} bridge{'s' if bridge_count != 1 else ''} match" if bridge_count else "No bridges (one clip)") if not bridges
                else "; ".join(bridges), again),
        _metric("narration_copy", "Card narration matches the storyboard", ("pass" if not narration else "fail") if narrated else "not_applicable",
                "Opening, bridge and ending narration use the approved wording.", (narration or "matches") if narrated else None,
                "The narrator reads the approved opening, bridge and ending wording.",
                ("The narration matches on every card" if not narration else "; ".join(narration)) if narrated else "No narrated cards in this export",
                again),
        _metric("evidence_coverage", "Every clip is cited as source evidence", "pass" if evidence == set(clip_ids) and cited <= set(clip_ids) else "fail",
                "Each exported clip has evidence and the cards cite only exported clips.",
                {"evidence": sorted(evidence), "cited_by_cards": sorted(cited)},
                "Every clip is backed by a source excerpt, and the cards only refer to clips that are in the video.",
                f"{len(evidence & set(clip_ids))} of {len(clip_ids)} clips have source evidence", again),
        _metric("duration_budget", "Length is inside the duration budget",
                "pass" if budget["min_seconds"] <= total <= budget["max_seconds"] else "fail",
                f"{budget['min_seconds']}-{budget['max_seconds']} seconds.", {"seconds": round(total, 1)},
                f"The video is between {budget['min_seconds']} and {budget['max_seconds']} seconds long.", f"{total:.1f} seconds",
                "Choose different clips or shorter cards, then export again."),
        _metric("video_matches_timeline", "Video length matches the timeline",
                "unavailable" if video_seconds is None else "pass" if abs(video_seconds - total) <= VIDEO_TOLERANCE else "fail",
                f"Within {VIDEO_TOLERANCE}s of the recorded timeline.", {"video_seconds": video_seconds, "timeline_seconds": round(total, 2)},
                "The finished video file is as long as its recorded plan says.",
                "The video length could not be measured" if video_seconds is None
                else f"Video {video_seconds:.1f} s · recorded plan {total:.1f} s",
                "Export again; the video file may be damaged or incomplete."),
    ]
    plan = manifest.get("blueprint")
    rows = _plan_rows(plan, storyboard, clips) if plan else []
    metrics += [*_blueprint_metrics(plan, clips, total), *_wording_metrics(rows)] if plan else [
        _metric("blueprint_recorded", "Blueprint recorded", "not_applicable",
                "Exports made after blueprints were added record the plan.", "No blueprint in this export.",
                "The export keeps the plan it was built from, so it can be compared with the result.",
                "No blueprint was saved with this export (it was made before blueprints existed, or planning was unavailable).")]
    counts = {key: sum(item["status"] == key for item in metrics) for key in _STATUSES}
    return {"version": STRUCTURE_VERSION, "status": "passed" if not counts["fail"] and not counts["review"] else "needs_attention",
            "video_seconds": video_seconds, "metrics": metrics, "counts": counts, "note": "",
            "blueprint": ({"status": plan["generated"]["status"], "revision": plan.get("revision"),
                           "rows": rows} if plan else None)}
