import json
import tempfile
import unittest
import wave
from pathlib import Path

from pipeline import illustration


def shape(kind, points, fill="#2a9d8f", opacity=1, width=0):
    return {"type": kind, "fill": fill, "opacity": opacity, "line_width": width, "points": points}


def scene(shapes):
    return {"background_top": "#1d3557", "background_bottom": "#457b9d", "shapes": shapes}


GOOD = [shape("rect", [0, 0.7, 1, 1]), shape("ellipse", [0.1, 0.1, 0.3, 0.3], "#ff856b"),
        shape("polygon", [0.6, 0.7, 0.8, 0.3, 0.95, 0.7], "#264653"), shape("line", [0.05, 0.9, 0.95, 0.85], "#e9c46a", 1, 0.01),
        shape("rect", [0.2, 0.5, 0.25, 0.7], "#f4a261"), shape("rect", [0.3, 0.45, 0.35, 0.7], "#f4a261")]


class IllustrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_invalid_shapes_are_dropped_and_coordinates_clamped(self):
        bad = [shape("rect", [0, 0, 0.001, 0.001]), shape("rect", [0, 0, 1, 1], "red"), shape("star", [0, 0, 1, 1]),
               shape("polygon", [0, 0, 1, 1]), {"type": "rect", "fill": "#ffffff", "points": [0, 0, "x", 1]},
               shape("rect", [-5, -5, 9, 9], "#111111")]
        cleaned = illustration.sanitize_scene(scene(GOOD + bad))
        self.assertEqual(len(cleaned["shapes"]), len(GOOD) + 1)
        self.assertEqual(cleaned["shapes"][-1]["points"], [0.0, 0.0, 1.0, 1.0])

    def test_too_few_usable_shapes_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "usable shapes"):
            illustration.sanitize_scene(scene(GOOD[:3]))
        with self.assertRaisesRegex(ValueError, "background"):
            illustration.sanitize_scene({"background_top": "blue", "background_bottom": "#000000", "shapes": GOOD})

    def test_scene_renders_for_both_aspects_and_blank_scene_is_rejected(self):
        for aspect, size in (("16:9", (1280, 720)), ("9:16", (720, 1280))):
            from PIL import Image
            path = self.root / f"{aspect.replace(':', 'x')}.png"
            illustration.render_scene(json.dumps(scene(GOOD)), aspect, path)
            with Image.open(path) as image:
                self.assertEqual(image.size, size)
        flat = {"background_top": "#222222", "background_bottom": "#222222",
                "shapes": [shape("rect", [0.1 * i, 0.1, 0.1 * i + 0.05, 0.2], "#222222") for i in range(1, 8)]}
        with self.assertRaisesRegex(ValueError, "nearly blank"):
            illustration.render_scene(flat, "16:9", self.root / "blank.png")

    def test_music_is_audible_and_not_clipped(self):
        notes = [{"midi": 48 + 3 * (i % 5), "start_beat": i, "beats": 1.5} for i in range(16)]
        path = self.root / "music.wav"
        illustration.render_music({"tempo_bpm": 90, "notes": notes}, path)
        with wave.open(str(path)) as stream:
            frames = stream.readframes(stream.getnframes())
            self.assertGreaterEqual(stream.getnframes() / stream.getframerate(), 12)
        import numpy as np
        samples = np.frombuffer(frames, dtype="<i2") / 32768
        self.assertGreater(float(np.sqrt(np.mean(samples ** 2))), 0.03)
        self.assertLess(float(np.max(np.abs(samples))), 0.9)

    def test_music_rejects_too_few_or_out_of_range_notes(self):
        notes = [{"midi": 10, "start_beat": i, "beats": 1} for i in range(20)]
        with self.assertRaisesRegex(ValueError, "usable notes"):
            illustration.sanitize_music({"tempo_bpm": 80, "notes": notes})


if __name__ == "__main__":
    unittest.main()
