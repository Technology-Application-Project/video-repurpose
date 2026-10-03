from __future__ import annotations

import re

from pipeline.utils import MAX_REVIEW, excerpt_for_range

_embedder = None


def get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer

        _embedder = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedder


def rank_candidates(
    candidates: list[dict],
    objectives: list[str],
    segments: list[dict],
    top_n: int = MAX_REVIEW,
    *,
    target_audience: str | None = None,
) -> list[dict]:
    if target_audience:
        return rank_editorial_candidates(candidates, segments, top_n)
    from sentence_transformers import util

    model = get_embedder()
    goals = objectives or ["educational lecture content"]
    goal_emb = model.encode(goals, convert_to_tensor=True)

    ranked = []
    for cand in candidates:
        text = excerpt_for_range(segments, cand["start"], cand["end"])
        if not text.strip():
            continue
        cand_emb = model.encode(text, convert_to_tensor=True)
        score = float(util.cos_sim(cand_emb, goal_emb).max().item())
        item = dict(cand)
        item["score"] = round(score, 4)
        item["excerpt"] = text
        ranked.append(item)

    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked[:top_n]


def rank_editorial_candidates(candidates: list[dict], segments: list[dict], top_n: int) -> list[dict]:
    ranked = []
    for candidate in candidates:
        if candidate.get("editorial_status") == "rejected":
            continue
        item = dict(candidate)
        item["excerpt"] = excerpt_for_range(segments, item["start"], item["end"])
        if not item["excerpt"].strip():
            continue
        if item.get("editorial_status") != "estimated":
            item.update(score=None, editorial_status="unavailable")
        ranked.append(item)
    ranked.sort(key=lambda item: (item.get("score") is None, -(item.get("score") or 0), item["start"]))
    selected = []
    for item in ranked:
        tokens = set(re.findall(r"\w+", item["excerpt"].casefold()))
        duplicate = False
        for previous in selected:
            overlap = max(0, min(item["end"], previous["end"]) - max(item["start"], previous["start"]))
            shortest = min(item["end"] - item["start"], previous["end"] - previous["start"])
            other_tokens = set(re.findall(r"\w+", previous["excerpt"].casefold()))
            text_overlap = len(tokens & other_tokens) / max(1, len(tokens | other_tokens))
            if overlap / max(0.001, shortest) > 0.5 or (len(tokens) >= 12 and text_overlap > 0.85):
                duplicate = True
                break
        if not duplicate:
            selected.append(item)
        if len(selected) >= top_n:
            break
    return selected
