from __future__ import annotations

import re
import shutil
from pathlib import Path

from pipeline.progress import NO_PROGRESS, StepProgress
from pipeline.storyboard import storyboard_timeline, validate_storyboard
from pipeline.utils import canvas_size, ffmpeg_bin, find_font, has_audio, media_duration, media_size, run_cmd, write_srt


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def _normalize_clip(source: Path, dest: Path, width: int, height: int, fps: int = 30) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,setsar=1"
    )
    cmd = [ffmpeg_bin(), "-y", "-i", str(source)]
    if not has_audio(source):
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest"]
    cmd += [
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(dest),
    ]
    run_cmd(cmd)
    return dest


def _wrap_text(text: str, max_chars: int, max_lines: int = 4) -> str:
    words = (text or "").split()
    if not words:
        return ""
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if len(trial) <= max_chars:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:max_lines])


def _split_takeaway_items(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    chunks = re.split(r"\n+|(?<=\S)\s+(?=•\s)", raw)
    items = []
    for chunk in chunks:
        item = re.sub(r"^[\s•\-\t]+", "", chunk).strip()
        if item:
            items.append(item)
    return items or [raw]


def _wrap_to_pixel_width(text: str, font, max_width: int) -> list[str]:
    from PIL import Image, ImageDraw

    dummy = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(dummy)
    words = (text or "").split()
    if not words:
        return []
    lines, current = [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), trial, font=font)
        if not current or (bbox[2] - bbox[0]) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _format_bullets_for_font(text: str, font, max_width: int, max_lines: int = 10) -> str:
    from PIL import Image, ImageDraw

    dummy = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(dummy)
    bullet = "• "
    indent = "  "
    bullet_w = draw.textbbox((0, 0), bullet, font=font)[2]
    wrap_w = max(40, max_width - bullet_w)
    lines: list[str] = []
    for item in _split_takeaway_items(text):
        wrapped = _wrap_to_pixel_width(item, font, wrap_w)
        if not wrapped:
            continue
        lines.append(bullet + wrapped[0])
        lines.extend(indent + line for line in wrapped[1:])
        lines.append("")
        if len(lines) >= max_lines:
            break
    return "\n".join(lines).strip()


def _load_fonts(title_size: int, sub_size: int):
    from PIL import ImageFont

    font_path = find_font()
    try:
        title_font = ImageFont.truetype(font_path, title_size) if font_path else ImageFont.load_default()
        sub_font = ImageFont.truetype(font_path, sub_size) if font_path else ImageFont.load_default()
    except Exception:
        title_font = ImageFont.load_default()
        sub_font = title_font
    return title_font, sub_font


def _type_sizes(width: int, height: int) -> tuple[int, int, int]:
    short = min(width, height)
    title_size = max(22, min(44, round(short * 0.07)))
    body_size = max(15, min(24, round(short * 0.042)))
    foot_size = max(12, min(18, round(short * 0.03)))
    return int(title_size), int(body_size), int(foot_size)


