from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable


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
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def new_run_id() -> str:
    return uuid.uuid4().hex[:10]


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


def run_cmd(cmd: list[str], *, on_fraction: Callable[[float], None] | None = None,
            duration: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command. For ffmpeg with `on_fraction` and the output `duration`, report the
    share of the output encoded so far, read from ffmpeg's own -progress stream."""
    if on_fraction is None or not duration or duration <= 0:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "command failed").strip()
            raise RuntimeError(err[-2000:])
        return result
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr, text=True)
        for line in process.stdout:
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit():  # both are microseconds
                on_fraction(min(1.0, int(value) / 1_000_000 / duration))
        process.wait()
        stderr.seek(0)
        err = stderr.read().decode("utf-8", errors="replace")
    if process.returncode != 0:
        raise RuntimeError((err or "command failed").strip()[-2000:])
    on_fraction(1.0)
    return subprocess.CompletedProcess(cmd, process.returncode, "", err)


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


PLAYABLE_VIDEO_CODECS = {"h264"}
PLAYABLE_PIX_FMTS = {"yuv420p", "yuvj420p"}
PLAYABLE_AUDIO_CODECS = {"aac", "mp3"}


def probe_streams(path: str | Path) -> tuple[dict | None, dict | None]:
    """Return (first video stream, first audio stream) as ffprobe dicts."""
    result = run_cmd(
        [
            ffprobe_bin(),
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,pix_fmt",
            "-of",
            "json",
            str(path),
        ]
    )
    streams = json.loads(result.stdout or "{}").get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return video, audio


def ensure_browser_playable(path: str | Path,
                            on_fraction: Callable[[float], None] | None = None) -> tuple[str, str | None]:
    """Re-encode/remux a video to H.264 + AAC mp4 if browsers can't play it.

    Returns (path to use, short description of what was done or None if untouched).
    """
    path = Path(path)
    video, audio = probe_streams(path)
    if video is None:
        raise RuntimeError("The uploaded file has no video stream.")
    video_ok = video.get("codec_name") in PLAYABLE_VIDEO_CODECS and video.get("pix_fmt") in PLAYABLE_PIX_FMTS
    audio_ok = audio is None or audio.get("codec_name") in PLAYABLE_AUDIO_CODECS
    container_ok = path.suffix.lower() in {".mp4", ".m4v"}
    if video_ok and audio_ok and container_ok:
        return str(path), None

    output = path.with_name(f"{path.stem}_playable.mp4")
    cmd = [ffmpeg_bin(), "-v", "error", "-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a:0?"]
    if video_ok:
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy"] if audio_ok else ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", str(output)]
    run_cmd(cmd, on_fraction=on_fraction, duration=media_duration(path) if on_fraction else None)

    if not video_ok:
        action = f"re-encoded {video.get('codec_name')}/{video.get('pix_fmt')} video to H.264"
    elif not audio_ok:
        action = f"converted {audio.get('codec_name')} audio to AAC"
    else:
        action = f"remuxed {path.suffix or 'file'} to mp4"
    return str(output), action


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
