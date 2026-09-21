from __future__ import annotations

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
) -> list[dict]:
    from sentence_transformers import util

    model = get_embedder()
    goals = objectives or ["educational lecture content"]
    goal_emb = model.encode(goals, convert_to_tensor=True)

    ranked = []
    for cand in candidates:
        text = " ".join(
            part
            for part in (
                cand.get("title"),
                cand.get("takeaway"),
                cand.get("quote"),
                excerpt_for_range(segments, cand["start"], cand["end"]),
            )
            if part
        )
        cand_emb = model.encode(text, convert_to_tensor=True)
        score = float(util.cos_sim(cand_emb, goal_emb).max().item())
        item = dict(cand)
        item["score"] = round(score, 4)
        item["excerpt"] = excerpt_for_range(segments, cand["start"], cand["end"])
        ranked.append(item)

    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked[:top_n]
