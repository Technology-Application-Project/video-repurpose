from __future__ import annotations

import json
import re
import shutil
import sys
import wave
from pathlib import Path

from pipeline.utils import ROOT, run_cmd

SAMPLE_RATE = 22050

KOKORO_DIR = ROOT / "models" / "kokoro"
KOKORO_MODEL = KOKORO_DIR / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = KOKORO_DIR / "voices-v1.0.bin"
KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_PREFIX = "Kokoro: "
KOKORO_DEFAULT = "af_heart"
KOKORO_ACCENTS = {"a": ("American", "en-us"), "b": ("British", "en-gb")}
_kokoro = None

_SAPI_SCRIPT = r"""
param([string]$Jobs, [string]$Voice, [int]$Rate)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($Voice) { $synth.SelectVoice($Voice) }
$synth.Rate = $Rate
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(%d,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($job in (Get-Content -Raw -Encoding UTF8 $Jobs | ConvertFrom-Json)) {
    $synth.SetOutputToWaveFile($job.out, $format)
    $synth.Speak($job.text)
}
$synth.SetOutputToNull()
$synth.Dispose()
""" % SAMPLE_RATE


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def kokoro_installed() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        import soundfile  # noqa: F401
    except ImportError:
        return False
    return True


def _kokoro_model():
    """Load Kokoro once, downloading the model files on first use."""
    global _kokoro
    if _kokoro is None:
        import urllib.request

        from kokoro_onnx import Kokoro

        KOKORO_DIR.mkdir(parents=True, exist_ok=True)
        for path in (KOKORO_MODEL, KOKORO_VOICES):
            if not path.exists():
                partial = path.with_suffix(path.suffix + ".part")
                try:
                    urllib.request.urlretrieve(KOKORO_RELEASE + path.name, partial)
                except OSError as exc:
                    raise RuntimeError(f"Could not download Kokoro model file {path.name}: {exc}") from exc
                partial.replace(path)
        _kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    return _kokoro


def kokoro_voices() -> list[str]:
    """English Kokoro voices as dropdown labels, default voice first."""
    if not kokoro_installed():
        return []
    try:
        names = _kokoro_model().get_voices()
    except Exception:
        return []
    names = sorted((n for n in names if n[:1] in KOKORO_ACCENTS and n[1:2] in "fm"),
                   key=lambda n: (n != KOKORO_DEFAULT, n))
    return [f"{KOKORO_PREFIX}{n} ({KOKORO_ACCENTS[n[0]][0]} {'female' if n[1] == 'f' else 'male'})" for n in names]


def _kokoro_voice_id(voice: str | None) -> str | None:
    """Kokoro voice id for a dropdown label, or None when the label names a system voice."""
    if not voice:
        return KOKORO_DEFAULT if kokoro_installed() else None
    if voice.startswith(KOKORO_PREFIX):
        return voice[len(KOKORO_PREFIX):].split()[0]
    return None


def engine() -> str | None:
    if kokoro_installed():
        return "kokoro"
    return system_engine()


def system_engine() -> str | None:
    if sys.platform == "win32" and _powershell():
        return "sapi"
    if shutil.which("say"):
        return "say"
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak"
    return None


def list_voices() -> list[str]:
    """Kokoro voices first (the default), then the optional system voices (Microsoft SAPI / macOS say)."""
    return kokoro_voices() + system_voices()


def system_voices() -> list[str]:
    kind = system_engine()
    try:
        if kind == "sapi":
            script = ("Add-Type -AssemblyName System.Speech; "
                      "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
                      "Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name }")
            out = run_cmd([_powershell(), "-NoProfile", "-NonInteractive", "-Command", script]).stdout
            return [line.strip() for line in out.splitlines() if line.strip()]
        if kind == "say":
            out = run_cmd(["say", "-v", "?"]).stdout
            return [re.split(r"\s{2,}", line.strip())[0] for line in out.splitlines() if "en_" in line]
    except RuntimeError:
        return []
    return []


