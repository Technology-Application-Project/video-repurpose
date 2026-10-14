import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pipeline import music
from pipeline.export import media_notes
from pipeline.media import CARD_MUSIC_BOOST, mix_media, prepare_media
from pipeline.utils import ffmpeg_bin, media_duration


def write_library(root, audiences=music.AUDIENCES, per_audience=3, **changes):
    root = Path(root)
    tracks = []
    for audience in audiences:
        for index in range(per_audience):
            relative = f"{audience.lower().replace(' ', '-')}/track-{index}.mp3"
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(f"{audience}{index}".encode())
            tracks.append({"file": relative, "audience": audience, "title": f"{audience} {index}", "license": "CC0-1.0",
                           "source": "test", "credit": "Original", "redistributable": True, **changes})
    (root / "manifest.json").write_text(json.dumps({"tracks": tracks}))
    return root


class LibraryTests(unittest.TestCase):
    def test_bundled_library_has_three_valid_tracks_per_audience(self):
        tracks = music.load_library()
        for audience in music.AUDIENCES:
            self.assertEqual(len([track for track in tracks if track["audience"] == audience]), 3, audience)
        self.assertTrue(all(Path(track["path"]).is_file() and track["redistributable"] for track in tracks))
        self.assertEqual(len({track["file"] for track in tracks}), len(tracks))

    def test_choice_is_stable_audience_specific_and_varies_with_the_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = write_library(directory)
            first = music.choose_track("Kids", "selection-a", root)
            self.assertEqual(first, music.choose_track("Kids", "selection-a", root))
            self.assertEqual(first["audience"], "Kids")
            picked = {music.choose_track("Kids", f"selection-{index}", root)["title"] for index in range(20)}
            self.assertEqual(len(picked), 3)

    def test_asset_is_hashed_credited_and_marked_as_library_music(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = music.library_asset("Researcher", "key", write_library(directory))
        self.assertEqual((asset["kind"], asset["provider"], asset["license"]), ("music", "library", "CC0-1.0"))
        self.assertRegex(asset["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(asset["credit"] and asset["title"])

    def test_unusable_libraries_fail_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "manifest"):
                music.load_library(directory)
            with self.assertRaisesRegex(ValueError, "no track for Kids"):
                music.load_library(write_library(directory, audiences=("Researcher", "General public")))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "redistribution"):
                music.load_library(write_library(directory, redistributable=False))
        with tempfile.TemporaryDirectory() as directory:
            root = write_library(directory)
            (root / "kids" / "track-0.mp3").unlink()
            with self.assertRaisesRegex(ValueError, "missing"):
                music.load_library(root)
        with tempfile.TemporaryDirectory() as directory:
            root = write_library(directory)
            manifest = json.loads((root / "manifest.json").read_text())
            manifest["tracks"][0]["file"] = "../outside.mp3"
            (root / "manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "missing"):
                music.load_library(root)

    def test_post_text_credits_library_music_and_does_not_call_it_ai(self):
        assets = {"opening": {"model": "scene-model"}, "music": {"provider": "library", "title": "Open Road",
                                                                    "credit": "Original synthesized composition", "license": "CC0-1.0"}}
        notes = media_notes(assets)
        self.assertIn("music: Open Road - Original synthesized composition (CC0-1.0)", notes)
        self.assertNotIn("and instrumental music", notes)
        generated = media_notes({"music": {"model": "model-x"}})
        self.assertIn("AI-generated conceptual illustrations and instrumental music", generated)


class MixTests(unittest.TestCase):
    def mix_command(self, provider):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("music.mp3", "broll.mp4", "source.mp4"):
                (root / name).write_bytes(b"x")
            draft = {"media_assets": {"music": {"path": str(root / "music.mp3"), "kind": "music", **({"provider": provider} if provider else {})},
                                      "broll": {"path": str(root / "broll.mp4"), "kind": "video"}},
                     "music_gain": 0.3, "duration_budget": {"min_seconds": 30, "max_seconds": 60}}
            timeline = [{"kind": "clip", "start": 0, "duration": 10}]
            with patch("pipeline.media.verify_media"), patch("pipeline.media.media_duration", return_value=10.0), patch(
                    "pipeline.media.validate_compilation_duration"), patch("pipeline.media.run_cmd") as run:
                mix_media(root / "source.mp4", root / "out.mp4", draft, timeline, 640, 360, root)
            return " ".join(run.call_args_list[-1].args[0])

    def test_library_tracks_keep_their_own_fade_in_and_generated_ones_get_one(self):
        self.assertNotIn("afade=t=in", self.mix_command("library"))
        self.assertIn("afade=t=in", self.mix_command(None))
        self.assertIn(f"{CARD_MUSIC_BOOST - 1}", self.mix_command("library"))


class PrepareMediaTests(unittest.TestCase):
    def draft(self, audience):
        return {"audience": audience, "clip_ids": ["c1", "c2"], "opening": {"evidence_ids": ["c1"]}, "ending": {"evidence_ids": ["c2"]},
                "evidence": [{"clip_id": "c1", "quote": "A"}, {"clip_id": "c2", "quote": "B"}]}

    def batch(self, directory, with_music_item):
        def asset(kind, prompt, aspect="16:9"):
            path = Path(directory) / f"{kind}-{len(prompt)}.bin"
            path.write_bytes(prompt.encode())
            from pipeline.project import file_hash
            return {"path": str(path), "sha256": file_hash(path), "kind": kind}

        items = {"video": {"provider": "local-illustration"}, "image": {}}
        if with_music_item:
            items["music"] = {}
        return Mock(quote={"items": items}, asset=Mock(side_effect=asset), local_broll=Mock(side_effect=lambda still, aspect: asset("video", "b")))

    def test_library_music_replaces_the_paid_music_request(self):
        for audience in music.AUDIENCES:
            with self.subTest(audience=audience), tempfile.TemporaryDirectory() as directory, patch(
                    "pipeline.media.media_duration", return_value=5.0):
                batch, draft = self.batch(directory, False), self.draft(audience)
                prepare_media(draft, batch, "16:9")
                asset = draft["media_assets"]["music"]
                self.assertEqual(asset["provider"], "library")
                self.assertIn(f"/{audience.lower().replace(' ', '-')}/", asset["path"])
                self.assertNotIn("music", [call.args[0] for call in batch.asset.call_args_list])
                again = self.draft(audience)
                prepare_media(again, self.batch(directory, False), "16:9")
                self.assertEqual(again["media_assets"]["music"]["path"], asset["path"])

    def test_generated_music_is_still_requested_when_the_quote_has_a_music_item(self):
        with tempfile.TemporaryDirectory() as directory, patch("pipeline.media.media_duration", return_value=5.0):
            batch, draft = self.batch(directory, True), self.draft("Kids")
            prepare_media(draft, batch, "16:9")
            self.assertIn("music", [call.args[0] for call in batch.asset.call_args_list])
            self.assertNotIn("provider", draft["media_assets"]["music"])


@unittest.skipUnless(os.environ.get("RUN_MEDIA_TESTS") == "1", "Set RUN_MEDIA_TESTS=1 for ffmpeg integration tests")
class BundledAudioTests(unittest.TestCase):
    def test_every_track_is_a_clean_instrumental_bed_of_consistent_level(self):
        loudness = {}
        for track in music.load_library():
            path = track["path"]
            self.assertAlmostEqual(media_duration(Path(path)), track["seconds"], delta=0.2, msg=track["file"])
            report = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", path, "-af", "ebur128=peak=true", "-f", "null", "-"],
                                    capture_output=True, text=True).stderr.split("Summary:")[-1]
            integrated = float(re.search(r"I:\s+(-?[\d.]+) LUFS", report).group(1))
            peak = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", report).group(1))
            self.assertLess(peak, -3, f"{track['file']} leaves no headroom")
            self.assertGreater(integrated, -24, f"{track['file']} is too quiet")
            loudness[track["file"]] = integrated
        self.assertLess(max(loudness.values()) - min(loudness.values()), 3.0, "track levels should be matched so one gain fits all")


if __name__ == "__main__":
    unittest.main()
