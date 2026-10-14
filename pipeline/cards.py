"""Animated storyboard cards: static background plus text layers revealed on a schedule tied to the narration."""
from __future__ import annotations

import re
from pathlib import Path

from pipeline.assemble import _display_font, _load_fonts, _star, _wrap_to_pixel_width
from pipeline.utils import ffmpeg_bin, run_cmd

MIN_HOLD = 0.8
LINE_SPACING = 4
TYPE_RATIO = {"divider": 0.075, "ending": 0.06}  # starting type size as a share of the short side; fitting shrinks it
PORTRAIT_ANCHOR = 0.58  # block centre as a share of height on 9:16
PORTRAIT_SAFE_BOTTOM = 0.85  # keep text above the app UI zone when it fits
DRIFT_SPAN = 0.7
WORD_REVEAL_MAX_WORDS = 8  # longer blocks reveal by line; word-by-word is for short titles


def _presentation(audience: str) -> dict:
    from pipeline.media import PRESENTATION
    return PRESENTATION.get(audience, PRESENTATION["General public"])


def card_look(audience: str) -> dict:
    return _presentation(audience)["look"]


def card_motion(audience: str) -> dict:
    return _presentation(audience)["motion"]


def _ease(kind: str, u: str) -> str:
    """ffmpeg expression easing u (0..1) towards 1; 'back' overshoots slightly before settling."""
    if kind == "linear":
        return f"({u})"
    if kind == "back":
        v = f"(({u})-1)"
        return f"(1+2.70158*{v}*{v}*{v}+1.70158*{v}*{v})"
    return f"(1-(1-({u}))*(1-({u})))"


