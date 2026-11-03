from copy import deepcopy

import pytest

from examples import blocked_candidate, example_inputs
from qa_checks import evaluate_processing_reliability, evaluate_release, seal_blueprint


def test_valid_candidate_still_requires_educator_approval():
    bp, candidate, learner, _ = example_inputs()
    report = evaluate_release(bp, candidate, learner)
    assert report["status"] == "pending_human_approval"
    assert not report["failures"]
    assert report["checks_passed"] == report["checks_total"]


def test_valid_candidate_is_approved_after_human_signoff():
    bp, candidate, learner, educator = example_inputs()
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "approved"


@pytest.mark.parametrize("duration", [59, 91])
def test_duration_outside_project_range_requests_revision(duration):
    bp, candidate, learner, educator = example_inputs()
    candidate["duration_seconds"] = duration
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "revise"
    assert "duration_out_of_range" in {failure["code"] for failure in report["failures"]}


@pytest.mark.parametrize("duration", [60, 90])
def test_duration_project_boundaries_are_accepted_when_matching_blueprint(duration):
    bp, candidate, learner, educator = example_inputs()
    bp["video"]["duration_seconds"] = duration
    bp = seal_blueprint(bp)
    candidate["blueprint_id"] = bp["blueprint_id"]
    candidate["version"] = bp["version"]
    candidate["integrity_hash"] = bp["integrity_hash"]
    candidate["duration_seconds"] = duration
    report = evaluate_release(bp, candidate, learner, educator)
    assert not any(f["code"] == "duration_out_of_range" for f in report["failures"])


def test_unauthorized_source_is_critical_and_blocks_release():
    bp, candidate, learner, educator = example_inputs()
    bp["processing"]["source_authorized"] = False
    bp = seal_blueprint(bp)
    candidate["integrity_hash"] = bp["integrity_hash"]
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "blocked"
    assert "unauthorized_source" in {f["code"] for f in report["failures"]}


def test_blueprint_identity_mismatch_blocks_release():
    bp, candidate, learner, educator = example_inputs()
    candidate["version"] += 1
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "blocked"
    assert "blueprint_mismatch" in {f["code"] for f in report["failures"]}


def test_tampered_blueprint_hash_blocks_release():
    bp, candidate, learner, educator = example_inputs()
    bp["video"]["duration_seconds"] = 80
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "blocked"
    assert "blueprint_hash_mismatch" in {f["code"] for f in report["failures"]}


def test_missing_source_timestamp_blocks_release():
    bp, candidate, learner, educator = example_inputs()
    candidate["claims"][0].pop("source_start_sec")
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "blocked"
    assert "traceability_incomplete" in {f["code"] for f in report["failures"]}


def test_unsupported_claim_blocks_even_if_everything_else_passes():
    bp, candidate, learner, educator = blocked_candidate()
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "blocked"
    codes = {failure["code"] for failure in report["failures"]}
    assert {"unsupported_claim", "missing_context"}.issubset(codes)


def test_incomplete_caption_coverage_is_critical():
    bp, candidate, learner, educator = example_inputs()
    candidate["caption_coverage_percent"] = 92
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "blocked"


def test_caption_accuracy_below_target_requires_revision():
    bp, candidate, learner, educator = example_inputs()
    candidate["caption_accuracy_percent"] = 94.9
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "revise"
    assert "caption_accuracy_below_target" in {f["code"] for f in report["failures"]}


def test_learner_comprehension_below_target_requires_revision():
    bp, candidate, learner, educator = example_inputs()
    learner["comprehension_percent"] = 79
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "revise"


def test_learner_evidence_without_ethics_approval_requires_revision():
    bp, candidate, learner, educator = example_inputs()
    learner["ethics_approved"] = False
    report = evaluate_release(bp, candidate, learner, educator)
    assert report["status"] == "revise"
    assert "learner_evidence_missing" in {f["code"] for f in report["failures"]}


def test_processing_reliability_target():
    assert evaluate_processing_reliability(9, 10)["status"] == "pass"
    assert evaluate_processing_reliability(8, 10)["status"] == "fail"


@pytest.mark.parametrize("success,total", [(-1, 10), (11, 10), (0, 0)])
def test_processing_reliability_rejects_invalid_counts(success, total):
    with pytest.raises(ValueError):
        evaluate_processing_reliability(success, total)
