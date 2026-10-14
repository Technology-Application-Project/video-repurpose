import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline import transcribe as T

PAYLOAD = {"language": "en", "segments": [{"start": 0.0, "end": 2.0, "text": "Retrosplenial cortex."}]}


class TranscriptCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / "lecture.mp4"
        self.source.write_bytes(b"lecture audio")
        patcher = patch.object(T, "CACHE_DIR", self.root / "cache")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_asr(self, model="small", source=None):
        work = self.root / f"run-{len(list(self.root.glob('run-*')))}"
        work.mkdir()
        return T.transcribe_video(str(source or self.source), work, model), work

    def test_second_transcription_of_same_file_is_a_cache_hit(self):
        with patch.object(T, "_transcribe", return_value=PAYLOAD) as asr:
            first, _ = self.run_asr()
            second, work = self.run_asr()
        asr.assert_called_once()
        self.assertFalse(first["asr"]["cached"])
        self.assertTrue(second["asr"]["cached"])
        self.assertEqual(second["segments"], PAYLOAD["segments"])
        self.assertEqual(json.loads((work / "transcript.json").read_text())["segments"], PAYLOAD["segments"])
        self.assertTrue((work / "transcript.srt").read_text().strip())

    def test_cache_key_changes_with_model_content_and_compute_type(self):
        other = self.root / "other.mp4"
        other.write_bytes(b"different lecture")
        with patch.object(T, "_transcribe", return_value=PAYLOAD) as asr:
            self.run_asr("small")
            self.run_asr("large-v3")
            self.run_asr("small", other)
            with patch.object(T, "_compute_type", return_value="float32"):
                self.run_asr("small")
        self.assertEqual(asr.call_count, 4)

    def test_corrupt_or_wrong_format_cache_is_ignored_and_rebuilt(self):
        with patch.object(T, "_transcribe", return_value=PAYLOAD) as asr:
            self.run_asr()
            entry = next((self.root / "cache").glob("*.json"))
            for damaged in ("not json", json.dumps({"format": "0", **PAYLOAD}),
                            json.dumps({"format": T.TRANSCRIPT_CACHE_VERSION, "language": "en", "segments": []})):
                entry.write_text(damaged)
                result, _ = self.run_asr()
                self.assertFalse(result["asr"]["cached"])
        self.assertEqual(asr.call_count, 4)

    def test_cpu_uses_int8_and_unknown_model_is_rejected(self):
        self.assertEqual(T._compute_type("cpu"), "int8")
        self.assertEqual(T._compute_type("cuda"), "float16")
        with self.assertRaisesRegex(ValueError, "Choose a transcription model"):
            T.get_whisper_model("tiny-typo")

    def test_model_is_loaded_once_per_model_and_compute_type(self):
        loaded = []
        fake = type("FakeWhisperx", (), {"load_model": staticmethod(lambda size, device, compute_type: loaded.append((size, compute_type)) or object())})
        with patch.dict("sys.modules", {"whisperx": fake}), patch.dict(T._whisper_models, clear=True):
            T.get_whisper_model("small")
            T.get_whisper_model("small")
            T.get_whisper_model("large-v3")
        self.assertEqual(loaded, [("small", "int8"), ("large-v3", "int8")])


if __name__ == "__main__":
    unittest.main()
