"""Synthesise a demo's narration sentence by sentence with local Kokoro and record the measured timings.

Usage: python tools/make_voiceover.py [SCRIPT_JSON] [OUT_DIR]
Defaults to the stakeholder intro; writes OUT_DIR/audio/<scene>-<n>.aiff and OUT_DIR/timings.json.
Each sentence is its own clip, so the recorder can start an on-screen action exactly when its sentence starts.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.kokoro_tts import synthesize  # noqa: E402


def duration(path: Path) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                            capture_output=True, text=True, check=True)
    return float(result.stdout)


def main() -> None:
    script_path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "stakeholder-intro" / "script.json"
    out = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / "work" / "stakeholder-intro"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    (out / "audio").mkdir(parents=True, exist_ok=True)
    scenes = []
    for scene in script["scenes"]:
        sentences = []
        for index, text in enumerate(re.split(r"(?<=[.!?])\s+", scene["text"].strip())):
            audio = synthesize(text, out / "audio" / f"{scene['id']}-{index}.aiff", voice=script["voice"], speed=script["speed"])
            sentences.append({"text": text, "audio": str(audio), "seconds": round(duration(audio), 3)})
        scenes.append({**scene, "sentences": sentences})
        print(f"{scene['id']:<11} {' + '.join(f'{item['seconds']:.2f}' for item in sentences)} s")
    (out / "timings.json").write_text(json.dumps({"voice": script["voice"], "speed": script["speed"], "scenes": scenes}, indent=2), encoding="utf-8")
    print(f"speech total {sum(item['seconds'] for scene in scenes for item in scene['sentences']):.1f}s")


if __name__ == "__main__":
    main()
