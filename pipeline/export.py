from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from pipeline.assemble import assemble_compilation
from pipeline.cut import cut_clip
from pipeline.utils import EXPORT_ROOT, format_timestamp, save_json


DEFAULT_CTA = "Full discussion in the original lecture. Link in the description."


def default_takeaway(clips: list[dict]) -> str:
    lines = []
    for clip in clips:
        text = (clip.get("takeaway") or clip.get("title") or "").strip()
        if text:
            lines.append(f"• {text}")
    return "\n".join(lines)


def compilation_post(clips: list[dict], source_url: str, intro_title: str, takeaway: str, cta: str) -> str:
    ranges = []
    for i, clip in enumerate(clips, 1):
        start = format_timestamp(clip["start"]).replace(",", ".")
        end = format_timestamp(clip["end"]).replace(",", ".")
        ranges.append(f"{i}. {clip.get('title') or clip.get('id')} ({start}–{end})")
    return (
        f"{intro_title}\n\n"
        f"{takeaway}\n\n"
        f"{cta}\n\n"
        f"Full source: {source_url or '(add source URL)'}\n\n"
        "Included segments:\n"
        + "\n".join(ranges)
        + "\n"
    )


def export_clips(
    *,
    run_id: str,
    source_video: str,
    segments: list[dict],
    clips: list[dict],
    source_url: str,
    work: Path,
    cta_text: str,
    intro_title: str = "",
    intro_subtitle: str = "",
    intro_seconds: float = 1.5,
    end_takeaway: str = "",
    end_seconds: float = 3.0,
    aspect_ratio: str = "Original",
) -> tuple[list[str], str, Path, str]:
    if not (3 <= len(clips) <= 5):
        raise RuntimeError("Keep 3–5 on-topic clips before export.")

    dest_root = EXPORT_ROOT / run_id
    if dest_root.exists():
        shutil.rmtree(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)

    raw_paths: list[Path] = []
    for i, clip in enumerate(clips, 1):
        clip_id = clip.get("id") or f"c{i}"
        raw = work / "raw_clips" / f"{clip_id}.mp4"
        cut_clip(source_video, clip["start"], clip["end"], raw, copy=False)
        raw_paths.append(raw)

    title = intro_title or (clips[0].get("title") if clips else "Highlights")
    subtitle = intro_subtitle.strip()
    takeaway = (end_takeaway or default_takeaway(clips)).strip()
    cta = (cta_text or DEFAULT_CTA).strip() or DEFAULT_CTA
    compilation = dest_root / "compilation.mp4"
    srt_path = dest_root / "captions.srt"
    assemble_compilation(
        raw_paths,
        compilation,
        intro_title=title,
        intro_subtitle=subtitle,
        intro_seconds=intro_seconds,
        clips=clips,
        segments=segments,
        end_takeaway=takeaway,
        end_cta=cta,
        end_seconds=end_seconds,
        srt_path=srt_path,
        work=work / "assemble",
        aspect_ratio=aspect_ratio,
    )

    post = compilation_post(clips, source_url, title, takeaway, cta)
    (dest_root / "post.txt").write_text(post, encoding="utf-8")
    save_json(
        dest_root / "selection.json",
        {
            "clips": clips,
            "intro_title": title,
            "intro_subtitle": subtitle,
            "takeaway": takeaway,
            "cta": cta,
            "aspect_ratio": aspect_ratio,
        },
    )

    zip_path = dest_root / "compilation.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("compilation.mp4", "captions.srt", "post.txt", "selection.json"):
            path = dest_root / name
            if path.exists():
                zf.write(path, name)

    return [str(compilation)], post, zip_path, str(compilation)