def _render_card_image(
    dest: Path,
    heading: str,
    body: str,
    footer: str,
    width: int,
    height: int,
    *,
    layout: str = "center",
) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), (17, 17, 17))
    draw = ImageDraw.Draw(img)
    title_size, body_size, foot_size = _type_sizes(width, height)
    title_font, body_font = _load_fonts(title_size, body_size)
    _, foot_font = _load_fonts(title_size, foot_size)
    margin_x = max(20, int(width * 0.10))
    max_text_w = max(40, width - 2 * margin_x)
    line_gap = max(6, int(title_size * 0.22))
    dest.parent.mkdir(parents=True, exist_ok=True)

    if layout != "end":
        heading_text = "\n".join(_wrap_to_pixel_width(heading or "Highlights", title_font, max_text_w))
        body_text = "\n".join(_wrap_to_pixel_width(body, body_font, max_text_w))
        heading_w = heading_h = body_w = body_h = 0
        if heading_text:
            bbox = draw.multiline_textbbox((0, 0), heading_text, font=title_font, align="center", spacing=line_gap)
            heading_w, heading_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if body_text:
            bbox = draw.multiline_textbbox((0, 0), body_text, font=body_font, align="center", spacing=line_gap)
            body_w, body_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        stack_gap = max(18, int(height * 0.035)) if body_text else 0
        block_h = heading_h + stack_gap + body_h
        y = max(int(height * 0.12), (height - block_h) // 2)
        if heading_text:
            draw.multiline_text(
                ((width - heading_w) // 2, y),
                heading_text,
                fill=(255, 255, 255),
                font=title_font,
                align="center",
                spacing=line_gap,
            )
            y += heading_h + stack_gap
        if body_text:
            draw.multiline_text(
                ((width - body_w) // 2, y),
                body_text,
                fill=(210, 210, 210),
                font=body_font,
                align="center",
                spacing=line_gap,
            )
        img.save(dest)
        return dest

    heading_text = "\n".join(_wrap_to_pixel_width(heading or "Key takeaway", title_font, max_text_w))
    body_text = _format_bullets_for_font(body, body_font, max_text_w, max_lines=12)
    footer_text = "\n".join(_wrap_to_pixel_width(footer, foot_font, int(width * 0.78)))
    top_y = int(height * 0.10)

    if heading_text:
        bbox = draw.multiline_textbbox((0, 0), heading_text, font=title_font, align="center", spacing=line_gap)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.multiline_text(
            ((width - w) // 2, top_y),
            heading_text,
            fill=(255, 255, 255),
            font=title_font,
            align="center",
            spacing=line_gap,
        )
        body_y = top_y + h + max(16, int(height * 0.03))
    else:
        body_y = top_y

    if footer_text:
        foot_bbox = draw.multiline_textbbox((0, 0), footer_text, font=foot_font, align="center", spacing=line_gap)
        foot_w, foot_h = foot_bbox[2] - foot_bbox[0], foot_bbox[3] - foot_bbox[1]
        foot_y = height - foot_h - int(height * 0.07)
        draw.multiline_text(
            ((width - foot_w) // 2, foot_y),
            footer_text,
            fill=(180, 200, 255),
            font=foot_font,
            align="center",
            spacing=line_gap,
        )
    else:
        foot_y = height - int(height * 0.07)

    if body_text:
        body_bbox = draw.multiline_textbbox((0, 0), body_text, font=body_font, align="left", spacing=6)
        body_h = body_bbox[3] - body_bbox[1]
        max_body_bottom = foot_y - 16
        if body_y + body_h > max_body_bottom:
            body_y = max(top_y, max_body_bottom - body_h)
        draw.multiline_text(
            (margin_x, body_y),
            body_text,
            fill=(230, 230, 230),
            font=body_font,
            align="left",
            spacing=6,
        )

    img.save(dest)
    return dest


def _synthesize_title(title: str, dest: Path) -> Path:
    executable = shutil.which("say")
    if executable is None:
        from pipeline.narrate import engine, speak_title

        if engine() is None:
            raise RuntimeError("Intro narration requires a local speech synthesizer (macOS say, Windows SAPI or espeak-ng).")
        return speak_title(title, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    text_path = dest.with_suffix(".txt")
    text_path.write_text(title, encoding="utf-8")
    run_cmd([executable, "-v", "Samantha", "-r", "175", "-f", str(text_path), "-o", str(dest)])
    return dest


def _card_video(png: Path, dest: Path, width: int, height: int, seconds: float, fps: int = 30,
                *, audio: Path | None = None) -> Path:
    cmd = [
        ffmpeg_bin(),
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-t",
        f"{seconds:.2f}",
        "-i",
        str(png),
    ]
    cmd += ["-i", str(audio)] if audio is not None else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    if audio is not None:
        cmd += ["-af", "adelay=150:all=1,apad"]
    cmd += [
        "-vf",
        f"scale={width}:{height},format=yuv420p,setsar=1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-t",
        f"{seconds:.2f}",
        "-shortest",
        str(dest),
    ]
    run_cmd(cmd)
    return dest


def make_intro_card(
    dest: Path,
    *,
    title: str,
    subtitle: str,
    width: int,
    height: int,
    seconds: float,
    fps: int = 30,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    width, height = _even(max(width, 2)), _even(max(height, 2))
    seconds = min(2.0, max(1.0, float(seconds)))
    png = dest.with_suffix(".png")
    _render_card_image(png, title or "Highlights", subtitle or "", "", width, height)
    return _card_video(png, dest, width, height, seconds, fps)


def make_end_card(
    dest: Path,
    *,
    takeaway: str,
    cta: str,
    width: int,
    height: int,
    seconds: float,
    fps: int = 30,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    width, height = _even(max(width, 2)), _even(max(height, 2))
    seconds = min(6.0, max(2.0, float(seconds)))
    png = dest.with_suffix(".png")
    _render_card_image(png, "Key takeaway", takeaway, cta, width, height, layout="end")
    return _card_video(png, dest, width, height, seconds, fps)


def concat_mp4s(parts: list[Path], dest: Path, on_fraction=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    listing = dest.with_suffix(".txt")
    lines = []
    for part in parts:
        path = str(part.resolve()).replace("'", r"'\''")
        lines.append(f"file '{path}'")
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_cmd(
        [
            ffmpeg_bin(),
            "-y",
            "-fflags",
            "+genpts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-avoid_negative_ts",
            "make_zero",
            str(dest),
        ],
        on_fraction=on_fraction,
        duration=sum(media_duration(part) for part in parts) if on_fraction else None,
    )
    return dest


def _clip_caption_units(segments: list[dict], start: float, end: float) -> list[dict]:
    words = []
    for seg in segments:
        for word in seg.get("words") or []:
            if "start" not in word or "end" not in word:
                continue
            ws, we = float(word["start"]), float(word["end"])
            if we <= start or ws >= end:
                continue
            token = str(word.get("word", "")).strip()
            if not token:
                continue
            words.append({"start": max(start, ws), "end": min(end, we), "word": token})
    if words:
        return _group_caption_words(words)
    units = []
    for seg in segments:
        if float(seg["end"]) <= start or float(seg["start"]) >= end:
            continue
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        units.append(
            {
                "start": max(start, float(seg["start"])),
                "end": min(end, float(seg["end"])),
                "text": text,
            }
        )
    return units


def _group_caption_words(words: list[dict], max_chars: int = 38, max_seconds: float = 3.2) -> list[dict]:
    groups: list[dict] = []
    bucket: list[dict] = []

    def flush() -> None:
        if not bucket:
            return
        text = " ".join(item["word"] for item in bucket)
        groups.append({"start": bucket[0]["start"], "end": bucket[-1]["end"], "text": text})
        bucket.clear()

    for word in words:
        trial = " ".join(item["word"] for item in bucket + [word])
        dur = (word["end"] - bucket[0]["start"]) if bucket else 0.0
        punct = bool(bucket) and bucket[-1]["word"][-1:] in ".?!,;:"
        if bucket and (len(trial) > max_chars or dur > max_seconds or (punct and len(trial) > 16)):
            flush()
        bucket.append(word)
    flush()
    return groups


def compilation_cues(clips: list[dict], segments: list[dict], intro_dur: float, clip_durs: list[float],
                     *, clip_offsets: list[float] | None = None) -> list[dict]:
    cues = []
    cursor = float(intro_dur)
    if clip_offsets is not None and len(clip_offsets) != len(clips):
        raise ValueError("Each clip needs a timeline offset.")
    for index, (clip, dur) in enumerate(zip(clips, clip_durs)):
        if clip_offsets is not None:
            cursor = clip_offsets[index]
        cs, ce = float(clip["start"]), float(clip["end"])
        src_dur = max(0.05, ce - cs)
        scale = float(dur) / src_dur
        for unit in _clip_caption_units(segments, cs, ce):
            local_start = max(0.0, (float(unit["start"]) - cs) * scale)
            local_end = min(float(dur), max(local_start + 0.12, (float(unit["end"]) - cs) * scale))
            text = str(unit.get("text", "")).strip()
            if not text:
                continue
            cues.append(
                {
                    "start": round(cursor + local_start, 3),
                    "end": round(cursor + local_end, 3),
                    "text": text,
                }
            )
        cursor += float(dur)
    return cues


def _caption_png(dest: Path, text: str, canvas_w: int, canvas_h: int) -> Path:
    from PIL import Image, ImageDraw

    max_text_w = max(80, int(canvas_w * 0.86))
    font_size = max(18, min(40, round(min(canvas_w, canvas_h) * 0.038)))
    _, font = _load_fonts(font_size, font_size)
    wrapped = "\n".join(_wrap_to_pixel_width(text, font, max_text_w)) or text
    probe = Image.new("RGB", (8, 8))
    probe_draw = ImageDraw.Draw(probe)
    bbox = probe_draw.multiline_textbbox((0, 0), wrapped, font=font, align="center", spacing=6)
    tw, th = int(round(bbox[2] - bbox[0])), int(round(bbox[3] - bbox[1]))
    pad_x, pad_y = 22, 12
    box_w = max(2, int(min(canvas_w, tw + pad_x * 2)))
    box_h = max(2, int(th + pad_y * 2))
    img = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, box_w - 1, box_h - 1), radius=min(16, box_h // 3), fill=(0, 0, 0, 175))
    draw.multiline_text(((box_w - tw) // 2, pad_y), wrapped, fill=(255, 255, 255, 255), font=font, align="center", spacing=6)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest)
    return dest


def burn_captions(video: Path, cues: list[dict], dest: Path, width: int, height: int, work: Path,
                  *, graphics: list[dict] | None = None, on_fraction=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cues = [*cues, *(graphics or [])]
    if not cues:
        if dest != video:
            dest.write_bytes(video.read_bytes())
        return dest

    duration = media_duration(video)
    current = video
    cap_dir = work / "caption_overlays"
    cap_dir.mkdir(parents=True, exist_ok=True)
    batch_size = 8
    audio = has_audio(video)
    margin = max(24, int(height * 0.045))
    passes = -(-len(cues) // batch_size)
    for batch_i in range(0, len(cues), batch_size):
        done_passes = batch_i // batch_size
        pass_progress = (lambda f, d=done_passes: on_fraction((d + f) / passes)) if on_fraction else None
        batch = cues[batch_i : batch_i + batch_size]
        out = cap_dir / f"burn_{batch_i:03d}.mp4"
        cmd = [ffmpeg_bin(), "-y", "-i", str(current)]
        for j, cue in enumerate(batch):
            png = cue.get("png")
            if png is None:
                png = cap_dir / f"cap_{batch_i:03d}_{j}.png"
                _caption_png(png, cue["text"], width, height)
            cmd += ["-loop", "1", "-i", str(png)]
        filters = []
        last = "0:v"
        for j, cue in enumerate(batch):
            png_label = f"p{j}"
            out_label = "vout" if j == len(batch) - 1 else f"v{j}"
            start, end = cue["start"], max(cue["start"] + 0.12, cue["end"])
            filters.append(f"[{j + 1}:v]format=rgba,setpts=PTS-STARTPTS[{png_label}]")
            position = cue.get("y", f"H-h-{margin}")
            filters.append(
                f"[{last}][{png_label}]overlay=x=(W-w)/2:y={position}:eof_action=repeat:enable='gte(t,{start:.3f})*lt(t,{end:.3f})'[{out_label}]"
            )
            last = out_label
        cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
        if audio:
            cmd += ["-map", "0:a", "-c:a", "aac", "-ar", "44100", "-ac", "2"]
        cmd += [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-t",
            f"{duration:.6f}",
            "-shortest",
            str(out),
        ]
        run_cmd(cmd, on_fraction=pass_progress, duration=duration)
        current = out
    if dest != current:
        if dest.exists():
            dest.unlink()
        dest.write_bytes(current.read_bytes())
    return dest


def _storyboard_panel(dest: Path, sections: list[tuple[str, str]], width: int, height: int,
                      *, opening: bool = False) -> Path:
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (width, height), (14, 35, 33, 255) if opening else (22, 27, 30, 235))
    draw = ImageDraw.Draw(image)
    padding = max(8, round(width * 0.1 if opening else min(width, height) * 0.09))
    available_width = width - padding * 2
    available_height = height - padding * 2
    roles = {"title": (1.35, (255, 255, 255)), "body": (1.0, (240, 240, 240)),
             "question": (1.0, (151, 225, 205)), "credit": (0.75, (203, 209, 210))}
    if opening:
        roles.update(title=(1.5, (248, 250, 245)), body=(0.7, (165, 219, 202)))
    sections = [(text, role) for text, role in sections if text.strip()]
    initial_size = max(14, min(96 if opening else 48, round(width * (0.085 if opening else 0.045))))
    for size in range(initial_size, 7, -1):
        blocks = []
        gap = max(6, round(size * 0.6))
        for text, role in sections:
            factor, color = roles[role]
            font, _ = _load_fonts(max(8, round(size * factor)), size)
            wrapped = "\n".join(_wrap_to_pixel_width(text, font, available_width))
            bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4,
                                             align="center" if opening else "left")
            blocks.append((wrapped, font, color, bounds))
        block_height = sum(bounds[3] - bounds[1] for _, _, _, bounds in blocks) + gap * max(0, len(blocks) - 1)
        if block_height <= available_height and all(bounds[2] - bounds[0] <= available_width for _, _, _, bounds in blocks):
            break
    else:
        raise ValueError("Storyboard text does not fit this output size. Shorten the text.")
    if opening:
        rule_y = max(4, padding // 2)
        draw.rectangle((round(width * 0.42), rule_y, round(width * 0.58), rule_y + max(3, height // 120)),
                       fill=(237, 194, 85, 255))
    else:
        draw.rectangle((0, 0, max(3, width // 150), height), fill=(105, 211, 190, 255))
    position = (height - block_height) // 2
    for text, font, color, bounds in blocks:
        left = (width - (bounds[2] - bounds[0])) / 2 if opening else padding
        draw.multiline_text((left - bounds[0], position - bounds[1]), text, font=font, fill=color, spacing=4,
                            align="center" if opening else "left")
        position += bounds[3] - bounds[1] + gap
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


def _assemble_storyboard(raw_clips: list[Path], dest: Path, clips: list[dict], segments: list[dict],
                         storyboard: dict, width: int, height: int, work: Path, srt_path: Path | None,
                         tracker: StepProgress = NO_PROGRESS) -> Path:
    validate_storyboard(storyboard, clips)
    if len(raw_clips) != len(clips):
        raise ValueError("Storyboard and media clip counts differ.")
    tracker.step("Normalizing clips")
    normalized = []
    for index, raw in enumerate(raw_clips):
        tracker.count(index, len(raw_clips), "clips")
        normalized.append(_normalize_clip(raw, work / f"norm_{index:02d}.mp4", width, height))
    tracker.step("Rendering title cards")
    card_total = sum(1 for item in storyboard_timeline(clips, [1.0] * len(clips), storyboard) if item["kind"] != "clip")
    cards_done = 0
    durations = [media_duration(path) for path in normalized]
    timeline = storyboard_timeline(clips, durations, storyboard)
    parts, graphics, clip_offsets = [], [], []
    clip_index = 0
    for index, item in enumerate(timeline):
        if item["kind"] == "clip":
            parts.append(normalized[clip_index])
            clip_offsets.append(item["start"])
            if clip_index == 0:
                opening = storyboard["opening"]
                sections = [] if opening.get("mode") == "Card" else [(opening["title"], "title"), (opening["subtitle"], "body")]
                seconds = opening["seconds"]
            else:
                transition = storyboard["transitions"][clip_index - 1]
                sections = [(transition["label"], "title")] if transition["mode"] == "Overlay" else []
                seconds = transition["seconds"]
            if sections:
                png = _storyboard_panel(work / f"label_{index}.png", sections, round(width * 0.84), round(height * 0.25))
                graphics.append({"png": png, "start": item["start"],
                                 "end": item["start"] + min(seconds, item["duration"]),
                                 "y": round(height * (0.05 if storyboard["overlay_position"] == "Upper" else 0.38))})
            clip_index += 1
            continue
        if item["kind"] == "opening":
            opening = storyboard["opening"]
            sections = [(opening["title"], "title"), (opening["subtitle"], "body")]
        elif item["kind"] == "divider":
            sections = [(item["text"], "title")]
        else:
            ending = storyboard["ending"]
            sections = [("Key takeaway", "credit"), (ending["takeaway"], "title"),
                        (ending["question"], "question"), (ending["credit"], "credit"), (ending["cta"], "credit")]
        tracker.count(cards_done, card_total, "cards")
        cards_done += 1
        png = _storyboard_panel(work / f"card_{index}.png", sections, width, height,
                    opening=item["kind"] == "opening")
        narration = None
        card_seconds = item["duration"]
        if item["kind"] == "opening" and storyboard["opening"].get("narration"):
            narration = _synthesize_title(storyboard["opening"]["title"], work / "intro_narration.aiff")
            card_seconds = max(card_seconds, media_duration(narration) + 0.4)
        card = _card_video(png, work / f"card_{index}.mp4", width, height, card_seconds, audio=narration)
        actual_duration = media_duration(card)
        difference = actual_duration - item["duration"]
        item["duration"] = actual_duration
        for later in timeline[index + 1:]:
            later["start"] += difference
        parts.append(card)
    tracker.step("Joining clips and cards")
    bare = concat_mp4s(parts, work / "concat_bare.mp4", on_fraction=tracker.fraction)
    cues = compilation_cues(clips, segments, 0, durations, clip_offsets=clip_offsets)
    if srt_path is not None:
        write_srt(cues, srt_path)
    from pipeline.utils import save_json

    save_json(work / "timeline.json", timeline)
    tracker.step("Burning captions and overlays")
    return burn_captions(bare, cues, dest, width, height, work, graphics=graphics, on_fraction=tracker.fraction)


ASSEMBLE_STEPS = 4


def assemble_compilation(
    raw_clips: list[Path],
    dest: Path,
    *,
    intro_title: str,
    intro_subtitle: str = "",
    intro_seconds: float = 1.5,
    clips: list[dict] | None = None,
    segments: list[dict] | None = None,
    end_takeaway: str = "",
    end_cta: str = "",
    end_seconds: float = 3.0,
    srt_path: Path | None = None,
    work: Path | None = None,
    aspect_ratio: str = "Original",
    storyboard: dict | None = None,
    preview: bool = False,
    tracker: StepProgress = NO_PROGRESS,
) -> Path:
    """Uses ASSEMBLE_STEPS steps of `tracker`."""
    if not raw_clips:
        raise RuntimeError("No raw clips to assemble.")
    src_w, src_h = media_size(raw_clips[0])
    width, height = canvas_size(src_w, src_h, aspect_ratio)
    if preview:
        scale = min(1.0, 640 / max(width, height))
        width, height = _even(round(width * scale)), _even(round(height * scale))
    work = work or (dest.parent / "_assemble")
    work.mkdir(parents=True, exist_ok=True)
    if storyboard is not None:
        return _assemble_storyboard(raw_clips, dest, clips or [], segments or [], storyboard,
                                    width, height, work, srt_path, tracker)

    tracker.step("Rendering title cards")
    tracker.count(0, 2, "cards")
    intro = make_intro_card(
        work / "intro.mp4",
        title=intro_title,
        subtitle=intro_subtitle,
        width=width,
        height=height,
        seconds=intro_seconds,
    )
    tracker.count(1, 2, "cards")
    end = make_end_card(
        work / "end_card.mp4",
        takeaway=end_takeaway or "Review the selected points, then watch the full source.",
        cta=end_cta or "Full discussion in the original lecture. Link in the description.",
        width=width,
        height=height,
        seconds=end_seconds,
    )
    parts = [intro]
    clip_durs = []
    tracker.step("Normalizing clips")
    for i, raw in enumerate(raw_clips, 1):
        tracker.count(i - 1, len(raw_clips), "clips")
        normalized = _normalize_clip(raw, work / f"norm_{i:02d}.mp4", width, height)
        parts.append(normalized)
        clip_durs.append(media_duration(normalized))
    parts.append(end)

    tracker.step("Joining clips and cards")
    bare = work / "concat_bare.mp4"
    concat_mp4s(parts, bare, on_fraction=tracker.fraction)

    cues = compilation_cues(clips or [], segments or [], media_duration(intro), clip_durs)
    if srt_path is not None:
        write_srt(cues, srt_path)

    tracker.step("Burning captions")
    return burn_captions(bare, cues, dest, width, height, work, on_fraction=tracker.fraction)
