"""Render the curated background-music library, one folder per audience profile.

Original compositions played by a small additive synthesizer. Output is
deterministic: re-running reproduces the same files. Nothing is downloaded and
no model is called, so the result carries no third-party licence.

    python3 tools/generate_music.py
"""
from __future__ import annotations

import json
import subprocess
import wave
import zlib
from pathlib import Path

import numpy as np
from scipy.signal import fftconvolve

RATE = 44100
SECONDS = 64.0
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "music"

TRIADS = {"maj": (0, 4, 7), "min": (0, 3, 7), "sus": (0, 5, 7)}

INSTRUMENTS = {
    # partials are (harmonic multiple, relative level)
    "pad": {"kind": "sustained", "attack": 0.9, "release": 1.2, "detune": 6.0,
            "partials": [(1, 1.0), (2, 0.42), (3, 0.17), (4, 0.09), (6, 0.04)]},
    "pluck": {"kind": "percussive", "attack": 0.004, "half_life": 0.50, "detune": 2.5,
              "partials": [(1, 1.0), (2, 0.44), (3, 0.21), (4, 0.11), (5, 0.05)]},
    "marimba": {"kind": "percussive", "attack": 0.002, "half_life": 0.24, "detune": 0.0,
                "partials": [(1, 1.0), (4, 0.33), (10, 0.08)]},
    "bass": {"kind": "percussive", "attack": 0.014, "half_life": 0.85, "detune": 0.0,
             "partials": [(1, 1.0), (2, 0.20), (3, 0.06)]},
}


def midi_hz(midi: float) -> float:
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def env_sustained(length: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(length)
    a = min(length, max(1, int(attack * RATE)))
    env[:a] = np.sin(np.linspace(0.0, np.pi / 2, a)) ** 2
    r = min(length, max(1, int(release * RATE)))
    env[length - r:] *= np.cos(np.linspace(0.0, np.pi / 2, r)) ** 2
    return env


def env_percussive(length: int, attack: float, half_life: float) -> np.ndarray:
    env = 0.5 ** (np.arange(length) / (half_life * RATE))
    a = min(length, max(1, int(attack * RATE)))
    env[:a] *= np.linspace(0.0, 1.0, a)
    return env


def voice(instrument: str, midi: float, seconds: float, amp: float) -> np.ndarray:
    spec = INSTRUMENTS[instrument]
    length = int(seconds * RATE)
    if length <= 0:
        return np.zeros(0)
    t = np.arange(length) / RATE
    base = midi_hz(midi)
    detune = spec["detune"]
    offsets = [0.0, detune, -detune] if detune else [0.0]
    tone = np.zeros(length)
    for cents in offsets:
        freq = base * 2.0 ** (cents / 1200.0)
        for multiple, level in spec["partials"]:
            if freq * multiple > RATE / 2.2:
                continue
            tone += level * np.sin(2 * np.pi * freq * multiple * t)
    tone /= len(offsets)
    if spec["kind"] == "sustained":
        env = env_sustained(length, spec["attack"], spec["release"])
    else:
        env = env_percussive(length, spec["attack"], spec["half_life"])
    return tone * env * amp


def woodblock(seconds: float, amp: float, seed: int) -> np.ndarray:
    """A soft rounded click. Deliberately quiet: nothing in the Kids mix may startle."""
    length = int(seconds * RATE)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, 1.0, length)
    t = np.arange(length) / RATE
    shaped = noise * np.sin(2 * np.pi * 900 * t)
    return shaped * env_percussive(length, 0.002, 0.035) * amp


def place(left: np.ndarray, right: np.ndarray, start: int, signal: np.ndarray, pan: float) -> None:
    end = min(len(left), start + len(signal))
    if end <= start or start < 0:
        return
    chunk = signal[:end - start]
    angle = (np.clip(pan, -1.0, 1.0) + 1.0) * np.pi / 4
    left[start:end] += chunk * np.cos(angle)
    right[start:end] += chunk * np.sin(angle)


def reverb(signal: np.ndarray, decay: float, wet: float, seed: int) -> np.ndarray:
    taps = int(decay * RATE)
    rng = np.random.default_rng(seed)
    impulse = rng.normal(0.0, 1.0, taps) * np.exp(-np.arange(taps) / (decay * RATE / 4.5))
    impulse[0] = 1.0
    tail = fftconvolve(signal, impulse)[:len(signal)]
    dry_peak = float(np.max(np.abs(signal))) or 1.0
    tail_peak = float(np.max(np.abs(tail))) or 1.0
    return (1.0 - wet) * signal + wet * tail / tail_peak * dry_peak


def chord_notes(root: int, quality: str) -> list[int]:
    return [root + step for step in TRIADS[quality]]


