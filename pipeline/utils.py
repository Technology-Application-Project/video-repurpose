from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = ROOT / "work"
EXPORT_ROOT = ROOT / "exports"
MAX_REVIEW = 10
MIN_KEEP = 1
MAX_KEEP = 5

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def slugify(text: str | None, fallback: str = "item", limit: int = 40) -> str:
    """Lowercase letters, digits and single hyphens only, so the name is safe in paths and FFmpeg filters."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:limit].strip("-")
    return slug or fallback


def new_run_id(source_name: str | None = None) -> str:
    """`YYYYMMDD-HHMM_<source>_<hex4>` when the source file name is known, else a bare 10-character id."""
    if not source_name:
        return uuid.uuid4().hex[:10]
    return f"{datetime.now():%Y%m%d-%H%M}_{slugify(Path(source_name).stem, 'video')}_{uuid.uuid4().hex[:4]}"


def run_dir(run_id: str) -> Path:
    path = WORK_ROOT / run_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "previews").mkdir(exist_ok=True)
    (path / "raw_clips").mkdir(exist_ok=True)
    (path / "exports").mkdir(exist_ok=True)
    return path


def as_video_path(video: Any) -> str | None:
    if video is None:
        return None
    if isinstance(video, str):
        return video
    if isinstance(video, dict):
        return video.get("video") or video.get("name") or video.get("path")
    path = getattr(video, "name", None)
    return str(path) if path else str(video)


def format_timestamp(seconds: float) -> str:
    milliseconds = int(round(max(0.0, float(seconds)) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:
            return str(obj)
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(to_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def write_srt(segments: list[dict], path: Path) -> None:
    lines: list[str] = []
    index = 1
    for seg in segments:
        if "start" not in seg or "end" not in seg:
            continue
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        lines.append(str(index))
        lines.append(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}")
        lines.append(text)
        lines.append("")
        index += 1
    path.write_text("\n".join(lines), encoding="utf-8")


def short_url(url: str, limit: int = 32, host_only: bool = False) -> str:
    """On-screen form of a source link: no scheme, www, query or fragment; long paths keep their last segment."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse((url or "").strip())
        host = parsed.hostname
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not host:
        return ""
    host = host.removeprefix("www.")
    path = "" if host_only else parsed.path.rstrip("/")
    text = host + path
    if len(text) > limit and path.count("/") > 1:
        text = f"{host}/.../{path.rsplit('/', 1)[1]}"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def ffmpeg_bin() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("ffmpeg is not installed or not on PATH.")
    return path


def ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise RuntimeError("ffprobe is not installed or not on PATH.")
    return path


def run_cmd(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(err[-2000:])
    return result


def media_duration(path: str | Path) -> float:
    result = run_cmd(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ]
    )
    return float(result.stdout.strip())


def media_size(path: str | Path) -> tuple[int, int]:
    result = run_cmd(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(path),
        ]
    )
    width_s, height_s = result.stdout.strip().split("x")
    width, height = int(width_s), int(height_s)
    return width - (width % 2), height - (height % 2)


def even_dim(value: int) -> int:
    value = max(2, int(value))
    return value if value % 2 == 0 else value - 1


def canvas_size(width: int, height: int, aspect: str | None) -> tuple[int, int]:
    width = even_dim(width if width >= 2 else 1920)
    height = even_dim(height if height >= 2 else 1080)
    key = (aspect or "Original").strip().split()[0]
    ratios = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}
    if key not in ratios:
        return width, height
    rw, rh = ratios[key]
    if rw == rh:
        side = even_dim(max(width, height, 1080))
        return side, side
    long_side = max(width, height, 1920)
    if rw > rh:
        out_w = even_dim(long_side)
        out_h = even_dim(round(out_w * rh / rw))
    else:
        out_h = even_dim(long_side)
        out_w = even_dim(round(out_h * rw / rh))
    return max(out_w, 2), max(out_h, 2)


def has_audio(path: str | Path) -> bool:
    result = subprocess.run(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def find_font() -> str | None:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path
    return None


def ff_text(text: str) -> str:
    cleaned = re.sub(r"[\r\n]+", " ", text).strip()
    cleaned = cleaned.replace("\\", "\\\\").replace("'", r"\'").replace(":", r"\:").replace("%", r"\%")
    return cleaned[:180]


def ff_subtitles_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def parse_objectives(text: str) -> list[str]:
    return [line.strip(" -•\t") for line in (text or "").splitlines() if line.strip()]


def excerpt_for_range(segments: list[dict], start: float, end: float) -> str:
    parts = []
    for seg in segments:
        if float(seg["end"]) <= start or float(seg["start"]) >= end:
            continue
        parts.append(str(seg.get("text", "")).strip())
    return " ".join(p for p in parts if p)


def snap_to_segments(start: float, end: float, segments: list[dict]) -> tuple[float, float]:
    if not segments:
        return start, end
    covering = [s for s in segments if float(s["end"]) > start and float(s["start"]) < end]
    if not covering:
        nearest = min(segments, key=lambda s: min(abs(float(s["start"]) - start), abs(float(s["end"]) - end)))
        covering = [nearest]
    return float(covering[0]["start"]), float(covering[-1]["end"])
