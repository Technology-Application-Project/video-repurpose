import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import app

from pipeline.assemble import assemble_compilation, burn_captions
from pipeline.export import export_clips
from pipeline.storyboard import draft_storyboard
from pipeline.utils import ffmpeg_bin, format_timestamp, media_duration


@unittest.skipUnless(os.environ.get("RUN_MEDIA_TESTS") == "1", "Set RUN_MEDIA_TESTS=1 for ffmpeg integration tests")
class MediaTests(unittest.TestCase):
    @unittest.skipUnless(__import__("pipeline.narrate", fromlist=["engine"]).engine(), "Requires a local speech synthesizer")
    def test_narrated_opening_finishes_before_speech_and_captions(self):
        import json
        from array import array

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([ffmpeg_bin(), "-v", "error", "-y", "-f", "lavfi", "-i",
                            "color=c=green:size=320x180:rate=30", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=44100", "-t", "1", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True, capture_output=True)
            title = "Understanding complex relationships requires careful analysis of evidence and assumptions"
            clips = [{"id": "c1", "start": 0, "end": 1, "title": title,
                      "takeaway": "Examine assumptions.", "excerpt": "Source speech."}]
            draft = draft_storyboard(clips, "Students")
            for preview in (True, False):
                work = root / ("preview" if preview else "export")
                output = assemble_compilation([source], work / "out.mp4", intro_title="", clips=clips,
                    segments=[{"start": 0, "end": 1, "text": "Source speech."}], storyboard=draft,
                    work=work, srt_path=work / "captions.srt", preview=preview)
                timeline = json.loads((work / "timeline.json").read_text())
                narration_seconds = media_duration(next(work.glob("intro_narration.*[!t]")))
                opening_seconds = timeline[0]["duration"]
                self.assertGreater(opening_seconds, 3)
                self.assertGreaterEqual(opening_seconds + 0.04, narration_seconds + 0.4)
                self.assertAlmostEqual(timeline[1]["start"], opening_seconds, delta=0.04)
                self.assertIn(format_timestamp(opening_seconds), (work / "captions.srt").read_text())
                self.assertAlmostEqual(media_duration(output), opening_seconds + 6, delta=0.15)
                audio = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(output), "-t", "2",
                                        "-vn", "-ac", "1", "-f", "f32le", "pipe:1"], check=True, capture_output=True)
                samples = array("f", audio.stdout)
                self.assertGreater(sum(sample * sample for sample in samples) / len(samples), 0.00001)

    def test_single_clip_export_has_opening_captions_ending_and_no_divider(self):
        import json
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([ffmpeg_bin(), "-y", "-f", "lavfi", "-i", "color=c=green:size=320x180:rate=30",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "40",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
                           check=True, capture_output=True)
            clips = [{"id": "c1", "start": 0, "end": 40, "title": "An example",
                      "takeaway": "Examine assumptions.", "excerpt": "Single example."}]
            segments = [{"start": 0, "end": 2, "text": "Single example."}]
            storyboard = draft_storyboard(clips, "Students")
            storyboard["opening"].pop("narration")
            storyboard["duration_budget"] = {"min_seconds": 45, "max_seconds": 120}
            state = app.empty_state()
            state.update(run_id="single", source_video=str(source), source_duration=40,
                         work=str(root / "work"), candidates=clips, transcript={"segments": segments},
                         target_audience="Students")
            rows = []
            for field in ("id", "title", "takeaway", "start", "end", "keep"):
                rows.extend([True if field == "keep" else clips[0][field],
                             *[False if field == "keep" else None] * (app.MAX_REVIEW - 1)])
            with patch("app.generate_storyboard", return_value=storyboard):
                outputs = list(app.confirm_and_generate_storyboard(state, "Original", "fixture-model",
                                                                  *rows, progress=Mock()))
            generated = outputs[-1][2 + 3 * app.MAX_KEEP:]
            state = generated[0]
            self.assertIn("Preview ready", generated[1])
            self.assertTrue(Path(generated[2]).is_file())
            self.assertAlmostEqual(media_duration(generated[2]), 48, delta=0.15)
            self.assertIsNone(state["storyboard_approval"])
            values = [*generated[6:-(app.MAX_KEEP - 1)], "Original"]
            state = app.approve_package_storyboard(state, True, "Test fixture reviewer", *values)[0]
            with patch("pipeline.export.EXPORT_ROOT", root / "exports"), patch("app.EXPORT_ROOT", root / "exports"):
                result = app.export_package_storyboard(state, True, "Test fixture reviewer", *values,
                                                      *rows, progress=Mock())
                compilation, bundle = result[2], Path(result[4])
                export_key = Path(compilation).parent.relative_to(root / "exports").as_posix()
                loaded = app.load_assessment_export(export_key)
                self.assertEqual(Path(loaded[1]).resolve(), Path(compilation).resolve())
                self.assertIn("Students", loaded[3])
                fields = [*(["not_assessed"] * 5), *([""] * 5), *(["not_assessed"] * 4), *([""] * 4)]
                summary, files = app.save_ui_assessment(loaded[0], export_key, "Test fixture reviewer", *fields)
                self.assertIn("**blocked**", summary)
                self.assertTrue(all(Path(path).is_file() for path in files))
            metadata = json.loads(Path(compilation).with_name("selection.json").read_text())
            self.assertEqual(metadata["storyboard"]["transitions"], [])
            self.assertEqual([item["kind"] for item in metadata["timeline"]], ["opening", "clip", "ending"])
            self.assertAlmostEqual(media_duration(compilation), 48, delta=0.15)
            self.assertIn("00:00:03,000", Path(compilation).with_name("captions.srt").read_text())
            self.assertEqual(len(list((root / "work").glob("exports/*/assemble/label_*.png"))), 0)
            from array import array

            for start, silent in [(0.5, True), (3.5, False)]:
                audio = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", str(start), "-i", compilation,
                                        "-t", "0.25", "-vn", "-ac", "1", "-f", "f32le", "pipe:1"],
                                       check=True, capture_output=True)
                samples = array("f", audio.stdout)
                self.assertTrue(samples)
                energy = sum(sample * sample for sample in samples) / len(samples)
                self.assertLess(energy, 0.00001) if silent else self.assertGreater(energy, 0.001)
            with zipfile.ZipFile(bundle) as archive:
                self.assertIsNone(archive.testzip())
            subprocess.run([ffmpeg_bin(), "-v", "error", "-i", compilation, "-f", "null", "-"],
                           check=True, capture_output=True)

    def test_storyboard_overlay_divider_and_caption_timeline(self):
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([ffmpeg_bin(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
                           check=True, capture_output=True)
            clips = [{"id": f"c{index}", "start": index, "end": index + 1,
                      "title": "An example", "takeaway": "Examine assumptions.", "excerpt": "Example."}
                     for index in range(3)]
            segments = [{"start": index, "end": index + 0.8, "text": f"Caption {index}"} for index in range(3)]
            draft = draft_storyboard(clips, "Beginners")
            draft["opening"].pop("narration")
            draft["transitions"][0].update(mode="Divider", label="Another example")
            draft["transitions"][1].update(mode="Overlay", label="A limitation")
            for aspect in ("16:9", "9:16"):
                draft["aspect_ratio"] = aspect
                work = root / aspect.replace(":", "-")
                output = assemble_compilation([source] * 3, work / "out.mp4", intro_title="",
                                              clips=clips, segments=segments, storyboard=draft, work=work,
                                              srt_path=work / "captions.srt", aspect_ratio=aspect, preview=True)
                timeline = json.loads((work / "timeline.json").read_text())
                self.assertAlmostEqual(media_duration(output), 12.5, delta=0.2)
                self.assertAlmostEqual(timeline[3]["start"], 5.5, delta=0.08)
                captions = (work / "captions.srt").read_text()
                self.assertIn("00:00:03,000", captions)
                self.assertEqual(captions.count("Caption"), 3)
                subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(output), "-f", "null", "-"],
                               check=True, capture_output=True)
            export_clips_source = root / "long.mp4"
            subprocess.run([ffmpeg_bin(), "-y", "-stream_loop", "2", "-i", str(source), "-t", "3",
                            "-c", "copy", str(export_clips_source)], check=True, capture_output=True)
            with patch("pipeline.export.EXPORT_ROOT", root / "exports"):
                _, post, bundle, compilation = export_clips(
                    run_id="storyboard", source_video=str(export_clips_source), segments=segments, clips=clips,
                    source_url="", work=root / "export-work", cta_text="", storyboard=draft,
                    storyboard_approval={"reviewer": "Fixture", "revision": "test-only"}, aspect_ratio="9:16")
            selection = json.loads(Path(compilation).with_name("selection.json").read_text())
            self.assertEqual(selection["storyboard"], draft)
            self.assertEqual(selection["storyboard_approval"]["reviewer"], "Fixture")
            self.assertEqual(len(selection["timeline"]), 6)
            self.assertIn(draft["ending"]["question"], post)
            self.assertTrue(bundle.is_file())

    def test_caption_batches_preserve_duration(self):
        for with_audio in (True, False):
            with self.subTest(with_audio=with_audio), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source.mp4"
                command = [ffmpeg_bin(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30"]
                if with_audio:
                    command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-c:a", "aac"]
                command += ["-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)]
                subprocess.run(command, check=True, capture_output=True)
                cues = [{"start": index * 0.06, "end": index * 0.06 + 0.04, "text": "Test"}
                        for index in range(40)]
                output = burn_captions(source, cues, root / "captioned.mp4", 320, 180, root)
                self.assertAlmostEqual(media_duration(output), media_duration(source), delta=0.07)

    def test_highlights_export_with_real_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run(
                [ffmpeg_bin(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "3",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
                check=True, capture_output=True,
            )
            clips = [{"id": f"c{index}", "start": index * 0.8, "end": index * 0.8 + 0.6,
                      "title": "Test", "takeaway": "Test point"} for index in range(3)]
            segments = [{"start": clip["start"], "end": clip["end"], "text": "Test caption"} for clip in clips]
            with patch("pipeline.export.EXPORT_ROOT", root / "exports"):
                _, _, bundle, compilation = export_clips(
                    run_id="smoke", source_video=str(source), segments=segments, clips=clips,
                    source_url="", work=root / "work", cta_text="Source", intro_seconds=1, end_seconds=2,
                )
            self.assertTrue(bundle.is_file())
            self.assertAlmostEqual(media_duration(compilation), 4.8, delta=0.15)
            captions = Path(compilation).with_name("captions.srt").read_text(encoding="utf-8")
            self.assertEqual(captions.count("Test caption"), 3)


if __name__ == "__main__":
    unittest.main()