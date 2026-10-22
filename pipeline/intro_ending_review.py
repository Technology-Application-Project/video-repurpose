"""Independent, offline intro/ending review for Brevio Studio.

Rules mirror generation constraints in pipeline/storyboard.py; this module does
not alter generation, approval, or publishing. Scores are *heuristics*, not
empirical audience-retention predictions or factual-entailment results.
"""
from __future__ import annotations

import re
from typing import Any

AUDIENCES = {"Researcher", "General public", "Kids"}
STOP = frozenset("a an and are as at be by can did do for from has have how i in is it of on or our the this to we what when where which who why will with you your".split())
GENERIC_OPEN = re.compile(r"^(in this (video|lecture)|today (we|i)|this (video|lecture)|welcome|hello|let'?s (talk|learn))\b", re.I)
GENERIC_CTA = {"try it now", "learn more", "click here", "watch more", "subscribe now"}
QUESTION_AUDIENCES = {"General public", "Kids"}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.casefold())


def overlap(a: str, b: str) -> float:
    subject = set(words(a)) - STOP
    evidence = set(words(b)) - STOP
    return round(len(subject & evidence) / len(subject), 3) if subject else 0.0


def review_intro_ending(storyboard: dict[str, Any], audience: str, evidence: dict[str, str], *, has_source_url: bool = False) -> dict[str, Any]:
    """Review one existing storyboard, using selected clip ID -> excerpt text.

    Evidence must be selected excerpts, not a random transcript substring.
    Never asserts support just because shared words exist.
    """
    if audience not in AUDIENCES:
        raise ValueError(f"Unsupported audience: {audience}")
    opening = storyboard.get("opening") or {}
    ending = storyboard.get("ending") or {}
    fields = [
        ("opening.title", opening, "title", 7),
        ("opening.subtitle", opening, "subtitle", 6),
        ("ending.takeaway", ending, "takeaway", 16),
        ("ending.question", ending, "question", 12),
        ("ending.cta", ending, "cta", 8),
    ]
    results = []
    for label, section, key, limit in fields:
        content = str(section.get(key) or "").strip()
        reasons: list[str] = []
        if not content:
            reasons.append("missing_text")
        if len(content.split()) > limit:
            reasons.append("word_limit_exceeded")
        if label == "opening.title":
            if GENERIC_OPEN.match(content):
                reasons.append("generic_throat_clearing")
            if audience in QUESTION_AUDIENCES and not content.endswith("?"):
                reasons.append("question_hook_required")
        if label == "ending.cta":
            if has_source_url and content.casefold().strip(".!? ") in GENERIC_CTA:
                reasons.append("generic_cta")
            if not has_source_url and content != "Full lecture in description.":
                reasons.append("fixed_cta_required_without_source_url")
        # The reflection question and CTA are invitations; do not mistake them for
        # factual claims requiring direct transcript lexical overlap.
        requires_evidence = label in {"opening.title", "opening.subtitle", "ending.takeaway"}
        refs = [str(item) for item in section.get("evidence_ids", [])]
        snippets = [evidence[ref] for ref in refs if ref in evidence]
        if requires_evidence:
            if not refs or len(snippets) != len(refs):
                reasons.append("missing_or_unknown_evidence_id")
            lexical = max((overlap(content, snippet) for snippet in snippets), default=0.0)
            if lexical < 0.25 and snippets:
                reasons.append("weak_lexical_evidence_match")
        else:
            lexical = None
        results.append({"field": label, "text": content, "warnings": reasons,
                        "evidence_ids": refs if requires_evidence else [],
                        "lexical_overlap": lexical})
    # This check mirrors storyboard speech budget, not actual TTS measured timing.
    total_ending_words = sum(len(str(ending.get(k) or "").split()) for k in ("takeaway", "question", "cta"))
    duration = ending.get("seconds")
    try:
        duration = float(duration)
    except (ValueError, TypeError):
        duration = 0.0
    timing_warnings = []
    if duration < 4 or duration > 6:
        timing_warnings.append("ending_duration_outside_4_to_6_seconds")
    if total_ending_words > 28 or (duration > 0 and total_ending_words > 5 * duration):
        timing_warnings.append("ending_narration_too_dense")
    return {"schema_version": 1, "audience": audience,
            "result": "needs_review" if timing_warnings or any(item["warnings"] for item in results) else "precheck_pass",
            "fields": results, "timing_warnings": timing_warnings,
            "human_review_required": True,
            "limitations": "Offline rubric and lexical overlap only. Does not establish semantic faithfulness, factual correctness, audience retention, or human approval."}


def compare_opening_options(options: list[dict], audience: str, evidence: dict[str, str], *, has_source_url: bool = False) -> dict:
    """Stable option comparison; fewest rule warnings, then overlap, then input order.

    Candidates must already be generated by an authorised source. This code never
    contacts a provider or edits the active Brevio storyboard.
    """
    examined = []
    for i, option in enumerate(options):
        report = review_intro_ending(option, audience, evidence, has_source_url=has_source_url)
        warning_count = sum(len(f["warnings"]) for f in report["fields"]) + len(report["timing_warnings"])
        overlap_score = sum(float(f["lexical_overlap"] or 0) for f in report["fields"] if f["lexical_overlap"] is not None)
        examined.append({"option": i + 1, "warning_count": warning_count,
                         "overlap_sum": round(overlap_score, 3), "report": report})
    ranked = sorted(examined, key=lambda item: (item["warning_count"], -item["overlap_sum"], item["option"]))
    return {"ranking": [item["option"] for item in ranked], "options": examined,
            "note": "Heuristic comparison for review; not an automatic content approval."}