def spoken(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else f"{text}."


def runtime_label(seconds) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    return f"{round(seconds)} sec" if seconds < 90 else f"{round(seconds / 60)} min"


def _bare(word: str) -> str:
    return re.sub(r"[\W_]+", "", word).casefold()


def card_accents(kind: str, storyboard: dict, number: int = 1) -> dict:
    """Map the card's title text to the single word the model marked for accent colour."""
    if kind == "opening":
        section, field = storyboard["opening"], "title"
    elif kind == "divider":
        section, field = storyboard["transitions"][number - 2], "label"
    else:
        section, field = storyboard["ending"], "takeaway"
    accent = str(section.get("accent") or "").strip()
    return {section[field]: accent} if accent else {}


def card_drift(kind: str, number: int = 1, audience: str = "General public") -> int:
    """Background pan direction: the opening drifts right, bridges alternate, the ending stays centred."""
    if not card_motion(audience)["drift"]:
        return 0
    if kind == "opening":
        return 1
    return (-1 if number % 2 == 0 else 1) if kind == "divider" else 0


def card_badge(kind: str, storyboard: dict, number: int = 1):
    """Audiences whose look uses a number badge show the bridge count as a large circle, not as chip text."""
    return f"{number:02d}" if kind == "divider" and card_look(storyboard["audience"])["badge"] else None


def card_beats(kind: str, storyboard: dict, kickers: dict, *, number: int = 1, narration: bool = True):
    """Return (beats, corner_lines); a beat is a list of (text, role, narrated) sections shown together."""
    media = bool(storyboard.get("media_assets"))
    label = ["AI illustration"] if media else []
    if kind == "opening":
        opening = storyboard["opening"]
        runtime = runtime_label(storyboard.get("measured_seconds"))
        kicker = f"{kickers['opening']} \u2022 {runtime}" if runtime else kickers["opening"]  # Arial Rounded lacks U+00B7
        beats = [[(kicker, "kicker", False), (opening["title"], "title", narration),
                  (opening["subtitle"], "body", False)]]
        corner = label
    elif kind == "divider":
        text = storyboard["transitions"][number - 2]["label"]
        kicker = kickers["divider"] if card_badge(kind, storyboard, number) else f"{kickers['divider']}  {number:02d}"
        beats = [[(kicker, "kicker", False), (text, "title", narration)]]
        corner = label
    else:
        ending = storyboard["ending"]
        action = kickers.get("ending_action", kickers["ending"])
        if card_look(storyboard["audience"])["ending_hero"] == "cta":
            second = [(action, "kicker", False), (ending["cta"], "title", narration), (ending["question"], "question", False)]
        else:
            second = [(action, "kicker", False), (ending["question"], "title", False), (ending["cta"], "question", narration)]
        beats = [[(kickers["ending"], "kicker", False), (ending["takeaway"], "title", narration)], second]
        corner = [ending.get("credit") or "", *label]
    beats = [[section for section in beat if section[0].strip()] for beat in beats]
    return beats, [line for line in corner if line.strip()]


def _role_styles(audience: str, opening: bool) -> dict:
    look = card_look(audience)
    roles = {"title": (1.35, look["ink"]), "body": (1.0, look["body"]), "question": (1.0, look["accent_text"])}
    if opening:
        roles.update(title=(1.6, look["ink"]), body=(0.75, look["opening_body"]))
    if audience == "Kids":
        roles["title"] = (1.7 if opening else 1.6, look["ink"])
        roles["question"] = (1.15, look["accent_text"])
    return roles


def _advance(font, size: int) -> int:
    try:
        ascent, descent = font.getmetrics()
    except AttributeError:
        ascent, descent = size, round(size * 0.25)
    return ascent + descent


def _accent_span(lines: list[str], accent: str):
    """(line, start, end) of the first word matching the accent word, punctuation included."""
    target = _bare(accent)
    if not target:
        return None
    for index, line in enumerate(lines):
        position = 0
        for word in line.split(" "):
            if _bare(word) == target:
                return index, position, position + len(word)
            position += len(word) + 1
    return None


def fit_card(sections: list[tuple], width: int, height: int, *, opening: bool, audience: str,
             ratio: float = TYPE_RATIO["ending"], accents: dict | None = None, badge: str | None = None) -> dict:
    """Lay out one beat. Never shrinks below a readable size; raises instead so wording can be corrected."""
    from PIL import Image, ImageDraw
    from pipeline.media import CREATIVE

    accent = CREATIVE.get(audience, CREATIVE["General public"])["accent"]
    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    padding = max(8, round(width * 0.1 if opening else min(width, height) * 0.09))
    available_width, available_height = width - padding * 2, height - padding * 2
    roles = _role_styles(audience, opening)
    kicker = next((text for text, role, _ in sections if role == "kicker"), "")
    stack = [section for section in sections if section[1] != "kicker"]
    initial = (max(14, min(110, round(width * 0.095))) if opening
               else max(14, min(100, round(min(width, height) * ratio))))
    floor = min(initial, round(min(width, height) * 0.045))
    for size in range(initial, floor - 1, -1):
        gap = max(6, round(size * 0.6))
        chip = None
        if kicker:
            chip_font = _display_font(audience, max(9, round(size * 0.5)))
            box = probe.textbbox((0, 0), kicker.upper(), font=chip_font)
            pad_x, pad_y = round(size * 0.5), round(size * 0.22)
            chip = {"label": kicker.upper(), "font": chip_font, "box": box, "pad_x": pad_x, "pad_y": pad_y,
                    "w": box[2] - box[0] + 2 * pad_x, "h": box[3] - box[1] + 2 * pad_y}
            chip["row_w"], chip["row_h"] = chip["w"], chip["h"]
            if badge:
                diameter = round(chip["h"] * 2.1)
                badge_font = _display_font(audience, max(9, round(diameter * 0.5)))
                chip["badge"] = {"label": badge, "d": diameter, "font": badge_font, "gap": round(size * 0.35),
                                 "box": probe.textbbox((0, 0), badge, font=badge_font)}
                chip["row_w"] = chip["w"] + chip["badge"]["gap"] + diameter
                chip["row_h"] = max(chip["h"], diameter)
        blocks = []
        for text, role, narrated in stack:
            factor, color = roles[role]
            font = (_display_font(audience, max(8, round(size * factor))) if role == "title"
                    else _load_fonts(max(8, round(size * factor)), size)[0])
            lines = _wrap_to_pixel_width(text, font, available_width)
            widths = [probe.textlength(line, font=font) for line in lines]
            advance = _advance(font, max(8, round(size * factor)))
            span = _accent_span(lines, (accents or {}).get(text, "")) if role == "title" else None
            blocks.append({"role": role, "narrated": narrated, "font": font, "color": color, "lines": lines,
                           "widths": widths, "advance": advance, "accent_span": span,
                           "height": len(lines) * advance + max(0, len(lines) - 1) * LINE_SPACING})
        total = (chip["row_h"] + gap if chip else 0) + sum(block["height"] for block in blocks) + gap * max(0, len(blocks) - 1)
        if (total <= available_height and all(max(block["widths"], default=0) <= available_width for block in blocks)
                and (chip is None or chip["row_w"] <= available_width)):
            break
    else:
        raise ValueError("Card text does not fit at a readable size. Shorten the wording.")
    top = (height - total) // 2
    if height > width:
        top = min(round(height * PORTRAIT_ANCHOR - total / 2), round(height * PORTRAIT_SAFE_BOTTOM) - total)
        top = max(padding, min(top, height - padding - total))
    if chip:
        chip["x"] = (width - chip["row_w"]) / 2 if opening else padding
        chip["y"] = top + (chip["row_h"] - chip["h"]) // 2
        chip["row_y"] = top
        top += chip["row_h"] + gap
    for block in blocks:
        block["y"] = top
        block["x"] = [(width - w) / 2 if opening else padding for w in block["widths"]]
        top += block["height"] + gap
    return {"width": width, "height": height, "opening": opening, "audience": audience, "accent": accent,
            "look": card_look(audience), "chip": chip, "blocks": blocks, "padding": padding, "size": size}


def check_card_text(kind: str, storyboard: dict, kickers: dict, *, number: int = 1) -> None:
    """Verify every beat fits readably on both landscape and portrait canvases."""
    # Draft-time check has no measured runtime yet; test with the widest label.
    beats, _ = card_beats(kind, {**storyboard, "measured_seconds": storyboard.get("measured_seconds") or 88},
                          kickers, number=number)
    for width, height in ((1280, 720), (720, 1280)):
        for beat in beats:
            fit_card(beat, width, height, opening=kind == "opening", audience=storyboard["audience"],
                     ratio=TYPE_RATIO.get(kind, TYPE_RATIO["ending"]), badge=card_badge(kind, storyboard, number))


def render_background(width: int, height: int, *, picture: Path | None, audience: str, opening: bool, mirror: bool = False,
                      blur: bool = False):
    from PIL import Image, ImageDraw, ImageFilter, ImageOps

    look = card_look(audience)
    image = Image.new("RGBA", (width, height), (*look["flat"], 255))
    if picture is not None:
        with Image.open(picture) as frame:
            image = ImageOps.fit(frame.convert("RGBA"), (width, height))
        if mirror:
            image = ImageOps.mirror(image)
        if blur:  # a real frame can hold its own text; keep colour and mood, lose the detail
            image = image.filter(ImageFilter.GaussianBlur(max(6, round(min(width, height) * 0.03))))
        scrim = Image.new("RGBA", image.size, (0, 0, 0, 0))
        scrim_draw = ImageDraw.Draw(scrim)
        centre, rim = look["alpha"]
        for y in range(height):
            edge = abs(y - height / 2) / (height / 2)
            scrim_draw.line([(0, y), (width, y)], fill=(*look["scrim"], round(centre - (centre - rim) * edge ** 1.5)))
        image = Image.alpha_composite(image, scrim)
    if not opening:
        ImageDraw.Draw(image).rectangle((0, 0, max(3, width // 150), height), fill=(*look["accent_text"], 255))
    return image


def decor_layers(audience: str, width: int, height: int, work: Path, name: str, seconds: float) -> list[dict]:
    """Per-audience decor as small separate layers with their own entrance: rules wipe, wedges slide, stars pop."""
    from PIL import Image, ImageDraw
    from pipeline.media import CREATIVE

    accent = CREATIVE.get(audience, CREATIVE["General public"])["accent"]
    look, kind = card_look(audience), _presentation(audience)["decor"]
    thick, unit = max(3, height // 140), min(width, height)
    layers = []

    def add(image, tag, start, **fx):
        path = work / f"{name}_decor_{tag}.png"
        image.save(path)
        layers.append({"png": path, "start": start, "end": None, "slide": False, "decor": True,
                       "fx": {"seconds": seconds, **fx}})

    if kind == "rules":
        for tag, y in (("top", 0.07), ("bottom", 0.93)):
            add(Image.new("RGBA", (round(width * 0.76), thick), (*accent, 230)), tag, 0.05,
                kind="grow", x=round(width * 0.12), y=round(height * y))
    elif kind == "band":
        band_w, band_h, corner_w = round(width * 0.5), round(height * 0.2), round(width * 0.38)
        bottom, top = Image.new("RGBA", (band_w, band_h)), Image.new("RGBA", (corner_w, band_h))
        ImageDraw.Draw(bottom).polygon([(0, 0), (band_w, band_h), (0, band_h)], fill=(*accent, 235))
        ImageDraw.Draw(top).polygon([(corner_w, 0), (0, 0), (corner_w, band_h)], fill=(*accent, 235))
        add(bottom, "bottom", 0.0, kind="slide", x=0, y=height - band_h, dx=-band_w, dy=band_h)
        add(top, "top", 0.0, kind="slide", x=width - corner_w, y=0, dx=corner_w, dy=-band_h)
    else:
        for index, (x, y, size, shape) in enumerate(((0.08, 0.14, 0.06, "star"), (0.92, 0.18, 0.04, "dot"),
                                                      (0.12, 0.86, 0.04, "dot"), (0.90, 0.84, 0.065, "star"),
                                                      (0.50, 0.05, 0.025, "dot"), (0.50, 0.96, 0.025, "dot"))):
            radius = unit * size
            side = round(radius * 2.3) + 4
            image = Image.new("RGBA", (side, side))
            draw = ImageDraw.Draw(image)
            if shape == "star":
                outline = look["star_outline"]
                draw.polygon(_star(side / 2, side / 2, radius), fill=(*accent, 240),
                             outline=(*outline, 255) if outline else None, width=max(2, round(radius * 0.12)))
            else:
                draw.ellipse((side / 2 - radius, side / 2 - radius, side / 2 + radius, side / 2 + radius),
                             fill=(*look["dot"], 235))
            add(image, f"{index}", 0.05 + 0.07 * index, kind="pop", cx=width * x, cy=height * y)
    return layers


def render_layer(layout: dict, steps: list[tuple]):
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", (layout["width"], layout["height"]), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    look = layout["look"]
    for step in steps:
        if step[0] == "chip":
            chip = layout["chip"]
            draw.rounded_rectangle((chip["x"], chip["y"], chip["x"] + chip["w"], chip["y"] + chip["h"]),
                                   radius=round(chip["h"] * look["chip_radius"]), fill=(*layout["accent"], 255))
            draw.text((chip["x"] + chip["pad_x"] - chip["box"][0], chip["y"] + chip["pad_y"] - chip["box"][1]),
                      chip["label"], font=chip["font"], fill=(*look["chip_ink"], 255))
            badge = chip.get("badge")
            if badge:
                left, top, d = chip["x"] + chip["w"] + badge["gap"], chip["row_y"], badge["d"]
                draw.ellipse((left, top, left + d, top + d), fill=(*look["accent_text"], 255))
                box = badge["box"]
                draw.text((left + (d - (box[2] - box[0])) / 2 - box[0], top + (d - (box[3] - box[1])) / 2 - box[1]),
                          badge["label"], font=badge["font"], fill=(255, 255, 255, 255))
        else:
            block = layout["blocks"][step[1]]
            line = step[2]
            y = block["y"] + line * (block["advance"] + LINE_SPACING)
            text, x, font = block["lines"][line], block["x"][line], block["font"]
            span = block["accent_span"]
            if step[0] == "word":
                first, last = step[3], step[4]
                marked = span == (line, first, last)
                draw.text((x + draw.textlength(text[:first], font=font), y), text[first:last], font=font,
                          fill=(*(look["accent_text"] if marked else block["color"]), 255))
            elif span and span[0] == line:
                for part, color in ((text[:span[1]], block["color"]), (text[span[1]:span[2]], look["accent_text"]),
                                    (text[span[2]:], block["color"])):
                    draw.text((x, y), part, font=font, fill=(*color, 255))
                    x += draw.textlength(part, font=font)
            else:
                draw.text((x, y), text, font=font, fill=(*block["color"], 255))
    return layer


def render_corner(lines: list[str], width: int, height: int, audience: str):
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    size = max(11, round(min(width, height) * 0.024))
    font = _load_fonts(size, size)[1]
    draw = ImageDraw.Draw(layer)
    margin = max(8, round(min(width, height) * 0.03))
    y = height - margin
    for text in reversed(lines):
        box = draw.textbbox((0, 0), text, font=font)
        w, h = box[2] - box[0], box[3] - box[1]
        pad = round(size * 0.35)
        draw.rounded_rectangle((width - margin - w - 2 * pad, y - h - 2 * pad, width - margin, y), radius=pad,
                               fill=(0, 0, 0, 150))
        draw.text((width - margin - w - pad - box[0], y - h - pad - box[1]), text, font=font, fill=(228, 232, 234, 255))
        y -= h + 2 * pad + round(size * 0.3)
    return layer


def _units(block: dict, index: int, reveal: str) -> list[tuple[int, list[tuple]]]:
    """Split a block into reveal units of (characters, steps): the whole block, single words, or lines."""
    lines = block["lines"]
    if reveal == "block":
        return [(sum(len(line) for line in lines), [("line", index, number) for number in range(len(lines))])]
    if (reveal == "word" and block["role"] == "title" and block["narrated"]
            and sum(len(line.split()) for line in lines) <= WORD_REVEAL_MAX_WORDS):
        units = []
        for number, line in enumerate(lines):
            position = 0
            for word in line.split(" "):
                units.append((len(word) + 1, [("word", index, number, position, position + len(word))]))
                position += len(word) + 1
        return units
    return [(len(line), [("line", index, number)]) for number, line in enumerate(lines)]


def _beat_times(layout: dict, begin: float, end: float, speak_from: float, speak_to: float,
                reveal: str = "line") -> list[tuple[float, tuple]]:
    """Reveal chip first, narrated units as the voice reaches them, visual-only units around them."""
    steps = []
    if layout["chip"]:
        steps.append((begin + 0.05, ("chip",)))
    blocks = [_units(block, index, reveal) for index, block in enumerate(layout["blocks"])]
    narrated_chars = sum(chars for block, units in zip(layout["blocks"], blocks) if block["narrated"]
                         for chars, _ in units) or 1
    earliest = begin + (0.2 if layout["chip"] else 0.05)
    spoken_so_far, seen_narrated, visual_index = 0, False, 0
    for block, units in zip(layout["blocks"], blocks):
        for chars, unit_steps in units:
            if block["narrated"]:
                moment = max(earliest, speak_from + (speak_to - speak_from) * 0.9 * spoken_so_far / narrated_chars)
                spoken_so_far += chars
                seen_narrated = True
            else:
                moment = (speak_to + 0.1 if seen_narrated else begin + 0.3) + 0.2 * visual_index
                visual_index += 1
            for step in unit_steps:
                steps.append((max(begin, min(moment, end - MIN_HOLD)), step))
    return steps


def build_card(kind: str, storyboard: dict, kickers: dict, work: Path, name: str, width: int, height: int, *,
               duration: float, narration_seconds: float | None, number: int = 1, still: Path | None = None,
               fade_dy: int | None = None) -> tuple[Path, list[dict]]:
    """Write the background and text-layer PNGs for one card; returns (background, layers with start/end times)."""
    audience = storyboard["audience"]
    opening = kind == "opening"
    narrated = narration_seconds is not None
    beats, corner = card_beats(kind, storyboard, kickers, number=number, narration=narrated)
    accents = card_accents(kind, storyboard, number)
    badge = card_badge(kind, storyboard, number)
    layouts = [fit_card(beat, width, height, opening=opening, audience=audience,
                        ratio=TYPE_RATIO.get(kind, TYPE_RATIO["ending"]), accents=accents, badge=badge) for beat in beats]
    media = storyboard.get("media_assets") or {}
    picture, mirror = still, False
    if media:
        if opening:
            picture = Path(media["opening"]["path"])
        elif kind == "divider":
            slot = number - 2
            picture, mirror = Path(media["opening" if slot % 2 == 0 else "ending"]["path"]), (slot // 2) % 2 == 1
        else:
            picture = Path(media["ending"]["path"])
    elif kind == "ending":
        picture = None
    background = work / f"{name}_bg.png"
    background.parent.mkdir(parents=True, exist_ok=True)
    render_background(width, height, picture=picture, audience=audience, opening=opening, mirror=mirror,
                      blur=still is not None and not media).save(background)

    speech_from = 0.15
    speech_to = speech_from + (narration_seconds if narrated else max(0.6, (duration - 0.2) * 0.6))
    windows = []
    if len(layouts) == 2:
        first_chars = sum(len(spoken(text)) for text, role, flag in beats[0] if flag) or 1
        second_chars = sum(len(spoken(text)) for text, role, flag in beats[1] if flag) or 1
        split = speech_from + (speech_to - speech_from) * first_chars / (first_chars + second_chars)
        switch = min(max(split + 0.15, 1.3), max(1.3, duration - 1.5))
        windows = [(0.0, switch, speech_from, split), (switch, duration, split, speech_to)]
    else:
        windows = [(0.0, duration, speech_from, speech_to)]
    layers = decor_layers(audience, width, height, work, name, card_motion(audience)["decor_seconds"]) \
        if opening or picture is not None else []
    for layout, (begin, end, from_, to_) in zip(layouts, windows):
        timed = _beat_times(layout, begin, end, from_, to_, card_motion(audience)["reveal"])
        grouped: dict[float, list] = {}
        for moment, step in sorted(timed, key=lambda item: item[0]):
            grouped.setdefault(round(moment, 2), []).append(step)
        for moment, steps in grouped.items():
            png = work / f"{name}_{len(layers):02d}.png"
            render_layer(layout, steps).save(png)
            layers.append({"png": png, "start": moment, "end": end if len(layouts) == 2 and begin == 0.0 else None,
                           "slide": True})
    if corner:
        png = work / f"{name}_corner.png"
        render_corner(corner, width, height, audience).save(png)
        layers.append({"png": png, "start": 0.2, "end": None, "slide": False})
    return background, layers


def animated_card_video(background: Path, layers: list[dict], dest: Path, width: int, height: int, seconds: float,
                        fps: int = 30, *, audio: Path | None = None, drift: int = 0,
                        audience: str = "General public") -> Path:
    frames = max(1, round(seconds * fps))
    motion = card_motion(audience)
    fade, ease = motion["fade"], motion["ease"]
    # The pan range grows with the zoom, so the drift is a fraction of it: 0.5 centred, +/- moves left or right.
    pan = (f"(iw-iw/zoom)*({0.5 - DRIFT_SPAN / 2 * drift:.3f}+{DRIFT_SPAN * drift:.3f}*min(on/{frames},1))"
           if drift else "(iw-iw/zoom)/2")
    shift = max(2, round(height * motion["slide"]))
    command = [ffmpeg_bin(), "-y"]
    for source in [background, *(layer["png"] for layer in layers)]:
        command += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.3f}", "-i", str(source)]
    command += ["-i", str(audio)] if audio is not None else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    audio_index = len(layers) + 1
    zoom = (f"scale={width * 2}:{height * 2},zoompan=z='1+{motion['zoom'] - 1:.3f}*{_ease(ease, f'min(on/{frames},1)')}':"
            f"x='{pan}':y='(ih-ih/zoom)/2':d=1:s={width}x{height}:fps={fps},format=yuv420p")
    filters = [f"[0:v]{zoom}[v0]"]
    for index, layer in enumerate(layers, 1):
        start, fx = layer["start"], layer.get("fx")
        if fx:
            eased = _ease(ease, f"clip((t-{start:.3f})/{fx['seconds']:.3f},0,1)")
            if fx["kind"] == "slide":
                filters.append(f"[{index}:v]format=rgba[l{index}]")
                overlay = f"overlay=x='{fx['x']}+({fx['dx']})*(1-{eased})':y='{fx['y']}+({fx['dy']})*(1-{eased})'"
            elif fx["kind"] == "grow":
                filters.append(f"[{index}:v]format=rgba,scale=w='max(2,iw*{eased})':h=ih:eval=frame[l{index}]")
                overlay = f"overlay=x={fx['x']}:y={fx['y']}:enable='gte(t,{start:.3f})'"
            else:
                filters.append(f"[{index}:v]format=rgba,scale=w='max(2,iw*{eased})':h='max(2,ih*{eased})':eval=frame[l{index}]")
                overlay = f"overlay=x='{fx['cx']:.1f}-w/2':y='{fx['cy']:.1f}-h/2':enable='gte(t,{start:.3f})'"
            filters.append(f"[v{index - 1}][l{index}]{overlay}[v{index}]")
            continue
        chain = f"format=rgba,fade=t=in:st={start:.3f}:d={fade}:alpha=1"
        if layer["end"] is not None:
            chain += f",fade=t=out:st={max(start, layer['end'] - 0.2):.3f}:d=0.2:alpha=1"
        filters.append(f"[{index}:v]{chain}[l{index}]")
        slide = _ease(ease, f"(t-{start:.3f})/{fade}")
        offset = (f"y='if(lt(t,{start:.3f}),{shift},if(lt(t,{start + fade:.3f}),"
                  f"{shift}*(1-{slide}),0))'") if layer["slide"] else "y=0"
        filters.append(f"[v{index - 1}][l{index}]overlay=x=0:{offset}[v{index}]")
    filters.append(f"[v{len(layers)}]format=yuv420p,setsar=1[vout]")
    command += ["-filter_complex", ";".join(filters), "-map", "[vout]", "-map", f"{audio_index}:a"]
    if audio is not None:
        command += ["-af", "adelay=150:all=1,apad"]
    command += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100",
                "-ac", "2", "-t", f"{seconds:.2f}", "-shortest", str(dest)]
    run_cmd(command)
    return dest