def render_ambient(track: dict, left: np.ndarray, right: np.ndarray) -> None:
    """Researcher: sustained pad, sparse high notes, soft root. Nothing percussive."""
    beat = 60.0 / track["bpm"]
    bar = beat * 4
    melody_steps = [0, 7, 4, 12, 7, 0, 4, 7]
    index = 0
    position = 0.0
    while position < SECONDS:
        root, quality = track["progression"][index % len(track["progression"])]
        start = int(position * RATE)
        for order, note in enumerate(chord_notes(root, quality)):
            pan = (order - 1) * 0.45
            place(left, right, start, voice("pad", note + 12, bar + 1.4, 0.26), pan)
        place(left, right, start, voice("bass", root - 12, bar * 0.9, 0.22), 0.0)
        step = melody_steps[index % len(melody_steps)]
        place(left, right, start + int(beat * 2 * RATE),
              voice("pad", root + 24 + step, bar * 0.8, 0.13), 0.25)
        position += bar
        index += 1


def render_acoustic(track: dict, left: np.ndarray, right: np.ndarray) -> None:
    """General public: steady plucked arpeggio with a light walking root."""
    beat = 60.0 / track["bpm"]
    bar = beat * 4
    pattern = [0, 1, 2, 1, 2, 1, 0, 1]
    melody_steps = [12, 14, 16, 14, 12, 9, 12, 16]
    index = 0
    position = 0.0
    while position < SECONDS:
        root, quality = track["progression"][index % len(track["progression"])]
        notes = chord_notes(root, quality)
        start = int(position * RATE)
        for eighth, choice in enumerate(pattern):
            offset = start + int(eighth * beat / 2 * RATE)
            pan = -0.4 + 0.8 * (eighth % 3) / 2
            place(left, right, offset, voice("pluck", notes[choice] + 12, beat * 1.4, 0.21), pan)
        place(left, right, start, voice("bass", root - 12, beat * 1.8, 0.25), 0.0)
        place(left, right, start + int(beat * 2 * RATE), voice("bass", root - 5, beat * 1.6, 0.18), 0.0)
        if index % 2 == 1:
            step = melody_steps[index % len(melody_steps)]
            place(left, right, start + int(beat * RATE), voice("pluck", root + step, beat * 2.2, 0.17), 0.3)
        position += bar
        index += 1


def render_playful(track: dict, left: np.ndarray, right: np.ndarray) -> None:
    """Kids: bouncy marimba figure, soft block on the backbeat, pentatonic motif."""
    beat = 60.0 / track["bpm"]
    bar = beat * 4
    pattern = [0, 2, 1, 2, 0, 2, 1, 2]
    motif = [0, 2, 4, 2, 7, 4, 2, 0]
    pentatonic = [0, 2, 4, 7, 9]
    index = 0
    position = 0.0
    while position < SECONDS:
        root, quality = track["progression"][index % len(track["progression"])]
        notes = chord_notes(root, quality)
        start = int(position * RATE)
        for eighth, choice in enumerate(pattern):
            offset = start + int(eighth * beat / 2 * RATE)
            pan = -0.35 if eighth % 2 else 0.35
            place(left, right, offset, voice("marimba", notes[choice] + 12, beat * 1.1, 0.24), pan)
        place(left, right, start, voice("bass", root - 12, beat * 1.5, 0.23), 0.0)
        for backbeat in (1, 3):
            place(left, right, start + int(backbeat * beat * RATE), woodblock(0.12, 0.05, 11 + index), 0.15)
        if index % 2 == 0:
            degree = pentatonic[motif[index % len(motif)] % len(pentatonic)]
            place(left, right, start + int(beat * 2.5 * RATE),
                  voice("marimba", root + 24 + degree, beat * 1.2, 0.16), -0.2)
        position += bar
        index += 1


RENDERERS = {"ambient": render_ambient, "acoustic": render_acoustic, "playful": render_playful}