def clean_for_speech(text: str) -> str:
    text = re.sub(r"[*_#`>]+", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    text = clean_for_speech(text)
    parts = re.split(r"(?<=[.!?])\s+|\s*\n+\s*", text)
    return [part.strip() for part in parts if part.strip()]


def synthesize(texts: list[str], out_dir: Path, voice: str | None = None, rate: int = 0,
               on_count=None) -> list[Path]:
    """Speak each text into its own mono 16-bit WAV file. Returns the paths in order.

    Uses Kokoro unless `voice` names a system voice. `rate` runs from -5 (slow) to 5 (fast).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = [out_dir / f"tts_{index:03d}.wav" for index in range(len(texts))]
    kokoro_voice = _kokoro_voice_id(voice)
    kind = "kokoro" if kokoro_voice else system_engine()
    if kind is None:
        raise RuntimeError("No local speech synthesizer found (Kokoro, Windows SAPI, macOS say or espeak-ng).")
    if kind == "kokoro":
        import soundfile

        model = _kokoro_model()
        lang = KOKORO_ACCENTS.get(kokoro_voice[0], ("", "en-us"))[1]
        speed = min(1.5, max(0.5, 1.0 + 0.1 * int(rate)))
        for done, (text, path) in enumerate(zip(texts, outputs)):
            if on_count:
                on_count(done, len(texts))
            samples, sample_rate = model.create(text, voice=kokoro_voice, speed=speed, lang=lang)
            soundfile.write(str(path), samples, sample_rate, subtype="PCM_16")
    elif kind == "sapi":
        jobs = out_dir / "tts_jobs.json"
        jobs.write_text(json.dumps([{"text": text, "out": str(path.resolve())} for text, path in zip(texts, outputs)]),
                        encoding="utf-8")
        script = out_dir / "tts_sapi.ps1"
        script.write_text(_SAPI_SCRIPT, encoding="utf-8")
        run_cmd([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                 "-Jobs", str(jobs), "-Voice", voice or "", "-Rate", str(int(rate))])
    else:
        for index, (text, path) in enumerate(zip(texts, outputs)):
            if on_count:
                on_count(index, len(texts))
            text_path = out_dir / f"tts_{index:03d}.txt"
            text_path.write_text(text, encoding="utf-8")
            if kind == "say":
                cmd = ["say", "-f", str(text_path), "-o", str(path), f"--data-format=LEI16@{SAMPLE_RATE}"]
                if voice:
                    cmd += ["-v", voice]
            else:
                cmd = [shutil.which("espeak-ng") or shutil.which("espeak"), "-f", str(text_path), "-w", str(path)]
                if voice:
                    cmd += ["-v", voice]
            run_cmd(cmd)
    missing = [str(path) for path in outputs if not path.exists() or path.stat().st_size <= 44]
    if missing:
        raise RuntimeError(f"Speech synthesis produced no audio for: {', '.join(missing)}")
    return outputs


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def join_wavs(parts: list[Path], dest: Path, *, lead: float = 0.3, gap: float = 0.25, tail: float = 0.5) -> list[float]:
    """Concatenate WAVs with silence between them. Returns each part's start time in `dest`."""
    with wave.open(str(parts[0]), "rb") as first:
        params = first.getparams()
    silence = lambda seconds: b"\x00" * (int(params.framerate * seconds) * params.sampwidth * params.nchannels)
    starts, cursor = [], lead
    with wave.open(str(dest), "wb") as out:
        out.setparams(params)
        out.writeframes(silence(lead))
        for index, part in enumerate(parts):
            with wave.open(str(part), "rb") as handle:
                if (handle.getframerate(), handle.getsampwidth(), handle.getnchannels()) != \
                        (params.framerate, params.sampwidth, params.nchannels):
                    raise RuntimeError("Speech clips have mismatched audio formats.")
                frames = handle.readframes(handle.getnframes())
                starts.append(cursor)
                out.writeframes(frames)
                cursor += handle.getnframes() / float(handle.getframerate())
            pause = gap if index < len(parts) - 1 else tail
            out.writeframes(silence(pause))
            cursor += pause
    return starts


def speak_title(text: str, dest: Path, voice: str | None = None) -> Path:
    """Single-utterance helper used for storyboard intro narration."""
    work = dest.parent / f"{dest.stem}_tts"
    [wav] = synthesize([clean_for_speech(text)], work, voice)
    dest = dest.with_suffix(".wav")
    dest.write_bytes(wav.read_bytes())
    return dest
