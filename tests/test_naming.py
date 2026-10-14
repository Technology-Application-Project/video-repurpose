import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.export import export_clips
from pipeline.utils import new_run_id, slugify


class NamingTests(unittest.TestCase):
    def test_slugify_keeps_only_safe_characters(self):
        self.assertEqual(slugify("Liyn5xWvx44 first 10 min!"), "liyn5xwvx44-first-10-min")
        self.assertEqual(slugify("  --Kids / ages 8-12-- "), "kids-ages-8-12")
        self.assertEqual(slugify("", "video"), "video")
        self.assertEqual(slugify(None, "export"), "export")
        self.assertEqual(slugify("\u8bb2\u5ea7", "video"), "video")
        self.assertLessEqual(len(slugify("a" * 100)), 40)

    def test_run_id_names_the_source_and_sorts_by_time(self):
        run = new_run_id("/tmp/gradio/abc/My Lecture (final).mp4")
        self.assertRegex(run, r"^\d{8}-\d{4}_my-lecture-final_[0-9a-f]{4}$")
        self.assertRegex(new_run_id(), r"^[0-9a-f]{10}$")
        self.assertNotEqual(new_run_id("a.mp4"), new_run_id("a.mp4"))

    def test_export_folder_names_time_audience_and_aspect_and_latest_points_at_it(self):
        def assemble(raw_clips, dest, **kwargs):
            dest.write_bytes(b"video")
            kwargs["srt_path"].write_text("captions", encoding="utf-8")

        clips = [{"id": "c1", "start": 0.0, "end": 10.0, "title": "T", "takeaway": "P", "excerpt": "E"}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = dict(run_id="20261012-0915_lecture_ab12", source_video="source.mp4", segments=[], clips=clips,
                           source_url="", work=root / "work", cta_text="CTA", aspect_ratio="9:16",
                           selection_context={"target_audience": "General public"})
            with patch("pipeline.export.EXPORT_ROOT", root / "exports"), patch("pipeline.export.media_duration", return_value=40), patch(
                    "pipeline.export.cut_clip"), patch("pipeline.export.assemble_compilation", side_effect=assemble):
                first = export_clips(**options)
                second = export_clips(**{**options, "selection_context": {"target_audience": "Kids"}, "aspect_ratio": "16:9"})
            folder = Path(first[3]).parent
            self.assertEqual(folder.parent.name, "20261012-0915_lecture_ab12")
            self.assertRegex(folder.name, r"^\d{8}-\d{6}_general-public_9x16_[0-9a-f]{4}$")
            self.assertRegex(Path(second[3]).parent.name, r"^\d{8}-\d{6}_kids_16x9_[0-9a-f]{4}$")
            self.assertEqual(first[2].name, f"compilation-{folder.name}.zip")
            latest = root / "exports" / "latest"
            self.assertTrue(latest.is_symlink())
            self.assertEqual(latest.resolve(), Path(second[3]).parent.resolve())
            self.assertTrue((latest / "compilation.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
