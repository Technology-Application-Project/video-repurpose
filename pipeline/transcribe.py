from __future__ import annotations

import time
from importlib import metadata
from pathlib import Path
from typing import Any

from pipeline.project import atomic_json, data_hash, file_hash
from pipeline.utils import WORK_ROOT, save_json, write_srt

WHISPER_MODELS = ["small", "distil-large-v3", "large-v3"]
DEFAULT_WHISPER_MODEL = "small"
# Measured on an Apple M3 Pro CPU (int8): seconds of processing per second of audio.
WHISPER_MODEL_LABELS = {
    "small": "small (fastest, ~0.13x real time; may misspell technical terms)",
    "distil-large-v3": "distil-large-v3 (~0.3x real time; no accuracy gain seen on tested lectures)",
    "large-v3": "large-v3 (best technical vocabulary, ~0.5x real time)",
}
TRANSCRIPT_CACHE_VERSION = "1"
CACHE_DIR = WORK_ROOT / "_transcript_cache"

_whisper_models: dict[tuple[str, str], Any] = {}
_align_cache: dict[str, tuple[Any, Any]] = {}


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "cpu"
    except Exception:
        pass
    return "cpu"


def _compute_type(device: str) -> str:
    return "float16" if device == "cuda" else "int8"


def get_whisper_model(model_size: str = DEFAULT_WHISPER_MODEL):
    if model_size not in WHISPER_MODELS:
        raise ValueError(f"Choose a transcription model from: {', '.join(WHISPER_MODELS)}.")
    device = _device()
    key = (model_size, _compute_type(device))
    if key not in _whisper_models:
        import whisperx

        _whisper_models[key] = whisperx.load_model(model_size, device, compute_type=key[1])
    return _whisper_models[key]


def _cache_file(video_path: str, model_size: str, compute_type: str) -> Path:
    key = data_hash({"source": file_hash(video_path), "model": model_size, "compute_type": compute_type,
                     "format": TRANSCRIPT_CACHE_VERSION, "whisperx": metadata.version("whisperx")})
    return CACHE_DIR / f"{key}.json"


def _read_cache(path: Path) -> dict | None:
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        segments = data["segments"]
        valid = (data["format"] == TRANSCRIPT_CACHE_VERSION and isinstance(segments, list) and segments
                 and all(isinstance(item, dict) and {"start", "end", "text"} <= set(item) for item in segments))
        return {"language": data["language"], "segments": segments} if valid else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def transcribe_video(video_path: str, work: Path, model_size: str = DEFAULT_WHISPER_MODEL, use_cache: bool = True) -> dict:
    device = _device()
    compute_type = _compute_type(device)
    started = time.monotonic()
    cache_path = _cache_file(video_path, model_size, compute_type) if use_cache else None
    payload = _read_cache(cache_path) if cache_path else None
    cached = payload is not None
    if payload is None:
        payload = _transcribe(video_path, model_size, device)
        if cache_path:
            atomic_json(cache_path, {**payload, "format": TRANSCRIPT_CACHE_VERSION})
    json_path = work / "transcript.json"
    srt_path = work / "transcript.srt"
    save_json(json_path, payload)
    write_srt(payload["segments"], srt_path)
    return {**payload, "json_path": str(json_path), "srt_path": str(srt_path),
            "asr": {"model": model_size, "compute_type": compute_type, "cached": cached,
                    "seconds": round(time.monotonic() - started, 1)}}


def _transcribe(video_path: str, model_size: str, device: str) -> dict:
    import whisperx

    model = get_whisper_model(model_size)
    audio = whisperx.load_audio(video_path)
    result = model.transcribe(audio, batch_size=8)
    language = result.get("language") or "en"

    try:
        if language not in _align_cache:
            _align_cache[language] = whisperx.load_align_model(language_code=language, device=device)
        align_model, metadata_ = _align_cache[language]
        aligned = whisperx.align(result["segments"], align_model, metadata_, audio, device)
        segments = aligned.get("segments") or result.get("segments") or []
    except Exception:
        segments = result.get("segments") or []

    cleaned = []
    for seg in segments:
        item = {
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "text": str(seg.get("text", "")).strip(),
        }
        words = []
        for word in seg.get("words") or []:
            if "start" not in word or "end" not in word:
                continue
            words.append(
                {
                    "word": str(word.get("word", "")),
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                }
            )
        if words:
            item["words"] = words
        if item["text"]:
            cleaned.append(item)

    return {"language": language, "segments": cleaned}



def transcript_as_text(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(f"{i}. [{seg['start']:.3f}-{seg['end']:.3f}] {seg['text']}")
    return "\n".join(lines)
