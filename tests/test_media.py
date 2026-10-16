import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.assemble import assemble_compilation, burn_captions
from pipeline.export import export_clips
from pipeline.storyboard import draft_storyboard
from pipeline.utils import ffmpeg_bin, format_timestamp, media_duration


@unittest.skipUnless(os.environ.get("RUN_MEDIA_TESTS") == "1", "Set RUN_MEDIA_TESTS=1 for ffmpeg integration tests")
class MediaTests(unittest.TestCase):
    def test_audio_mix_preserves_video_with_and_without_sound_effect(self):
        from pipeline.media import mix_media
        from pipeline.project import file_hash

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            music = root / "music.wav"
            subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=160x90:rate=24", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=44100", "-t", "30", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)], check=True, capture_output=True)
            subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=220:sample_rate=44100", "-t", "2", str(music)],
                           check=True, capture_output=True)

            def video_hash(path):
                return subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(path), "-map", "0:v:0",
                                       "-f", "hash", "-hash", "sha256", "-"], check=True, capture_output=True).stdout

            expected = video_hash(source)
            for with_sfx in (False, True):
                with self.subTest(with_sfx=with_sfx):
                    assets = {"music": {"path": str(music), "sha256": file_hash(music),
                                        "kind": "music", "provider": "library"}}
                    if with_sfx:
                        assets["sfx"] = dict(assets["music"], kind="sfx")
                    draft = {"media_assets": assets, "music_gain": 0.3,
                             "duration_budget": {"min_seconds": 30, "max_seconds": 60}}
                    output = mix_media(source, root / f"mix-{with_sfx}.mp4", draft,
                                       [{"kind": "clip", "start": 0, "duration": 30}], 160, 90, root)
                    self.assertEqual(video_hash(output), expected)
                    self.assertAlmostEqual(media_duration(output), 30, delta=0.15)
                    subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(output), "-map", "0:a:0",
                                    "-f", "null", "-"], check=True, capture_output=True)
                    self.assertFalse((root / "ai_label.png").exists())

    def test_generated_media_all_audiences_and_aspects(self):
        import array
        import io
        import json
        from PIL import Image
        from pipeline.media import prepare_narration
        from pipeline.project import file_hash
        from pipeline.storyboard import AUDIENCE_PROFILES, DURATION_BUDGET

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=size=320x180:rate=30", "-f", "lavfi", "-i",
                            "sine=frequency=220:sample_rate=44100", "-t", "40", "-c:v", "libx264",
                            "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source)],
                           check=True, capture_output=True)
            raw = root / "raw.mp4"
            subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(source), "-t", "15", "-c", "copy", str(raw)],
                           check=True, capture_output=True)
            music = root / "music.wav"
            subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                            "sine=frequency=330:sample_rate=44100", "-t", "30", str(music)], check=True, capture_output=True)
            bitmap = root / "image.png"
            Image.new("RGB", (640, 360), "coral").save(bitmap)

            def asset(path, kind):
                return {"path": str(path), "sha256": file_hash(path), "kind": kind, "model": "test-fixture"}

            def speech(text, path, **kwargs):
                subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                                "sine=frequency=440:sample_rate=44100", "-t", "1", str(path)], check=True, capture_output=True)

            clips = [{"id": "c1", "start": 0, "end": 15, "title": "Which assumptions matter?",
                      "takeaway": "Check assumptions before applying advice.", "excerpt": "Check assumptions before applying advice."},
                     {"id": "c2", "start": 20, "end": 35, "title": "Test one example",
                      "takeaway": "Test one example.", "excerpt": "Then test one example."}]
            segments = [{"start": 0, "end": 2, "text": "Um, check the the assumptions."},
                        {"start": 20, "end": 22, "text": "[MUSIC] Then test one uh example."}]
            for audience in AUDIENCE_PROFILES:
                for aspect in ("16:9", "9:16"):
                    with self.subTest(audience=audience, aspect=aspect):
                        work = root / f"{audience}-{aspect.replace(':', '-')}"
                        work.mkdir()
                        draft = draft_storyboard(clips, audience)
                        draft.update(aspect_ratio=aspect, duration_budget=dict(DURATION_BUDGET), music_gain=0.08,
                                     media_assets={"opening": asset(bitmap, "image"), "ending": asset(bitmap, "image"),
                                                   "music": asset(music, "music")})
                        with patch("pipeline.assemble._synthesize_title", side_effect=speech) as synth:
                            self.assertTrue(30 <= prepare_narration(draft, clips, work) <= 60)
                            calls = synth.call_count
                            output = assemble_compilation([raw, raw], work / "out.mp4", intro_title="", clips=clips,
                                segments=segments, storyboard=draft, aspect_ratio=aspect, work=work / "assemble",
                                srt_path=work / "captions.srt", narration_cache=work / "narration_cache",
                                approved_assets=draft["narration_assets"], preview=True)
                            self.assertEqual(synth.call_count, calls)
                        self.assertTrue(30 <= media_duration(output) <= 60)
                        timeline = json.loads((work / "assemble" / "timeline.json").read_text())
                        captions = (work / "captions.srt").read_text()
                        self.assertIn(format_timestamp(timeline[1]["start"]), captions)
                        self.assertIn("check the assumptions.", captions)
                        self.assertIn("Then test one example.", captions)
                        for unwanted in ("Um", "the the", "MUSIC", " uh "):
                            self.assertNotIn(unwanted, captions)
                        frame = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", "1", "-i", str(output),
                                                "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                                               check=True, capture_output=True)
                        with Image.open(io.BytesIO(frame.stdout)) as image:
                            self.assertGreater(len(image.getcolors(image.width * image.height)), 10)
                        samples = subprocess.run([ffmpeg_bin(), "-v", "error", "-i", str(output), "-t", "6",
                                                  "-vn", "-ac", "1", "-f", "f32le", "-"], check=True, capture_output=True)
                        amplitudes = array.array("f")
                        amplitudes.frombytes(samples.stdout)
                        peak = max(abs(value) for value in amplitudes)
                        self.assertGreater(peak, 0.01)
                        self.assertLess(peak, 1.0)
                        if audience == "Kids" and aspect == "16:9":
                            import zipfile
                            from pipeline.assess import artifact_hashes, _narration_metrics
                            import srt
                            with patch("pipeline.export.EXPORT_ROOT", root / "exports"), patch(
                                "pipeline.assemble._synthesize_title", side_effect=AssertionError("Export regenerated narration")
                            ):
                                exported = export_clips(run_id="frozen", source_video=str(source), segments=segments,
                                    clips=clips, source_url="", work=work, cta_text=draft["ending"]["cta"],
                                    aspect_ratio=aspect, storyboard=draft, storyboard_approval={"kind": "human"})
                            destination = Path(exported[3]).parent
                            manifest = json.loads((destination / "selection.json").read_text())
                            self.assertNotIn("broll", manifest["asset_inventory"])
                            hashes = artifact_hashes(destination)
                            with zipfile.ZipFile(exported[2]) as bundle:
                                for item in manifest["asset_inventory"].values():
                                    self.assertIn(item["file"], bundle.namelist())
                                    self.assertEqual(hashes[item["file"]], item["sha256"])
                            metrics = _narration_metrics(destination / "compilation.mp4", manifest,
                                                        list(srt.parse((destination / "captions.srt").read_text())))
                            self.assertTrue(all(item["status"] == "pass" for item in metrics), metrics)

    def kokoro_audio(self, text, path, **request):
        self.assertEqual(request["voice"], "af_sky")
        self.assertEqual(request["speed"], 0.98)
        self.assertEqual(request["pitch_semitones"], 2)
        seconds = max(1, len(text.split()) * 60 / 140)
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=24000", "-t", str(seconds),
                        str(path)], check=True, capture_output=True)
        path.with_suffix(".txt").write_text(text)
        return path

    @patch("pipeline.kokoro_tts.synthesize")
    def test_narrated_intro_and_default_transition_frames_shift_captions(self, synthesize):
        import array
        import json
        from PIL import Image

        synthesize.side_effect = self.kokoro_audio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = []
            for color in ("red", "blue", "green"):
                source = root / f"{color}.mp4"
                subprocess.run([ffmpeg_bin(), "-y", "-f", "lavfi", "-i", f"color=c={color}:size=320x180:rate=30",
                                "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                               check=True, capture_output=True)
                sources.append(source)
            clips = [{"id": "c1", "start": 0, "end": 1,
                      "title": "Why does this example change how we check assumptions today?",
                      "takeaway": "Check assumptions.", "excerpt": "First example."},
                     {"id": "c2", "start": 1, "end": 2, "title": "How to compare examples",
                                            "takeaway": "Check assumptions.", "excerpt": "Second example."},
                                         {"id": "c3", "start": 2, "end": 3, "title": "Which conclusions need more evidence",
                                            "takeaway": "Check assumptions.", "excerpt": "Third example."}]
            segments = [{"start": 0, "end": 0.8, "text": "First example."},
                                                {"start": 1, "end": 1.8, "text": "Second example."},
                                                {"start": 2, "end": 2.8, "text": "Third example."}]
            for aspect in ("16:9", "9:16"):
                with self.subTest(aspect=aspect):
                    draft = draft_storyboard(clips, "Kids")
                    voice = "af_sky"
                    draft["aspect_ratio"] = aspect
                    work = root / aspect.replace(":", "-")
                    output = assemble_compilation(sources, work / "out.mp4", intro_title="", clips=clips,
                                                  segments=segments, storyboard=draft, work=work,
                                                  srt_path=work / "captions.srt", aspect_ratio=aspect, preview=True)
                    timeline = json.loads((work / "timeline.json").read_text())
                    self.assertEqual([item["kind"] for item in timeline],
                                     ["opening", "clip", "divider", "clip", "divider", "clip", "ending"])
                    intro, first, bridge, second, next_bridge, third, ending = timeline
                    self.assertEqual(intro["narration"]["text"], clips[0]["title"])
                    self.assertEqual(intro["narration"]["provider"], "kokoro")
                    self.assertEqual(intro["narration"]["speed"], 0.98)
                    self.assertEqual(intro["narration"]["pitch_semitones"], 2)
                    self.assertGreater(intro["duration"], draft["opening"]["seconds"])
                    self.assertGreaterEqual(intro["duration"], intro["narration"]["seconds"] + 0.35)
                    self.assertAlmostEqual(first["start"], intro["duration"], delta=0.04)
                    self.assertAlmostEqual(second["start"], bridge["start"] + bridge["duration"], delta=0.04)
                    self.assertEqual(bridge["text"], clips[1]["title"])
                    self.assertEqual(bridge["narration"]["text"], bridge["text"])
                    self.assertEqual(bridge["narration"]["voice"], voice)
                    self.assertEqual(bridge["narration"]["rate"], 171)
                    self.assertGreater(bridge["duration"], draft["transitions"][0]["seconds"])
                    self.assertGreaterEqual(bridge["duration"], bridge["narration"]["seconds"] + 0.35)
                    self.assertAlmostEqual(media_duration(output), ending["start"] + ending["duration"], delta=0.15)
                    captions = (work / "captions.srt").read_text()
                    self.assertIn("00:00:00,150", captions)
                    self.assertIn(format_timestamp(first["start"]), captions)
                    self.assertIn(format_timestamp(second["start"]), captions)
                    self.assertIn(format_timestamp(bridge["start"] + 0.15), captions)
                    self.assertIn(bridge["text"], captions)
                    self.assertEqual(next_bridge["narration"]["text"], clips[2]["title"])
                    self.assertEqual(next_bridge["narration"]["rate"], 171)
                    self.assertGreaterEqual(next_bridge["duration"], next_bridge["narration"]["seconds"] + 0.35)
                    self.assertAlmostEqual(third["start"], next_bridge["start"] + next_bridge["duration"], delta=0.04)
                    self.assertIn(format_timestamp(third["start"]), captions)
                    self.assertIn(format_timestamp(next_bridge["start"] + 0.15), captions)
                    self.assertIn(next_bridge["text"], captions)
                    bridge_audio = list(work.glob("divider_*_narration.aiff"))
                    self.assertEqual(len(bridge_audio), 2)
                    self.assertEqual({audio.with_suffix(".txt").read_text().strip() for audio in bridge_audio},
                                     {bridge["text"], next_bridge["text"]})
                    self.assertFalse(list(work.glob("label_*.png")))
                    with Image.open(work / "next_frame_2.png") as frame:
                        red, green, blue = frame.convert("RGB").getpixel((frame.width // 2, frame.height // 2))
                        self.assertGreater(blue, red + 100)
                    with Image.open(work / "card_2_bg.png") as frame:
                        self.assertGreater(len(frame.getcolors(frame.width * frame.height)), 10)
                    self.assertTrue(list(work.glob("card_2_0*.png")), "bridge text must be separate reveal layers")
                    with Image.open(work / "next_frame_4.png") as frame:
                        red, green, blue = frame.convert("RGB").getpixel((frame.width // 2, frame.height // 2))
                        self.assertGreater(green, max(red, blue) + 100)
                    for card in (intro, bridge, next_bridge, ending):
                        self.assertEqual(card["narration"]["voice"], voice)
                        samples = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", str(card["start"] + 0.15),
                                                  "-i", str(output), "-t", str(card["narration"]["seconds"]),
                                                  "-vn", "-ac", "1", "-f", "f32le", "-"], check=True, capture_output=True)
                        amplitudes = array.array("f")
                        amplitudes.frombytes(samples.stdout)
                        self.assertGreater(max(abs(sample) for sample in amplitudes), 0.01)

    @patch("pipeline.kokoro_tts.synthesize")
    def test_slow_ending_narration_extends_card_and_is_audible(self, synthesize):
        import array
        import json

        synthesize.side_effect = self.kokoro_audio
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            subprocess.run([ffmpeg_bin(), "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30",
                            "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                           check=True, capture_output=True)
            clips = [{"id": "c1", "start": 0, "end": 1, "title": "Which assumptions matter?",
                      "takeaway": "Check the assumptions behind each example before applying this advice to a new situation.",
                      "excerpt": "Check assumptions before applying advice."}]
            draft = draft_storyboard(clips, "Kids")
            draft["ending"].update(question="Which assumptions matter?", cta="Explain one example in your own words.")
            output = assemble_compilation([source], root / "out.mp4", intro_title="", clips=clips,
                                          storyboard=draft, work=root, srt_path=root / "captions.srt")
            ending = json.loads((root / "timeline.json").read_text())[-1]
            self.assertEqual(ending["narration"]["voice"], "af_sky")
            self.assertEqual(ending["narration"]["rate"], 171)
            self.assertEqual(ending["narration"]["provider"], "kokoro")
            self.assertEqual(ending["narration"]["pitch_semitones"], 2)
            self.assertIn(draft["ending"]["cta"], ending["narration"]["text"])
            self.assertGreater(ending["duration"], draft["ending"]["seconds"])
            self.assertGreaterEqual(ending["duration"], ending["narration"]["seconds"] + 0.35)
            self.assertAlmostEqual(media_duration(output), ending["start"] + ending["duration"], delta=0.12)
            self.assertIn(format_timestamp(ending["start"] + 0.15), (root / "captions.srt").read_text())
            self.assertIn(draft["ending"]["cta"], (root / "captions.srt").read_text())
            samples = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", str(ending["start"] + 0.15),
                                      "-i", str(output), "-t", str(ending["narration"]["seconds"]),
                                      "-vn", "-ac", "1", "-f", "f32le", "-"], check=True, capture_output=True)
            amplitudes = array.array("f")
            amplitudes.frombytes(samples.stdout)
            self.assertGreater(max(abs(sample) for sample in amplitudes), 0.01)

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
            storyboard["duration_budget"] = {"min_seconds": 45, "max_seconds": 120}
            with patch("pipeline.export.EXPORT_ROOT", root / "exports"):
                _, _, bundle, compilation = export_clips(run_id="single", source_video=str(source),
                    segments=segments, clips=clips, source_url="", work=root / "work", cta_text="",
                    storyboard=storyboard)
            metadata = json.loads(Path(compilation).with_name("selection.json").read_text())
            self.assertEqual(metadata["storyboard"]["transitions"], [])
            self.assertEqual([item["kind"] for item in metadata["timeline"]], ["opening", "clip", "ending"])
            self.assertAlmostEqual(media_duration(compilation), 47.5, delta=0.15)
            self.assertIn("00:00:02,500", Path(compilation).with_name("captions.srt").read_text())
            self.assertEqual(len(list((root / "work").glob("exports/*/assemble/label_*.png"))), 0)
            with zipfile.ZipFile(bundle) as archive:
                self.assertIsNone(archive.testzip())
            subprocess.run([ffmpeg_bin(), "-v", "error", "-i", compilation, "-f", "null", "-"],
                           check=True, capture_output=True)

            from pipeline.assess import assess_export_automatically

            report_dir = assess_export_automatically(Path(compilation).parent)
            report = json.loads((report_dir / "assessment.json").read_text())
            checks = {item["id"]: item["status"] for item in report["metrics"]}
            self.assertEqual(checks["decode"], "pass")
            self.assertEqual(checks["timeline"], "pass")
            self.assertEqual(checks["narration_audio"], "pass")
            silent = root / "silent.mp4"
            subprocess.run([ffmpeg_bin(), "-y", "-i", compilation, "-c:v", "copy", "-af", "volume=0",
                            "-c:a", "aac", str(silent)], check=True, capture_output=True)
            silent.replace(compilation)
            changed_report_dir = assess_export_automatically(Path(compilation).parent)
            self.assertNotEqual(report_dir, changed_report_dir)
            changed = json.loads((changed_report_dir / "assessment.json").read_text())
            checks = {item["id"]: item["status"] for item in changed["metrics"]}
            self.assertEqual(checks["narration_audio"], "fail")
            self.assertEqual(checks["loudness"], "fail")
            self.assertEqual(changed["technical_status"], "failed")
            self.assertEqual(changed["publication_status"], "not_assessed")

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
            draft["opening"].pop("mode")
            draft["opening"].pop("narration")
            for transition in draft["transitions"]:
                transition.pop("narration")
            draft["transitions"][0].update(mode="Divider", label="Another example")
            draft["transitions"][1].update(mode="Overlay", label="A limitation")
            for aspect in ("16:9", "9:16"):
                draft["aspect_ratio"] = aspect
                work = root / aspect.replace(":", "-")
                output = assemble_compilation([source] * 3, work / "out.mp4", intro_title="",
                                              clips=clips, segments=segments, storyboard=draft, work=work,
                                              srt_path=work / "captions.srt", aspect_ratio=aspect, preview=True)
                timeline = json.loads((work / "timeline.json").read_text())
                self.assertAlmostEqual(media_duration(output), 9.5, delta=0.2)
                self.assertAlmostEqual(timeline[2]["start"], 2.5, delta=0.08)
                captions = (work / "captions.srt").read_text()
                self.assertIn("00:00:00,000", captions)
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
            self.assertEqual(len(selection["timeline"]), 5)
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