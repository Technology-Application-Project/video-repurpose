"""Source-grounding audit for storyboard text (not a factual truth oracle).

This conservative, deterministic pre-review report detects evidence mistakes and
common changes in certainty/negation. Similarity helps locate evidence; it never
proves entailment. Human review is always required before publication.
"""
from __future__ import annotations

import math
import re
from typing import Any

_STOP = frozenset("a an the is are was were be to of in on for with and or that this these those it its by from as at you your we our do does did have has had what how why can could would should".split())
_CERTAIN = frozenset("always never all every guarantees guaranteed proves proven definitely must impossible ensures".split())
_UNCERTAIN = frozenset("may might sometimes some suggests suggest possibly perhaps potentially could often likely".split())
_NEGATION = frozenset("not no never without cannot can't doesn't don't isn't aren't won't".split())
_TOKEN = re.compile(r"[\w]+(?:'[\w]+)?", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [w.casefold() for w in _TOKEN.findall(text)]


def _overlap(claim: str, evidence: str) -> float:
    a = {w for w in _tokens(claim) if w not in _STOP and len(w) > 2}
    b = {w for w in _tokens(evidence) if w not in _STOP and len(w) > 2}
    return len(a & b) / len(a) if a else 0.0


def audit_storyboard(storyboard: dict, clips: list[dict], segments: list[dict]) -> dict:
    """Audit title, subtitle, transition labels and ending takeaway.

    Evidence is restricted to declared clip IDs and timestamp-overlapping speech.
    Generated questions and generic CTA are deliberately not treated as facts.
    Returns serializable structured evidence and review reasons; never auto-approves.
    """
    by_id = {str(c["id"]): c for c in clips}
    checks: list[dict[str, Any]] = []
    entries = [("opening.title", storyboard.get("opening", {}), "title"),
               ("opening.subtitle", storyboard.get("opening", {}), "subtitle")]
    entries += [(f"transition.{i}.label", item, "label")
                for i, item in enumerate(storyboard.get("transitions") or [])]
    entries.append(("ending.takeaway", storyboard.get("ending", {}), "takeaway"))
    for field, section, key in entries:
        claim = str(section.get(key) or "").strip()
        if not claim:
            continue
        allowed = [str(value) for value in section.get("evidence_ids") or []]
        valid = [by_id[ref] for ref in allowed if ref in by_id]
        evidence = []
        for clip in valid:
            try:
                start, end = float(clip["start"]), float(clip["end"])
                if not math.isfinite(start) or not math.isfinite(end) or start >= end:
                    continue
            except (TypeError, ValueError, KeyError):
                continue
            matched = [seg for seg in segments
                       if isinstance(seg, dict) and seg.get("text")
                       and float(seg["start"]) < end and float(seg["end"]) > start]
            quote = " ".join(str(s["text"]).strip() for s in matched)
            if quote:
                evidence.append({"clip_id": str(clip["id"]), "start": start, "end": end,
                                 "quote": quote[:3000], "overlap": round(_overlap(claim, quote), 3)})
        evidence.sort(key=lambda x: x["overlap"], reverse=True)
        reasons = []
        if len(valid) != len(allowed) or not valid:
            reasons.append("missing_or_invalid_evidence_ids")
        if not evidence:
            reasons.append("no_transcript_in_evidence_range")
        else:
            candidate = evidence[0]["quote"]
            ct, et = set(_tokens(claim)), set(_tokens(candidate))
            if _CERTAIN.intersection(ct) and _UNCERTAIN.intersection(et) and not _CERTAIN.intersection(et):
                reasons.append("possible_overstatement_of_certainty")
            # Negation mismatches are a warning requiring human review, not a contradiction verdict.
            if bool(ct & _NEGATION) != bool(et & _NEGATION):
                reasons.append("possible_negation_mismatch")
            if evidence[0]["overlap"] < 0.3:
                reasons.append("low_lexical_evidence_overlap")
        checks.append({"field": field, "claim": claim, "status": "needs_review" if reasons else "evidence_candidate_found",
                       "reasons": reasons, "evidence": evidence[:3]})
    return {"version": 1, "method": "timestamp-restricted-lexical-and-qualification-screen",
            "limitations": "Similarity is not entailment; figurative language, paraphrases, numbers, causality and context require human review.",
            "human_review_required": True, "checks": checks,
            "flagged_count": sum(bool(item["reasons"]) for item in checks)}
