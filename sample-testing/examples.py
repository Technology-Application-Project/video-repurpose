"""Deterministic, synthetic inputs for testing and classroom demonstration."""
from copy import deepcopy

from qa_checks import seal_blueprint


def example_inputs():
    blueprint = seal_blueprint({
        "schema_version": "1.0.0",
        "blueprint_id": "bp-demo-001",
        "version": 1,
        "audience": {"age_min": 8, "age_max": 12, "reading_grade_max": 6},
        "learning": {"objective": "Explain how evaporation moves water into the air.",
                     "required_concepts": ["evaporation"]},
        "video": {"duration_seconds": 75, "source_link_required": True},
        "processing": {"source_authorized": True},
        "accessibility": {"captions_required": True},
        "qa": {"pass_score": 80, "learner_evidence_required": True},
    })
    candidate = {
        "blueprint_id": blueprint["blueprint_id"],
        "version": blueprint["version"],
        "integrity_hash": blueprint["integrity_hash"],
        "source_authorized": True,
        "duration_seconds": 75.0,
        "claims": [{"text": "Heat changes some liquid water into water vapour.",
                    "supported_by_source": True,
                    "source_start_sec": 415.2,
                    "source_end_sec": 423.8,
                    "evidence_text": "Heat changes liquid water into water vapour."}],
        "structure": ["hook", "context", "concept", "takeaway", "call_to_action"],
        "concept_count": 1,
        "context_complete": True,
        "captions_present": True,
        "caption_coverage_percent": 100.0,
        "caption_accuracy_percent": 97.0,
        "audio_video_sync_ok": True,
        "boundary_integrity_ok": True,
        "source_link_present": True,
    }
    learner = {"ethics_approved": True, "sample_size": 10, "comprehension_percent": 90.0}
    educator = {"approved": True, "reviewer": "Educator reviewer"}
    return blueprint, candidate, learner, educator


def blocked_candidate():
    blueprint, candidate, learner, educator = example_inputs()
    candidate = deepcopy(candidate)
    candidate["claims"][0]["supported_by_source"] = False
    candidate["context_complete"] = False
    return blueprint, candidate, learner, educator
