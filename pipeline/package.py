from __future__ import annotations

from pathlib import Path

from pipeline.utils import ff_subtitles_path, ff_text, ffmpeg_bin, find_font, run_cmd, write_srt


def slice_srt(segments: list[dict], start: float, end: float, dest: Path) -> Path:
    sliced = []
    for seg in segments:
        if float(seg["end"]) <= start or float(seg["start"]) >= end:
            continue
        sliced.append(
            {
                "start": max(0.0, float(seg["start"]) - start),
                "end": max(0.05, float(seg["end"]) - start),
                "text": seg.get("text", ""),
            }
        )
    write_srt(sliced, dest)
    return dest


def package_clip(
    raw_clip: Path,
    dest: Path,
    *,
    title: str,
    cta: str,
    srt_path: Path | None,
    vertical: bool,
    burn_captions: bool,
    title_card: bool,
    end_cta: bool,
    duration: float,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf_parts: list[str] = []
    if vertical:
        vf_parts.append("scale=1080:1920:force_original_aspect_ratio=increase")
        vf_parts.append("crop=1080:1920")
        vf_parts.append("setsar=1")
    else:
        vf_parts.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

    font = find_font()
    fontsize = 42 if vertical else 28
    if title_card and title:
        box_y = "h*0.12" if vertical else "h*0.08"
        draw = (
            f"drawtext=text='{ff_text(title)}':fontsize={fontsize}:fontcolor=white:"
            f"x=(w-text_w)/2:y={box_y}:box=1:boxcolor=black@0.55:boxborderw=16:"
            f"enable='lte(t,2)'"
        )
        if font:
            draw += f":fontfile='{ff_subtitles_path(Path(font))}'"
        vf_parts.append(draw)

    if end_cta and cta and duration > 0:
        appear = max(0.0, duration - 2.0)
        draw = (
            f"drawtext=text='{ff_text(cta)}':fontsize={max(28, fontsize - 10)}:fontcolor=white:"
            f"x=(w-text_w)/2:y=h*0.82:box=1:boxcolor=black@0.55:boxborderw=12:"
            f"enable='gte(t,{appear:.3f})'"
        )
        if font:
            draw += f":fontfile='{ff_subtitles_path(Path(font))}'"
        vf_parts.append(draw)

    if burn_captions and srt_path and srt_path.exists() and srt_path.stat().st_size > 0:
        vf_parts.append(f"subtitles='{ff_subtitles_path(srt_path)}'")

    vf = ",".join(vf_parts)
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-i",
        str(raw_clip),
        "-vf",
        vf,
        "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(dest),
    ]
    try:
        run_cmd(cmd)
    except RuntimeError:
        simple = [
            ffmpeg_bin(),
            "-y",
            "-i",
            str(raw_clip),
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(dest),
        ]
        run_cmd(simple)
    return dest
