"""Executable Phase 6 validation checks for the KQ3 teaser release gate.

This module validates JSON-like records. It does not decode or inspect video
pixels/audio itself; media measurements must be supplied by the preceding
pipeline or a human-reviewed technical checklist.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

REQUIRED_SEQUENCE = ["hook", "context", "concept", "takeaway", "call_to_action"]
MIN_DURATION = 60.0
MAX_DURATION = 90.0
CAPTION_ACCURACY_TARGET = 95.0
CAPTION_COVERAGE_TARGET = 100.0
COMPREHENSION_TARGET = 80.0
PROCESSING_SUCCESS_TARGET = 90.0


def _canonical_hash(blueprint: dict[str, Any]) -> str:
    payload = dict(blueprint)
    payload.pop("integrity_hash", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def seal_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with a SHA-256 hash matching the Phase 1 canonical JSON rule."""
    sealed = dict(blueprint)
    sealed.pop("integrity_hash", None)
    sealed["integrity_hash"] = _canonical_hash(sealed)
    return sealed


def _number(value: Any, fallback: float = -1.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def evaluate_release(
    blueprint: dict[str, Any],
    candidate: dict[str, Any],
    learner_result: dict[str, Any] | None = None,
    educator_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate supplied Phase 6 evidence and return a structured QA report.

    A critical failure always blocks release. Any other failed acceptance check
    returns ``revise``. Passing automated checks still requires an educator's
    recorded approval before the report can return ``approved``.
    """
    learner_result = learner_result or {}
    educator_decision = educator_decision or {}
    checks: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    def record(name: str, code: str, passed: bool, expected: str, observed: Any,
               severity: str = "major", owner: str = "Phase 6") -> None:
        checks.append({"name": name, "code": code, "status": "pass" if passed else "fail",
                       "expected": expected, "observed": observed})
        if not passed:
            failures.append({"code": code, "severity": severity, "owner": owner,
                             "message": f"{name}: expected {expected}; observed {observed}."})

    bp_id = blueprint.get("blueprint_id")
    version = blueprint.get("version")
    expected_hash = blueprint.get("integrity_hash")
    calculated_hash = _canonical_hash(blueprint)
    record("Blueprint content hash", "blueprint_hash_mismatch", bool(expected_hash) and expected_hash == calculated_hash,
           "integrity_hash matches canonical blueprint content", expected_hash or "missing", "critical", "Phase 1 / Phase 6")

    identity_ok = (candidate.get("blueprint_id") == bp_id and candidate.get("version") == version
                   and candidate.get("integrity_hash") == expected_hash)
    record("Blueprint identity handoff", "blueprint_mismatch", identity_ok,
           f"ID={bp_id}, version={version}, hash={expected_hash}",
           f"ID={candidate.get('blueprint_id')}, version={candidate.get('version')}, hash={candidate.get('integrity_hash')}",
           "critical", "Phase 1 / receiving phase")

    processing = blueprint.get("processing", {})
    source_ok = processing.get("source_authorized") is True and candidate.get("source_authorized") is True
    record("Authorized source", "unauthorized_source", source_ok,
           "blueprint and candidate both confirm an authorized institutional source",
           {"blueprint": processing.get("source_authorized"), "candidate": candidate.get("source_authorized")},
           "critical", "Phase 2 / Phase 6")

    target_duration = _number(blueprint.get("video", {}).get("duration_seconds"))
    actual_duration = _number(candidate.get("duration_seconds"))
    duration_ok = MIN_DURATION <= actual_duration <= MAX_DURATION and abs(actual_duration - target_duration) <= 1.0
    record("Teaser duration", "duration_out_of_range", duration_ok,
           f"60-90 seconds and within 1 second of blueprint target ({target_duration:g})",
           actual_duration, "major", "Phase 5 / Phase 6")

    claims = candidate.get("claims", [])
    traceable = bool(claims) and all(
        _number(claim.get("source_start_sec")) >= 0
        and _number(claim.get("source_end_sec")) > _number(claim.get("source_start_sec"))
        and bool(claim.get("evidence_text", "").strip())
        for claim in claims if isinstance(claim, dict)
    ) and all(isinstance(claim, dict) for claim in claims)
    record("Claim-to-source traceability", "traceability_incomplete", traceable,
           "every selected claim has transcript evidence and a valid source time range",
           f"{sum(1 for c in claims if isinstance(c, dict) and c.get('evidence_text') and _number(c.get('source_start_sec')) >= 0 and _number(c.get('source_end_sec')) > _number(c.get('source_start_sec')))} of {len(claims)} claims traceable",
           "critical", "Phase 3 / Phase 4 / Phase 6")

    unsupported = [c for c in claims if isinstance(c, dict) and c.get("supported_by_source") is not True]
    record("Source-supported claims", "unsupported_claim", not unsupported,
           "zero claims unsupported by the original recording", len(unsupported),
           "critical", "Phase 4 / Phase 6")

    structure = candidate.get("structure", [])
    structure_ok = structure == REQUIRED_SEQUENCE and candidate.get("concept_count") == 1
    record("Single-concept teaser structure", "blueprint_fidelity", structure_ok,
           "hook, context, one concept, takeaway, call to action in order",
           {"structure": structure, "concept_count": candidate.get("concept_count")},
           "major", "Phase 4 / Phase 5 / Phase 6")

    context_ok = candidate.get("context_complete") is True
    record("Context preservation", "missing_context", context_ok,
           "required explanatory context is present", candidate.get("context_complete"),
           "critical", "Phase 2 / Phase 4 / Phase 6")

    accessibility = blueprint.get("accessibility", {})
    captions_required = accessibility.get("captions_required") is True
    coverage = _number(candidate.get("caption_coverage_percent"))
    captions_present = candidate.get("captions_present") is True
    coverage_ok = (not captions_required) or (captions_present and coverage >= CAPTION_COVERAGE_TARGET)
    record("Caption coverage", "caption_coverage_incomplete", coverage_ok,
           "100% spoken-content coverage when captions are required",
           {"captions_present": captions_present, "coverage_percent": coverage},
           "critical", "Phase 3 / Phase 5 / Phase 6")

    accuracy = _number(candidate.get("caption_accuracy_percent"))
    accuracy_ok = (not captions_required) or accuracy >= CAPTION_ACCURACY_TARGET
    record("Caption word accuracy", "caption_accuracy_below_target", accuracy_ok,
           f"at least {CAPTION_ACCURACY_TARGET:g}% against the verified transcript", accuracy,
           "major", "Phase 3 / Phase 5 / Phase 6")

    sync_ok = candidate.get("audio_video_sync_ok") is True
    record("Audio-video synchronization", "sync_error", sync_ok,
           "audio and video are acceptably synchronized", candidate.get("audio_video_sync_ok"),
           "major", "Phase 5 / Phase 6")

    boundaries_ok = candidate.get("boundary_integrity_ok") is True
    record("Clip boundary integrity", "boundary_error", boundaries_ok,
           "cuts preserve complete phrases and do not mislead", candidate.get("boundary_integrity_ok"),
           "major", "Phase 2 / Phase 5 / Phase 6")

    source_link_ok = (not blueprint.get("video", {}).get("source_link_required", True)
                      or candidate.get("source_link_present") is True)
    record("Call to action and source link", "source_link_missing", source_link_ok,
           "required call to action links to the full source recording", candidate.get("source_link_present"),
           "major", "Phase 5 / Phase 6")

    learner_required = blueprint.get("qa", {}).get("learner_evidence_required", True)
    learner_complete = (learner_result.get("ethics_approved") is True
                        and int(learner_result.get("sample_size", 0) or 0) > 0)
    if learner_required:
        record("Learner evidence governance", "learner_evidence_missing", learner_complete,
               "ethics-approved learner evidence with a recorded sample size",
               {"ethics_approved": learner_result.get("ethics_approved"),
                "sample_size": learner_result.get("sample_size")},
               "major", "Phase 6")
        comprehension = _number(learner_result.get("comprehension_percent"))
        comprehension_ok = learner_complete and comprehension >= COMPREHENSION_TARGET
        record("Learner comprehension", "comprehension_below_target", comprehension_ok,
               f"at least {COMPREHENSION_TARGET:g}% correct in the approved pilot",
               comprehension, "major", "Phase 6")

    total = len(checks)
    passed = sum(item["status"] == "pass" for item in checks)
    score = round(100 * passed / total, 1) if total else 0.0
    pass_score = _number(blueprint.get("qa", {}).get("pass_score"), 80.0)
    if score < pass_score:
        failures.append({"code": "qa_score_below_threshold", "severity": "major", "owner": "Phase 6",
                         "message": f"Overall QA score: expected at least {pass_score:g}%; observed {score:g}%."})

    critical = [f for f in failures if f["severity"] == "critical"]
    if critical:
        status = "blocked"
    elif failures:
        status = "revise"
    elif educator_decision.get("approved") is True and educator_decision.get("reviewer"):
        status = "approved"
    else:
        status = "pending_human_approval"

    return {
        "blueprint_id": bp_id,
        "version": version,
        "schema_version": blueprint.get("schema_version"),
        "integrity_hash": expected_hash,
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "score_percent": score,
        "pass_score": pass_score,
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "failures": failures,
        "human_decision": educator_decision or {"approved": False, "reviewer": None},
    }


def evaluate_processing_reliability(successful_jobs: int, total_jobs: int,
                                    target_percent: float = PROCESSING_SUCCESS_TARGET) -> dict[str, Any]:
    """Evaluate batch reliability as completed jobs divided by attempted jobs."""
    if total_jobs <= 0 or successful_jobs < 0 or successful_jobs > total_jobs:
        raise ValueError("Require total_jobs > 0 and 0 <= successful_jobs <= total_jobs.")
    observed = round(100 * successful_jobs / total_jobs, 1)
    return {"successful_jobs": successful_jobs, "total_jobs": total_jobs,
            "success_percent": observed, "target_percent": target_percent,
            "status": "pass" if observed >= target_percent else "fail"}
