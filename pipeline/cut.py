from __future__ import annotations

from pathlib import Path

from pipeline.utils import ffmpeg_bin, run_cmd


def cut_clip(source: str, start: float, end: float, dest: Path, copy: bool = False) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    ss = max(0.0, float(start))
    to = max(ss + 0.2, float(end))
    cmd = [ffmpeg_bin(), "-y", "-i", source, "-ss", f"{ss:.3f}", "-t", f"{to - ss:.3f}"]
    if copy:
        copy_cmd = [
            ffmpeg_bin(),
            "-y",
            "-ss",
            f"{ss:.3f}",
            "-to",
            f"{to:.3f}",
            "-i",
            source,
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            str(dest),
        ]
        try:
            run_cmd(copy_cmd)
            return dest
        except RuntimeError:
            if dest.exists():
                dest.unlink()
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-pix_fmt", "yuv420p", str(dest)]
    run_cmd(cmd)
    return dest


def make_preview(source: str, start: float, end: float, dest: Path) -> Path:
    return cut_clip(source, start, end, dest, copy=False)
