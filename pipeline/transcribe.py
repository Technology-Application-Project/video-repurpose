from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.utils import save_json, write_srt

_whisper_model = None
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


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisperx

        device = _device()
        _whisper_model = whisperx.load_model("small", device, compute_type="float32")
    return _whisper_model


def transcribe_video(video_path: str, work: Path) -> dict:
    import whisperx

    model = get_whisper_model()
    audio = whisperx.load_audio(video_path)
    result = model.transcribe(audio, batch_size=8)
    language = result.get("language") or "en"
    device = _device()

    try:
        if language not in _align_cache:
            _align_cache[language] = whisperx.load_align_model(language_code=language, device=device)
        align_model, metadata = _align_cache[language]
        aligned = whisperx.align(result["segments"], align_model, metadata, audio, device)
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

    payload = {"language": language, "segments": cleaned}
    json_path = work / "transcript.json"
    srt_path = work / "transcript.srt"
    save_json(json_path, payload)
    write_srt(cleaned, srt_path)
    payload["json_path"] = str(json_path)
    payload["srt_path"] = str(srt_path)
    return payload


def transcript_as_text(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(f"{i}. [{seg['start']:.3f}-{seg['end']:.3f}] {seg['text']}")
    return "\n".join(lines)
