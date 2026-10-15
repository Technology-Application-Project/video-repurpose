from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from pipeline.storyboard import AUDIENCE_PROFILES, storyboard_timeline, validate_storyboard
from pipeline.project import atomic_json, data_hash, file_hash
from pipeline.utils import canvas_size, ffmpeg_bin, find_font, has_audio, media_duration, media_size, run_cmd, write_srt


def _even(value: int) -> int:
    return value if value % 2 == 0 else value - 1


_FILLERS = {"um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "mm", "mhm"}
_STUTTER_WORDS = {"the", "a", "an", "and", "but", "so", "to", "of", "in", "on", "for", "with", "at", "as",
                  "i", "we", "you", "it", "it's", "is", "was", "this", "he", "she", "they", "be"}
_NON_SPEECH = re.compile(r"\[[^\]]*\]|\([^)]*\)|[\u266a\u266b]+")


def _plain_word(token: str) -> str:
    return re.sub(r"[^\w']", "", token).lower()


def clean_caption_words(words: list[dict]) -> list[dict]:
    """Drop filler sounds, non-speech tags and immediate function-word stutters; keeps all meaningful words."""
    kept: list[dict] = []
    for word in words:
        token = _NON_SPEECH.sub("", str(word["word"])).strip()
        plain = _plain_word(token)
        if not plain or plain in _FILLERS:
            continue
        if kept and plain == _plain_word(kept[-1]["word"]) and plain in _STUTTER_WORDS:
            kept[-1] = {**kept[-1], "word": token, "end": max(kept[-1]["end"], word["end"])}
            continue
        kept.append({**word, "word": token})
    return kept


def clean_caption_text(text: str) -> str:
    words = clean_caption_words([{"word": token, "start": 0.0, "end": 0.0} for token in str(text).split()])
    return " ".join(word["word"] for word in words)


def _hold_cues(cues: list[dict], limit_end: float, characters_per_second: float = 17.0) -> None:
    """Extend a caption into the pause after it, never past the next caption or the clip end."""
    for index, cue in enumerate(cues):
        ceiling = cues[index + 1]["start"] - 0.04 if index + 1 < len(cues) else limit_end
        wanted = cue["start"] + max(0.9, len(cue["text"]) / characters_per_second)
        cue["end"] = round(max(cue["end"], min(wanted, ceiling)), 3)


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


NEUTRAL_WPM = 175


def _is_neural_voice(voice: str) -> bool:
    """Edge TTS short names look like en-US-AvaNeural; macOS `say` voices never do."""
    return bool(re.fullmatch(r"[a-z]{2}-[A-Z]{2}-\w+Neural", voice or ""))


def _edge_rate(rate: int) -> str:
    """Profiles carry nominal words per minute; Edge takes an offset from the voice default."""
    return f"{round(rate / NEUTRAL_WPM * 100) - 100:+d}%"


def _synthesize_title(title: str, dest: Path, *, voice: str = "Samantha", rate: int = 175) -> Path:
    if _is_neural_voice(voice):
        import edge_tts

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.with_suffix(".txt").write_text(title, encoding="utf-8")
        with tempfile.NamedTemporaryFile(dir=dest.parent, suffix=".mp3") as audio:
            try:
                edge_tts.Communicate(
                    title, voice=voice, rate=_edge_rate(rate),
                    connect_timeout=5, receive_timeout=30,
                ).save_sync(audio.name)
            except Exception as error:
                raise RuntimeError(
                    f"Neural narration ({voice}) failed. Check internet access and retry the preview. "
                    "Edge TTS may be temporarily unavailable. No fallback voice was used."
                ) from error
            if Path(audio.name).stat().st_size == 0:
                raise RuntimeError(f"Neural narration ({voice}) returned empty audio. "
                                   "Retry the preview; no fallback voice was used.")
            run_cmd([ffmpeg_bin(), "-y", "-i", audio.name, "-c:a", "pcm_s16be", str(dest)])
        return dest
    executable = shutil.which("say")
    if executable is None:
        raise RuntimeError("Card narration requires macOS speech synthesis (say) and the selected system voice.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    text_path = dest.with_suffix(".txt")
    text_path.write_text(title, encoding="utf-8")
    run_cmd([executable, "-v", voice, "-r", str(rate), "-f", str(text_path), "-o", str(dest)])
    return dest


def narration_audio(text: str, dest: Path, profile: dict, cache: Path | None = None,
                    approved_assets: dict | None = None) -> dict:
    import json

    key = data_hash({"version": 1, "text": text, "voice": profile["voice"], "rate": profile["rate"]})
    if cache is None:
        _synthesize_title(text, dest, voice=profile["voice"], rate=profile["rate"])
        return {"cache_key": key, "sha256": file_hash(dest)}
    cache.mkdir(parents=True, exist_ok=True)
    audio, manifest = cache / f"{key}.aiff", cache / f"{key}.json"
    valid = False
    if audio.is_file() and manifest.is_file():
        try:
            valid = json.loads(manifest.read_text())["sha256"] == file_hash(audio)
        except (OSError, ValueError, KeyError):
            pass
    if approved_assets is not None:
        if not valid or approved_assets.get(key) != file_hash(audio):
            raise ValueError("Approved narration is missing or changed. Preview and approve again.")
    elif not valid:
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            pending = Path(temporary) / "speech.aiff"
            _synthesize_title(text, pending, voice=profile["voice"], rate=profile["rate"])
            if media_duration(pending) <= 0:
                raise ValueError("Narration has no measurable duration.")
            pending.replace(audio)
        atomic_json(manifest, {"sha256": file_hash(audio), "text": text, "profile": profile})
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(audio, dest)
    dest.with_suffix(".txt").write_text(text, encoding="utf-8")
    return {"cache_key": key, "sha256": file_hash(dest)}


def _card_video(png: Path, dest: Path, width: int, height: int, seconds: float, fps: int = 30,
                *, audio: Path | None = None, motion: bool = False) -> Path:
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
    scale_filter = f"scale={width}:{height},format=yuv420p,setsar=1"
    if motion:
        big_w, big_h = _even(width * 3 // 2), _even(height * 3 // 2)
        scale_filter = (f"scale={big_w}:{big_h},zoompan=z='min(1+0.0009*on,1.07)':x='(iw-iw/zoom)/2':"
                        f"y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={fps},format=yuv420p,setsar=1")
    cmd += [
        "-vf",
        scale_filter,
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


def concat_mp4s(parts: list[Path], dest: Path) -> Path:
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
        ]
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
    words = clean_caption_words(words)
    if words:
        return _group_caption_words(words)
    units = []
    for seg in segments:
        if float(seg["end"]) <= start or float(seg["start"]) >= end:
            continue
        text = clean_caption_text(seg.get("text", ""))
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
        clip_cues = []
        for unit in _clip_caption_units(segments, cs, ce):
            local_start = max(0.0, (float(unit["start"]) - cs) * scale)
            local_end = min(float(dur), max(local_start + 0.12, (float(unit["end"]) - cs) * scale))
            text = str(unit.get("text", "")).strip()
            if not text:
                continue
            clip_cues.append(
                {
                    "start": round(cursor + local_start, 3),
                    "end": round(cursor + local_end, 3),
                    "text": text,
                }
            )
        _hold_cues(clip_cues, round(cursor + float(dur), 3))
        cues.extend(clip_cues)
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
                  *, graphics: list[dict] | None = None) -> Path:
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
    for batch_i in range(0, len(cues), batch_size):
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
        run_cmd(cmd)
        current = out
    if dest != current:
        if dest.exists():
            dest.unlink()
        dest.write_bytes(current.read_bytes())
    return dest


def _display_font(audience: str, size: int):
    from PIL import ImageFont
    from pipeline.media import PRESENTATION

    for path in PRESENTATION.get(audience, PRESENTATION["General public"])["fonts"]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return _load_fonts(size, size)[0]


def _star(cx: float, cy: float, radius: float) -> list[tuple[float, float]]:
    import math

    return [(cx + (radius if index % 2 == 0 else radius * 0.45) * math.cos(-math.pi / 2 + index * math.pi / 5),
             cy + (radius if index % 2 == 0 else radius * 0.45) * math.sin(-math.pi / 2 + index * math.pi / 5))
            for index in range(10)]


def _decorate(image, audience: str, accent: tuple, width: int, height: int):
    from PIL import Image, ImageDraw
    from pipeline.media import PRESENTATION

    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    kind = PRESENTATION.get(audience, PRESENTATION["General public"])["decor"]
    thick, unit = max(3, height // 140), min(width, height)
    if kind == "rules":
        for y in (0.07, 0.93):
            draw.rectangle((width * 0.12, height * y, width * 0.88, height * y + thick), fill=(*accent, 230))
    elif kind == "band":
        draw.polygon([(0, height * 0.80), (width * 0.5, height), (0, height)], fill=(*accent, 235))
        draw.polygon([(width, 0), (width * 0.62, 0), (width, height * 0.20)], fill=(*accent, 235))
    else:
        for x, y, size, shape in ((0.08, 0.14, 0.06, "star"), (0.92, 0.18, 0.04, "dot"), (0.12, 0.86, 0.04, "dot"),
                                  (0.90, 0.84, 0.065, "star"), (0.50, 0.05, 0.025, "dot"), (0.50, 0.96, 0.025, "dot")):
            color = (*accent, 235) if shape == "star" else (255, 255, 255, 220)
            if shape == "star":
                draw.polygon(_star(width * x, height * y, unit * size), fill=color)
            else:
                radius = unit * size
                draw.ellipse((width * x - radius, height * y - radius, width * x + radius, height * y + radius), fill=color)
    return Image.alpha_composite(image, layer)


def _storyboard_panel(dest: Path, sections: list[tuple[str, str]], width: int, height: int,
                      *, opening: bool = False, background: Path | None = None, audience: str = "General public") -> Path:
    from PIL import Image, ImageDraw, ImageOps
    from pipeline.media import CREATIVE

    treatment = CREATIVE.get(audience, CREATIVE["General public"])
    accent = treatment["accent"]
    image = Image.new("RGBA", (width, height), (*treatment["background"], 255))
    if background is not None:
        with Image.open(background) as frame:
            image = ImageOps.fit(frame.convert("RGBA"), (width, height))
        scrim = Image.new("RGBA", image.size, (0, 0, 0, 0))
        scrim_draw = ImageDraw.Draw(scrim)
        for y in range(height):
            edge = abs(y - height / 2) / (height / 2)
            scrim_draw.line([(0, y), (width, y)], fill=(10, 18, 20, round(215 - 95 * edge ** 1.5)))
        image = Image.alpha_composite(image, scrim)
    if opening or background is not None:
        image = _decorate(image, audience, accent, width, height)
    draw = ImageDraw.Draw(image)
    padding = max(8, round(width * 0.1 if opening else min(width, height) * 0.09))
    available_width = width - padding * 2
    available_height = height - padding * 2
    roles = {"title": (1.35, (255, 255, 255)), "body": (1.0, (240, 240, 240)),
             "question": (1.0, (151, 225, 205)), "credit": (0.75, (203, 209, 210))}
    if opening:
        roles.update(title=(1.6, (255, 255, 255)), body=(0.75, (225, 240, 235)))
    roles["question"] = (1.15 if audience == "Kids" else 1.0, accent)
    if audience == "Kids":
        roles["title"] = (1.7 if opening else 1.6, (255, 255, 255))
    kicker = next((text for text, role in sections if role == "kicker" and text.strip()), "")
    sections = [(text, role) for text, role in sections if text.strip() and role != "kicker"]
    initial_size = max(14, min(110 if opening else 48, round(width * (0.095 if opening else 0.045))))
    for size in range(initial_size, 7, -1):
        blocks = []
        gap = max(6, round(size * 0.6))
        chip = None
        if kicker:
            chip_font = _display_font(audience, max(9, round(size * 0.5)))
            label = kicker.upper()
            box = draw.textbbox((0, 0), label, font=chip_font)
            pad_x, pad_y = round(size * 0.5), round(size * 0.22)
            chip = (label, chip_font, box, pad_x, pad_y, box[2] - box[0] + 2 * pad_x, box[3] - box[1] + 2 * pad_y)
        for text, role in sections:
            factor, color = roles[role]
            font = (_display_font(audience, max(8, round(size * factor))) if role == "title"
                    else _load_fonts(max(8, round(size * factor)), size)[0])
            wrapped = "\n".join(_wrap_to_pixel_width(text, font, available_width))
            bounds = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4,
                                             align="center" if opening else "left")
            blocks.append((wrapped, font, color, bounds))
        chip_height = (chip[6] + gap) if chip else 0
        block_height = (chip_height + sum(bounds[3] - bounds[1] for _, _, _, bounds in blocks)
                        + gap * max(0, len(blocks) - 1))
        if (block_height <= available_height and all(bounds[2] - bounds[0] <= available_width for _, _, _, bounds in blocks)
                and (chip is None or chip[5] <= available_width)):
            break
    else:
        raise ValueError("Storyboard text does not fit this output size. Shorten the text.")
    if not opening:
        draw.rectangle((0, 0, max(3, width // 150), height), fill=(*accent, 255))
    position = (height - block_height) // 2
    if chip:
        label, chip_font, box, pad_x, pad_y, chip_w, chip_h = chip
        left = (width - chip_w) / 2 if opening else padding
        draw.rounded_rectangle((left, position, left + chip_w, position + chip_h), radius=chip_h // 2, fill=(*accent, 255))
        draw.text((left + pad_x - box[0], position + pad_y - box[1]), label, font=chip_font, fill=(18, 22, 26))
        position += chip_h + gap
    for text, font, color, bounds in blocks:
        left = (width - (bounds[2] - bounds[0])) / 2 if opening else padding
        draw.multiline_text((left - bounds[0], position - bounds[1]), text, font=font, fill=color, spacing=4,
                            align="center" if opening else "left")
        position += bounds[3] - bounds[1] + gap
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest)
    return dest


def _bridge_frame(source: Path, destination: Path) -> tuple[Path | None, float | None]:
    from PIL import Image

    duration = media_duration(source)
    offsets = sorted({0.0, min(0.25, duration / 4), min(0.5, duration / 2)})
    for offset in offsets:
        destination.unlink(missing_ok=True)
        run_cmd([ffmpeg_bin(), "-y", "-ss", str(offset), "-i", str(source), "-frames:v", "1", str(destination)])
        if destination.exists():
            with Image.open(destination) as image:
                if image.convert("L").getextrema()[1] > 8:
                    return destination, offset
    destination.unlink(missing_ok=True)
    return None, None


def _assemble_storyboard(raw_clips: list[Path], dest: Path, clips: list[dict], segments: list[dict],
                         storyboard: dict, width: int, height: int, work: Path, srt_path: Path | None,
                         narration_cache: Path | None = None, approved_assets: dict | None = None) -> Path:
    validate_storyboard(storyboard, clips)
    from pipeline import cards
    from pipeline.media import mix_media, verify_media
    verify_media(storyboard)
    if len(raw_clips) != len(clips):
        raise ValueError("Storyboard and media clip counts differ.")
    normalized = [_normalize_clip(raw, work / f"norm_{index:02d}.mp4", width, height)
                  for index, raw in enumerate(raw_clips)]
    durations = [media_duration(path) for path in normalized]
    timeline = storyboard_timeline(clips, durations, storyboard)
    parts, graphics, clip_offsets, narration_cues = [], [], [], []
    from pipeline.storyboard import resolve_narration_profile
    profile = storyboard.get("narration_profile") or resolve_narration_profile(storyboard.get("audience"))
    from pipeline.media import PRESENTATION
    kickers = PRESENTATION.get(storyboard["audience"], PRESENTATION["General public"])["kickers"]
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
                png = _storyboard_panel(work / f"label_{index}.png", sections, round(width * 0.84), round(height * 0.25),
                                        audience=storyboard["audience"])
                graphics.append({"png": png, "start": item["start"],
                                 "end": item["start"] + min(seconds, item["duration"]),
                                 "y": round(height * (0.05 if storyboard["overlay_position"] == "Upper" else 0.38))})
            clip_index += 1
            continue
        still = None
        if item["kind"] == "divider":
            still, offset = _bridge_frame(normalized[clip_index], work / f"next_frame_{index}.png")
            item["background"] = {"kind": "source_frame" if still else "illustration" if storyboard.get("media_assets") else "plain",
                                  "clip_id": clips[clip_index]["id"], "clip_offset": offset}
        narration = None
        narration_text = ""
        card_seconds = item["duration"]
        if item["kind"] == "opening" and storyboard["opening"].get("narration"):
            narration_text = storyboard["opening"]["title"]
        elif item["kind"] == "divider" and storyboard["transitions"][clip_index - 1].get("narration"):
            narration_text = item["text"]
        elif item["kind"] == "ending" and storyboard["ending"].get("narration"):
            narration_text = " ".join(text if text.endswith((".", "?", "!")) else f"{text}."
                                      for text in (storyboard["ending"]["takeaway"], storyboard["ending"]["cta"]))
        if narration_text:
            narration = work / f"{item['kind']}_{index}_narration.aiff"
            asset = narration_audio(narration_text, narration, profile, narration_cache, approved_assets)
            audio_seconds = media_duration(narration)
            card_seconds = max(card_seconds, audio_seconds + 0.4)
            item["narration"] = {"text": narration_text, "voice": profile["voice"],
                                 "rate": profile["rate"], "seconds": audio_seconds, **asset}
            narration_cues.append({"start": item["start"] + 0.15,
                                   "end": item["start"] + 0.15 + audio_seconds, "text": narration_text})
        background, layers = cards.build_card(item["kind"], storyboard, kickers, work, f"card_{index}", width, height,
                                              duration=card_seconds, narration_seconds=audio_seconds if narration else None,
                                              number=clip_index + 1, still=still)
        card = cards.animated_card_video(background, layers, work / f"card_{index}.mp4", width, height, card_seconds,
                                         audio=narration, audience=storyboard["audience"],
                                         drift=cards.card_drift(item["kind"], clip_index + 1, storyboard["audience"]),
                                         kind=item["kind"])
        actual_duration = media_duration(card)
        difference = actual_duration - item["duration"]
        item["duration"] = actual_duration
        for later in timeline[index + 1:]:
            later["start"] += difference
        parts.append(card)
    bare = concat_mp4s(parts, work / "concat_bare.mp4")
    bare = mix_media(bare, work / "mixed.mp4", storyboard, timeline, width, height, work)
    cues = compilation_cues(clips, segments, 0, durations, clip_offsets=clip_offsets)
    if srt_path is not None:
        write_srt(sorted([*cues, *narration_cues], key=lambda cue: cue["start"]), srt_path)
    from pipeline.utils import save_json

    save_json(work / "timeline.json", timeline)
    return burn_captions(bare, cues, dest, width, height, work, graphics=graphics)


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
    narration_cache: Path | None = None,
    approved_assets: dict | None = None,
) -> Path:
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
                                    width, height, work, srt_path, narration_cache, approved_assets)

    intro = make_intro_card(
        work / "intro.mp4",
        title=intro_title,
        subtitle=intro_subtitle,
        width=width,
        height=height,
        seconds=intro_seconds,
    )
    parts = [intro]
    clip_durs = []
    for i, raw in enumerate(raw_clips, 1):
        normalized = _normalize_clip(raw, work / f"norm_{i:02d}.mp4", width, height)
        parts.append(normalized)
        clip_durs.append(media_duration(normalized))

    end = make_end_card(
        work / "end_card.mp4",
        takeaway=end_takeaway or "Review the selected points, then watch the full source.",
        cta=end_cta or "Full discussion in the original lecture. Link in the description.",
        width=width,
        height=height,
        seconds=end_seconds,
    )
    parts.append(end)

    bare = work / "concat_bare.mp4"
    concat_mp4s(parts, bare)

    cues = compilation_cues(clips or [], segments or [], media_duration(intro), clip_durs)
    if srt_path is not None:
        write_srt(cues, srt_path)

    return burn_captions(bare, cues, dest, width, height, work)
