from __future__ import annotations

import json
import re
from pathlib import Path

import ollama

from pipeline.transcribe import transcript_as_text
from pipeline.utils import ROOT, snap_to_segments

PROMPT_PATH = ROOT / "prompts" / "nominate_clips.txt"


def list_ollama_models() -> tuple[list[str], str]:
    try:
        payload = ollama.list()
    except Exception as exc:
        return [], f"Ollama not running ({exc}). Start Ollama, then reload."

    models = []
    raw = payload.get("models") if isinstance(payload, dict) else getattr(payload, "models", None)
    if raw is None and hasattr(payload, "model_dump"):
        raw = payload.model_dump().get("models")
    for item in raw or []:
        if isinstance(item, dict):
            name = item.get("model") or item.get("name")
        else:
            name = getattr(item, "model", None) or getattr(item, "name", None)
        if name:
            models.append(name)

    if not models:
        return [], "Ollama is running but no models are installed. Pull qwen2.5."

    def sort_key(name: str):
        n = name.lower()
        if n in {"qwen2.5", "qwen2.5:latest"}:
            return (0, n)
        if n.startswith("qwen2.5"):
            return (1, n)
        if "qwen2.5" in n:
            return (2, n)
        return (3, n)

    preferred = sorted(models, key=sort_key)
    return preferred, f"Ollama: {preferred[0]} ready"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _parse_json(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise
        return json.loads(match.group(0))


def _chunk_segments(segments: list[dict], max_chars: int = 9000) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for seg in segments:
        line = f"[{seg['start']:.3f}-{seg['end']:.3f}] {seg['text']}\n"
        if current and size + len(line) > max_chars:
            chunks.append(current)
            current = current[-2:]
            size = sum(len(s.get("text", "")) for s in current)
        current.append(seg)
        size += len(line)
    if current:
        chunks.append(current)
    return chunks or [segments]


def _window_fallback(segments: list[dict], min_s: float, max_s: float) -> list[dict]:
    if not segments:
        return []
    candidates = []
    i = 0
    idx = 1
    while i < len(segments) and len(candidates) < 12:
        start = float(segments[i]["start"])
        texts = []
        j = i
        end = start
        while j < len(segments) and (float(segments[j]["end"]) - start) < min_s:
            texts.append(segments[j]["text"].strip())
            end = float(segments[j]["end"])
            j += 1
        while j < len(segments) and (float(segments[j]["end"]) - start) <= max_s:
            texts.append(segments[j]["text"].strip())
            end = float(segments[j]["end"])
            j += 1
            if end - start >= min_s:
                break
        if end - start >= min_s * 0.7:
            quote = " ".join(texts).strip()
            candidates.append(
                {
                    "id": f"w{idx}",
                    "start": start,
                    "end": end,
                    "title": quote[:72] + ("..." if len(quote) > 72 else ""),
                    "takeaway": quote[:180],
                    "reason": "Fallback window from transcript (Ollama returned too few clips).",
                    "quote": quote[:200],
                    "source": "window",
                }
            )
            idx += 1
        i = j if j > i else i + 1
    return candidates


def _normalize_candidates(
    raw: list[dict],
    segments: list[dict],
    min_s: float,
    max_s: float,
    duration: float,
) -> list[dict]:
    cleaned = []
    for i, item in enumerate(raw, 1):
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        start, end = snap_to_segments(start, end, segments)
        start = max(0.0, start)
        end = min(duration, end) if duration else end
        if end - start < min_s * 0.6 or end <= start:
            continue
        if end - start > max_s + 8:
            end = start + max_s
            start, end = snap_to_segments(start, end, segments)
        cleaned.append(
            {
                "id": str(item.get("id") or f"c{i}"),
                "start": round(start, 3),
                "end": round(end, 3),
                "title": str(item.get("title") or "Untitled clip").strip(),
                "takeaway": str(item.get("takeaway") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "quote": str(item.get("quote") or "").strip(),
                "source": "ollama",
            }
        )
    uniq = []
    seen = set()
    for cand in cleaned:
        key = (round(cand["start"], 1), round(cand["end"], 1))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(cand)
    for i, cand in enumerate(uniq, 1):
        cand["id"] = f"c{i}"
    return uniq


def nominate_candidates(
    model: str,
    topic: str,
    objectives: list[str],
    segments: list[dict],
    min_s: float,
    max_s: float,
    duration: float,
) -> list[dict]:
    if not segments:
        raise RuntimeError("No transcript segments to nominate from.")

    system_prompt = (
        _load_prompt()
        .replace("MIN_SECONDS", str(min_s))
        .replace("MAX_SECONDS", str(max_s))
    )
    collected: list[dict] = []
    last_error = None
    for chunk in _chunk_segments(segments):
        user_prompt = (
            f"Topic: {topic}\n"
            f"Learning objectives:\n- "
            + "\n- ".join(objectives or ["(none provided)"])
            + f"\nClip length: {min_s}-{max_s} seconds\n\n"
            f"Timestamped transcript:\n{transcript_as_text(chunk)}\n"
        )
        try:
            response = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format="json",
            )
            message = response["message"] if isinstance(response, dict) else response.message
            content = message["content"] if isinstance(message, dict) else message.content
            parsed = _parse_json(content)
            collected.extend(parsed.get("candidates") or [])
        except Exception as exc:
            last_error = exc

    candidates = _normalize_candidates(collected, segments, min_s, max_s, duration)
    if len(candidates) < 3:
        candidates.extend(_window_fallback(segments, min_s, max_s))
        candidates = _normalize_candidates(candidates, segments, min_s, max_s, duration)

    if not candidates:
        detail = f" Last Ollama error: {last_error}" if last_error else ""
        raise RuntimeError("Ollama returned no usable clip candidates." + detail)
    return candidates
