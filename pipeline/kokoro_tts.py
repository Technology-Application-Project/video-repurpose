from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile


MODEL = "hexgrad/Kokoro-82M"
VERSION = "0.9.4"
RENDER_VERSION = "1.0"
VOICES = ("am_michael", "af_heart", "af_sky")
ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "work" / "kokoro-runtime"


def cache_identity() -> dict:
    return {"provider": "kokoro", "model": MODEL, "package_version": VERSION,
            "render_version": RENDER_VERSION, "sample_rate": 24000,
            "target_lufs": -19, "true_peak_ceiling_dbtp": -1}


def synthesize(text: str, dest: Path, *, voice: str, speed: float,
               pitch_semitones: float = 0, ffmpeg: str = "ffmpeg") -> Path:
    if voice not in VOICES:
        raise ValueError(f"Unsupported Kokoro voice: {voice}")
    if not math.isfinite(speed) or not 0.5 <= speed <= 2:
        raise ValueError("Kokoro speed must be between 0.5 and 2.")
    if not math.isfinite(pitch_semitones) or not -4 <= pitch_semitones <= 4:
        raise ValueError("Kokoro pitch must be between -4 and +4 semitones.")
    if not text.strip():
        raise ValueError("Narration must not be empty.")
    python = Path(os.environ.get("KOKORO_PYTHON", str(RUNTIME / ".venv" / "bin" / "python")))
    if not python.is_file():
        raise RuntimeError("Local Kokoro is not installed. Follow the Kokoro setup in pipeline/README.md "
                           "or set KOKORO_PYTHON. No fallback voice was used.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as directory:
        pending = Path(directory) / "speech.aiff"
        job = {"text": text, "destination": str(pending.resolve()), "voice": voice,
               "speed": speed, "pitch_semitones": pitch_semitones, "ffmpeg": ffmpeg}
        try:
            subprocess.run([str(python), str(Path(__file__).resolve())], input=json.dumps(job),
                           text=True, capture_output=True, check=True, timeout=180)
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError("Local Kokoro narration failed. Check its Python environment and cached models "
                               "using the Kokoro setup in pipeline/README.md. No fallback voice was used.") from error
        if not pending.is_file() or pending.stat().st_size == 0:
            raise RuntimeError("Local Kokoro returned empty audio. No fallback voice was used.")
        pending.replace(dest)
    dest.with_suffix(".txt").write_text(text, encoding="utf-8")
    return dest


def _loudness(ffmpeg: str, source: Path) -> dict:
    result = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(source), "-af",
         "loudnorm=I=-19:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    measurement, end = json.JSONDecoder().raw_decode(result.stderr[result.stderr.rfind("{"):])
    for key in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset"):
        if not math.isfinite(float(measurement[key])):
            raise ValueError("Narration has no measurable loudness.")
    return measurement


def _worker(prepare: bool) -> None:
    import sys

    os.environ["HF_HOME"] = os.environ.get("KOKORO_MODEL_CACHE", str(RUNTIME / "model-cache"))
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "0" if prepare else "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "0" if prepare else "1"
    from importlib.metadata import version
    import espeakng_loader
    import numpy as np
    import soundfile as sf
    import torch
    import spacy
    from phonemizer.backend.espeak.wrapper import EspeakWrapper
    from kokoro import KPipeline

    if version("kokoro") != VERSION:
        raise RuntimeError(f"Expected kokoro=={VERSION}; reinstall requirements-kokoro.txt.")
    if not spacy.util.is_package("en_core_web_sm"):
        if not prepare:
            raise RuntimeError("Missing English model. Run Kokoro --prepare before offline synthesis.")
        spacy.cli.download("en_core_web_sm")
    EspeakWrapper.set_library(espeakng_loader.get_library_path())
    EspeakWrapper.set_data_path(espeakng_loader.get_data_path())
    torch.set_num_threads(4)
    pipeline = KPipeline(lang_code="a", repo_id=MODEL, device="cpu")
    if prepare:
        for voice in VOICES:
            pipeline.load_voice(voice)
        print("Kokoro model and audience voices cached. Synthesis runs locally and offline.")
        return
    job = json.load(sys.stdin)
    text = " ".join(job["text"].split())
    if text[-1] not in ".!?":
        text += "."
    chunks = [audio.numpy() for graphemes, phonemes, audio in
              pipeline(text, voice=job["voice"], speed=job["speed"])]
    audio = np.concatenate(chunks)
    if not np.isfinite(audio).all() or not 0 < np.max(np.abs(audio)) < 1:
        raise ValueError("Kokoro returned invalid, silent or clipped audio.")
    fade_frames = min(120, len(audio) // 2)
    audio[:fade_frames] *= np.linspace(0, 1, fade_frames)
    audio[-fade_frames:] *= np.linspace(1, 0, fade_frames)
    destination = Path(job["destination"])
    ffmpeg = job["ffmpeg"]
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        raw = Path(directory) / "raw.wav"
        sf.write(raw, audio, 24000, subtype="PCM_16")
        source = raw
        if job["pitch_semitones"]:
            shifted_rate = round(24000 * 2 ** (job["pitch_semitones"] / 12))
            source = Path(directory) / "shifted.wav"
            subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-n", "-i", str(raw), "-af",
                 f"asetrate={shifted_rate},aresample=24000,atempo={24000 / shifted_rate},apad,atrim=end_sample={len(audio)}",
                 "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(source)],
                capture_output=True, check=True, timeout=30,
            )
        measured = _loudness(ffmpeg, source)
        audio_filter = (
            "loudnorm=I=-19:TP=-1.5:LRA=11:linear=true:"
            f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
            f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
            f"offset={measured['target_offset']}"
        )
        subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-n", "-i", str(source), "-af", audio_filter,
             "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16be", "-f", "aiff", str(destination)],
            capture_output=True, check=True, timeout=30,
        )
        finished = _loudness(ffmpeg, destination)
        gain = min(-19 - float(finished["input_i"]), -1.1 - float(finished["input_tp"]))
        if abs(gain) > 0.01:
            corrected = Path(directory) / "corrected.aiff"
            subprocess.run(
                [ffmpeg, "-nostdin", "-v", "error", "-n", "-i", str(destination),
                 "-af", f"volume={gain}dB", "-c:a", "pcm_s16be", str(corrected)],
                capture_output=True, check=True, timeout=30,
            )
            corrected.replace(destination)
        info = sf.info(destination)
        if info.samplerate != 24000 or info.channels != 1 or abs(info.duration - len(audio) / 24000) > 0.05:
            raise ValueError("Kokoro audio format or duration changed during finishing.")
        if float(_loudness(ffmpeg, destination)["input_tp"]) > -1:
            raise ValueError("Kokoro narration exceeds the true-peak ceiling.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    _worker(parser.parse_args().prepare)