# Original progressions, written for this library. Roots are MIDI note numbers.
TRACKS = [
    {"audience": "Researcher", "slug": "researcher", "style": "ambient", "bpm": 68,
     "name": "still-water", "title": "Still Water", "reverb": (2.0, 0.42),
     "progression": [(57, "min"), (53, "maj"), (50, "min"), (52, "maj")],
     "note": "Slow minor pad for sober, evidence-led narration."},
    {"audience": "Researcher", "slug": "researcher", "style": "ambient", "bpm": 64,
     "name": "slow-orbit", "title": "Slow Orbit", "reverb": (2.2, 0.45),
     "progression": [(50, "min"), (46, "maj"), (53, "maj"), (48, "maj")],
     "note": "Darker and wider; suits methods and limitations."},
    {"audience": "Researcher", "slug": "researcher", "style": "ambient", "bpm": 72,
     "name": "quiet-field", "title": "Quiet Field", "reverb": (1.8, 0.38),
     "progression": [(52, "min"), (48, "maj"), (57, "min"), (47, "maj")],
     "note": "Slightly brighter minor; suits an open research question."},
    {"audience": "General public", "slug": "general-public", "style": "acoustic", "bpm": 100,
     "name": "open-road", "title": "Open Road", "reverb": (1.3, 0.24),
     "progression": [(48, "maj"), (55, "maj"), (57, "min"), (53, "maj")],
     "note": "Warm and forward; the default for everyday explainers."},
    {"audience": "General public", "slug": "general-public", "style": "acoustic", "bpm": 96,
     "name": "clear-morning", "title": "Clear Morning", "reverb": (1.4, 0.26),
     "progression": [(53, "maj"), (48, "maj"), (50, "min"), (46, "maj")],
     "note": "Gentler pace for longer explanations."},
    {"audience": "General public", "slug": "general-public", "style": "acoustic", "bpm": 104,
     "name": "bright-steps", "title": "Bright Steps", "reverb": (1.2, 0.22),
     "progression": [(55, "maj"), (50, "maj"), (52, "min"), (48, "maj")],
     "note": "Highest energy of the three; good for a surprising result."},
    {"audience": "Kids", "slug": "kids", "style": "playful", "bpm": 108,
     "name": "wonder-walk", "title": "Wonder Walk", "reverb": (1.1, 0.20),
     "progression": [(48, "maj"), (53, "maj"), (55, "maj"), (48, "maj")],
     "note": "Major and settled; the safe default for ages 8-12."},
    {"audience": "Kids", "slug": "kids", "style": "playful", "bpm": 112,
     "name": "puzzle-time", "title": "Puzzle Time", "reverb": (1.1, 0.20),
     "progression": [(55, "maj"), (48, "maj"), (50, "maj"), (55, "maj")],
     "note": "A little springier; suits a question-led opening."},
    {"audience": "Kids", "slug": "kids", "style": "playful", "bpm": 104,
     "name": "tiny-discovery", "title": "Tiny Discovery", "reverb": (1.2, 0.22),
     "progression": [(53, "maj"), (46, "maj"), (48, "maj"), (53, "maj")],
     "note": "Softest of the three; good for a calm takeaway."},
]


def render(track: dict, destination: Path) -> dict:
    length = int(SECONDS * RATE)
    left, right = np.zeros(length), np.zeros(length)
    RENDERERS[track["style"]](track, left, right)

    decay, wet = track["reverb"]
    seed = zlib.crc32(track["name"].encode()) % 10_000  # hash() is salted per process and would break reproducibility
    left = reverb(left, decay, wet, seed)
    right = reverb(right, decay, wet, seed + 1)

    fade_in, fade_out = int(1.2 * RATE), int(2.5 * RATE)
    shape = np.ones(length)
    shape[:fade_in] = np.sin(np.linspace(0.0, np.pi / 2, fade_in)) ** 2
    shape[-fade_out:] = np.cos(np.linspace(0.0, np.pi / 2, fade_out)) ** 2
    left, right = left * shape, right * shape

    stereo = np.stack([left, right], axis=1)
    peak = float(np.max(np.abs(stereo)))
    if peak <= 0:
        raise SystemExit(f"{track['name']}: rendered silence")
    stereo = stereo / peak * 0.5
    rms = float(np.sqrt(np.mean(stereo ** 2)))

    wav = destination.with_suffix(".wav")
    wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(wav), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((stereo * 32767).astype("<i2").tobytes())

    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                    "-codec:a", "libmp3lame", "-b:a", "192k", str(destination)], check=True)
    wav.unlink()
    return {"peak": round(peak, 4), "rms_dbfs": round(20 * np.log10(rms), 2)}


def main() -> None:
    entries = []
    for track in TRACKS:
        destination = OUT / track["slug"] / f"{track['name']}.mp3"
        stats = render(track, destination)
        entries.append({
            "file": str(destination.relative_to(OUT)),
            "audience": track["audience"],
            "title": track["title"],
            "bpm": track["bpm"],
            "seconds": SECONDS,
            "note": track["note"],
            "license": "CC0-1.0",
            "source": "tools/generate_music.py in this repository",
            "credit": "Original synthesized composition; no third-party recording or sample.",
            "redistributable": True,
        })
        print(f"{destination.relative_to(ROOT)}  peak={stats['peak']}  rms={stats['rms_dbfs']} dBFS")

    manifest = {
        "version": "1.0",
        "description": "Curated instrumental beds, three per audience profile. Instrumental only, no vocals.",
        "license": "CC0-1.0",
        "redistributable": True,
        "tracks": entries,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(entries)} tracks -> {OUT.relative_to(ROOT)}/manifest.json")


if __name__ == "__main__":
    main()
