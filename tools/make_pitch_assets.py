"""Lay the measured pitch narration on one timeline: writes docs/pitch/voice-over.wav and docs/pitch/timings.js.

Usage: python tools/make_pitch_assets.py [WORK_DIR] [SUFFIX]
  after: python tools/make_voiceover.py docs/pitch/script.json work/pitch
  e.g.   python tools/make_pitch_assets.py pitch-v4 -v4   writes voice-over-v4.wav and timings-v4.js
The HTML page reads timings.js as a script because browsers block fetch() of local JSON files.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "work" / (sys.argv[1] if len(sys.argv) > 1 else "pitch") / "timings.json"
SUFFIX = sys.argv[2] if len(sys.argv) > 2 else ""
OUT = ROOT / "docs" / "pitch"
RATE = 24000
LEAD, TAIL, GAP = 0.6, 0.4, 0.1  # seconds before the first sentence, after the last, between sentences


def pcm(path: Path, tmp: Path) -> bytes:
    wav = tmp / (path.stem + ".wav")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(wav)], check=True)
    with wave.open(str(wav)) as handle:
        return handle.readframes(handle.getnframes())


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * round(seconds * RATE)


def main() -> None:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    audio = bytearray()
    scenes = []
    with tempfile.TemporaryDirectory() as name:
        tmp = Path(name)
        for scene in data["scenes"]:
            start = len(audio) / 2 / RATE
            audio += silence(LEAD)
            sentences = []
            for index, sentence in enumerate(scene["sentences"]):
                begin = len(audio) / 2 / RATE
                audio += pcm(Path(sentence["audio"]), tmp)
                end = len(audio) / 2 / RATE
                sentences.append({"text": sentence["text"], "start": round(begin, 3), "end": round(end, 3)})
                if index < len(scene["sentences"]) - 1:
                    audio += silence(GAP)
            audio += silence(TAIL)
            scenes.append({"id": scene["id"], "label": scene["label"], "start": round(start, 3),
                           "end": round(len(audio) / 2 / RATE, 3), "sentences": sentences})
    total = round(len(audio) / 2 / RATE, 3)
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"voice-over{SUFFIX}.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(bytes(audio))
    payload = {"voice": data["voice"], "speed": data["speed"], "total": total, "scenes": scenes}
    (OUT / f"timings{SUFFIX}.js").write_text("window.PITCH_TIMINGS = " + json.dumps(payload, indent=1) + ";\n", encoding="utf-8")
    print(f"total {total:.1f}s")
    for scene in scenes:
        print(f"{scene['id']:<12} {scene['start']:7.1f} - {scene['end']:7.1f}  ({scene['end'] - scene['start']:.1f}s)")


if __name__ == "__main__":
    main()
