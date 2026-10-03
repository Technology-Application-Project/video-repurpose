from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.utils import save_json


RUBRIC_VERSION = "1.1"
DIMENSIONS = {
    "source_fidelity": ("Source fidelity", 25),
    "learning_value": ("Audience fit and learning value", 25),
    "standalone_coherence": ("Standalone coherence", 20),
    "captions": ("Captions", 15),
    "audio_visuals": ("Audio and visuals", 15),
}
GATES = (
    "claims_supported",
    "cuts_preserve_context",
    "captions_readable",
    "human_final_review",
)
ARTIFACTS = ("compilation.mp4", "captions.srt", "post.txt", "selection.json")


def score_review(review: dict) -> dict:
    reviewer = review.get("reviewer") or {}
    if reviewer.get("kind") not in {"human", "assistant"} or not str(reviewer.get("name") or "").strip():
        raise ValueError("A named human or assistant reviewer is required.")
    ratings = review.get("ratings") or {}
    gates = review.get("gates") or {}
    if set(ratings) - set(DIMENSIONS) or set(gates) - set(GATES):
        raise ValueError("Unknown rubric dimension or publication gate.")
    dimensions = []
    earned_points = 0.0
    assessed_weight = 0
    blockers = []
    for key, (label, weight) in DIMENSIONS.items():
        if key == "learning_value" and review.get("rubric_version", "1.0") == "1.0":
            label = "Learning value"
        rating = ratings.get(key) or {}
        score = rating.get("score")
        evidence = rating.get("evidence") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise ValueError(f"{key}: evidence must be a list of nonempty references or observations.")
        if score is not None and (type(score) is not int or not 0 <= score <= 4):
            raise ValueError(f"{key}: score must be an integer from 0 to 4, or null.")
        if score is not None and not evidence:
            raise ValueError(f"{key}: a score requires evidence.")
        points = None if score is None else weight * score / 4
        if points is None:
            blockers.append(f"{label}: not assessed")
        else:
            earned_points += points
            assessed_weight += weight
            if score < 3:
                blockers.append(f"{label}: revision required ({score}/4)")
        dimensions.append({"id": key, "label": label, "weight": weight, "score": score,
                           "weighted_points": points, "evidence": evidence})
    gate_results = {}
    for key in GATES:
        gate = gates.get(key) or {}
        status = gate.get("status", "not_assessed")
        evidence = gate.get("evidence") or ""
        if status not in {"pass", "fail", "not_assessed"}:
            raise ValueError(f"{key}: invalid gate status.")
        if not isinstance(evidence, str) or (status != "not_assessed" and not evidence.strip()):
            raise ValueError(f"{key}: an assessed gate requires evidence.")
        if key == "human_final_review" and status == "pass" and reviewer["kind"] != "human":
            raise ValueError("An assistant cannot attest human final review.")
        gate_results[key] = {"status": status, "evidence": evidence}
        if status != "pass":
            blockers.append(f"{key}: {status}")
    complete = assessed_weight == 100
    return {
        "rubric_version": review.get("rubric_version", "1.0"),
        "selection_context": review.get("selection_context") or {},
        "reviewer": reviewer,
        "dimensions": dimensions,
        "earned_points": earned_points,
        "assessed_weight_percent": assessed_weight,
        "overall_score": earned_points if complete else None,
        "assessment_status": "complete" if complete else "partial",
        "publication_status": "approved" if not blockers else "blocked",
        "publication_blockers": blockers,
        "gates": gate_results,
    }


def artifact_hashes(export_dir: Path) -> dict[str, str]:
    hashes = {}
    for name in ARTIFACTS:
        with (export_dir / name).open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[name] = digest.hexdigest()
    return hashes


def export_context(export_dir: Path) -> dict:
    selection = json.loads((export_dir / "selection.json").read_text(encoding="utf-8"))
    return selection.get("selection_context") or {}


def assessment_markdown(assessment: dict) -> str:
    lines = ["# Post-Generation Assessment", "", f"Rubric: {assessment['rubric_version']}",
             f"Reviewer: {assessment['reviewer']['name']} ({assessment['reviewer']['kind']})", ""]
    context = assessment.get("selection_context") or {}
    lines += [f"Target audience: {context.get('target_audience') or 'Unknown (legacy export)' }.", ""]
    if assessment["overall_score"] is None:
        lines.append("Overall score: **not available; assessment incomplete**.")
    else:
        lines.append(f"Overall score: **{assessment['overall_score']:.2f}/100**.")
    lines += [f"Earned weighted points: {assessment['earned_points']:.2f}; "
              f"assessed weight: {assessment['assessed_weight_percent']}%.",
              "Missing dimensions are not scored as zero or normalized away.",
              f"Publication: **{assessment['publication_status']}**.", "",
              "| Dimension | Weight | Rating | Points |", "|---|---:|---:|---:|"]
    for dimension in assessment["dimensions"]:
        rating = "Not assessed" if dimension["score"] is None else f"{dimension['score']}/4"
        points = "-" if dimension["weighted_points"] is None else f"{dimension['weighted_points']:.2f}"
        lines.append(f"| {dimension['label']} | {dimension['weight']}% | {rating} | {points} |")
    lines += ["", "## Evidence", ""]
    for dimension in assessment["dimensions"]:
        lines.append(f"### {dimension['label']}")
        lines.extend(f"- {item}" for item in dimension["evidence"])
        lines.append("")
    lines += ["## Publication Gates", ""]
    for key, gate in assessment["gates"].items():
        lines.append(f"- {key}: {gate['status']}. {gate['evidence']}")
    lines += ["", "## Blockers", ""]
    lines.extend(f"- {blocker}" for blocker in assessment["publication_blockers"])
    lines += ["", "Approval applies only to the exact artifact hashes in assessment.json.", ""]
    return "\n".join(lines)


def assess_export(export_dir: Path, review: dict) -> Path:
    current_hashes = artifact_hashes(export_dir)
    if review.get("artifact_sha256") != current_hashes:
        raise ValueError("Review artifact hashes are missing or stale. Review the current export again.")
    review = {**review, "selection_context": export_context(export_dir)}
    assessment = score_review(review)
    assessment.update(artifact_sha256=current_hashes, created_at=datetime.now(timezone.utc).isoformat())
    destination = export_dir / "assessments" / uuid.uuid4().hex[:12]
    destination.mkdir(parents=True, exist_ok=False)
    save_json(destination / "review.json", review)
    save_json(destination / "assessment.json", assessment)
    (destination / "assessment.md").write_text(assessment_markdown(assessment), encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Score an evidence-backed review of a rendered compilation.")
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("--template", action="store_true", help="Create a blank review bound to the current export.")
    arguments = parser.parse_args()
    if arguments.template:
        review = review_template(arguments.export_dir)
        with arguments.review.open("x", encoding="utf-8") as stream:
            json.dump(review, stream, indent=2)
        print(arguments.review)
        return
    review = json.loads(arguments.review.read_text(encoding="utf-8"))
    print(assess_export(arguments.export_dir, review))


def review_template(export_dir: Path) -> dict:
    return {
    "rubric_version": RUBRIC_VERSION,
    "selection_context": export_context(export_dir),
        "reviewer": {"kind": "human", "name": ""},
        "artifact_sha256": artifact_hashes(export_dir),
        "ratings": {key: {"score": None, "evidence": []} for key in DIMENSIONS},
        "gates": {key: {"status": "not_assessed", "evidence": ""} for key in GATES},
    }


if __name__ == "__main__":
    main()