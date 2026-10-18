from __future__ import annotations

import json
import shutil
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from pipeline.assemble import assemble_compilation
from pipeline.cut import cut_clip
from pipeline.contracts import validate_range
from pipeline.project import file_hash
from pipeline.storyboard import validate_compilation_duration, validate_storyboard
from pipeline.utils import EXPORT_ROOT, MAX_KEEP, MIN_KEEP, format_timestamp, media_duration, save_json, slugify


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


def media_notes(media_assets: dict) -> str:
    """post.txt disclosure; bundled library music is a credited licensed track, not AI output."""
    library = media_assets.get("music", {}).get("provider") == "library"
    text = ("\nContains AI-generated conceptual illustrations; illustrations are not documentary evidence.\n" if library else
            "\nContains AI-generated conceptual illustrations and instrumental music; illustrations are not documentary evidence.\n")
    for name, asset in media_assets.items():
        if name == "music" and library:
            text += f"music: {asset['title']} - {asset['credit']} ({asset['license']})\n"
        else:
            text += f"{name}: {asset.get('model') or asset.get('credit', '')}\n"
    return text


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
    selection_context: dict | None = None,
    storyboard: dict | None = None,
    storyboard_approval: dict | None = None,
    blueprint: dict | None = None,
) -> tuple[list[str], str, Path, str]:
    if not (MIN_KEEP <= len(clips) <= MAX_KEEP):
        raise RuntimeError(f"Keep {MIN_KEEP}–{MAX_KEEP} on-topic clips before export.")
    if storyboard is not None:
        validate_storyboard(storyboard, clips)
        if storyboard["aspect_ratio"] != aspect_ratio or storyboard["source_url"] != source_url:
            raise ValueError("Storyboard aspect ratio or source URL differs from the export.")
        intro_title = storyboard["opening"]["title"]
        intro_subtitle = storyboard["opening"]["subtitle"]
        end_takeaway = storyboard["ending"]["takeaway"]
        cta_text = storyboard["ending"]["cta"]

    duration = media_duration(source_video)
    for clip in clips:
        validate_range(float(clip["start"]), float(clip["end"]), duration)
    audience = (selection_context or {}).get("target_audience")
    export_id = (f"{datetime.now():%Y%m%d-%H%M%S}_{slugify(audience, 'export')}_"
                 f"{slugify(aspect_ratio.replace(':', 'x'), 'orig')}_{uuid.uuid4().hex[:4]}")
    parent = EXPORT_ROOT / run_id
    parent.mkdir(parents=True, exist_ok=True)
    dest_root = parent / export_id
    export_work = work / "exports" / export_id
    with tempfile.TemporaryDirectory(prefix=".pending-", dir=parent) as temporary:
        staging = Path(temporary)
        raw_paths: list[Path] = []
        for index, clip in enumerate(clips, 1):
            raw = export_work / "raw_clips" / f"clip_{index}.mp4"
            cut_clip(source_video, clip["start"], clip["end"], raw, copy=False)
            raw_paths.append(raw)

        title = intro_title or clips[0].get("title") or "Highlights"
        subtitle = intro_subtitle.strip()
        takeaway = (end_takeaway or default_takeaway(clips)).strip()
        cta = (cta_text or DEFAULT_CTA).strip() or DEFAULT_CTA
        assemble_compilation(
            raw_paths,
            staging / "compilation.mp4",
            intro_title=title,
            intro_subtitle=subtitle,
            intro_seconds=intro_seconds,
            clips=clips,
            segments=segments,
            end_takeaway=takeaway,
            end_cta=cta,
            end_seconds=end_seconds,
            srt_path=staging / "captions.srt",
            work=export_work / "assemble",
            aspect_ratio=aspect_ratio,
            storyboard=storyboard,
            narration_cache=work / "narration_cache",
            approved_assets=storyboard.get("narration_assets") if storyboard else None,
        )
        if storyboard is not None and "duration_budget" in storyboard:
            validate_compilation_duration(media_duration(staging / "compilation.mp4"), storyboard["duration_budget"])
        post = compilation_post(clips, source_url, title, takeaway, cta)
        if storyboard is not None:
            post += f"\nReflection: {storyboard['ending']['question']}\nSource credit: {storyboard['ending']['credit']}\n"
            if storyboard.get("media_assets"):
                post += media_notes(storyboard["media_assets"])
        (staging / "post.txt").write_text(post, encoding="utf-8")
        inventory = {}
        if storyboard and storyboard.get("media_assets"):
            from pipeline.media import verify_media
            verify_media(storyboard)
            assets = dict(storyboard["media_assets"])
            for key, digest in storyboard.get("narration_assets", {}).items():
                assets[f"narration_{key}"] = {"path": str(work / "narration_cache" / f"{key}.aiff"), "sha256": digest}
            (staging / "assets").mkdir()
            for index, (role, asset) in enumerate(assets.items()):
                source = Path(asset["path"])
                if file_hash(source) != asset["sha256"]:
                    raise ValueError("Approved asset changed during export.")
                relative = f"assets/{index:02d}{source.suffix}"
                shutil.copy2(source, staging / relative)
                if file_hash(staging / relative) != asset["sha256"]:
                    raise ValueError("Exported asset differs from approved bytes.")
                inventory[role] = {"file": relative, "sha256": asset["sha256"]}
        save_json(
            staging / "selection.json",
            {
                "export_id": export_id,
                "clips": [{key: value for key, value in clip.items() if key != "preview_path"} for clip in clips],
                "intro_title": title,
                "intro_subtitle": subtitle,
                "takeaway": takeaway,
                "cta": cta,
                "aspect_ratio": aspect_ratio,
                "selection_context": selection_context or {},
                "storyboard": storyboard,
                "storyboard_approval": storyboard_approval,
                "blueprint": blueprint,
                "asset_inventory": inventory,
                "timeline": json.loads((export_work / "assemble" / "timeline.json").read_text(encoding="utf-8")) if storyboard else None,
            },
        )
        with zipfile.ZipFile(staging / f"compilation-{export_id}.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for name in ("compilation.mp4", "captions.srt", "post.txt", "selection.json"):
                archive.write(staging / name, name)
            for asset in inventory.values():
                archive.write(staging / asset["file"], asset["file"])
        staging.rename(dest_root)

    try:  # exports/latest always points at the newest export; best effort
        pointer = EXPORT_ROOT / ".latest-pointer"
        pointer.unlink(missing_ok=True)
        pointer.symlink_to(dest_root.relative_to(EXPORT_ROOT))
        pointer.replace(EXPORT_ROOT / "latest")
    except OSError:
        pass
    compilation = dest_root / "compilation.mp4"
    zip_path = dest_root / f"compilation-{export_id}.zip"
    return [str(compilation)], post, zip_path, str(compilation)
