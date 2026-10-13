from __future__ import annotations

from pathlib import Path

from pipeline.assemble import _even, _storyboard_panel, burn_captions, concat_mp4s
from pipeline.cut import cut_clip
from pipeline.narrate import join_wavs, split_sentences, synthesize, wav_duration
from pipeline.progress import NO_PROGRESS, StepProgress
from pipeline.utils import canvas_size, ffmpeg_bin, media_duration, media_size, run_cmd, save_json, write_srt

REMAKE_VIDEO_STEPS = 4
BACKGROUNDS = ["Source footage (muted)", "Title cards"]
CAPTION_CHARS = 38


def _caption_chunks(sentence: str, start: float, duration: float) -> list[dict]:
    """Split one spoken sentence into short caption cues, timed by character share."""
    words, chunks, current = sentence.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and len(trial) > CAPTION_CHARS:
            chunks.append(current)
            current = word
        else:
            current = trial
    if current:
        chunks.append(current)
    total = sum(len(chunk) for chunk in chunks) or 1
    cues, cursor = [], start
    for chunk in chunks:
        length = duration * len(chunk) / total
        cues.append({"start": round(cursor, 3), "end": round(cursor + length, 3), "text": chunk})
        cursor += length
    return cues


def _render_part(dest: Path, width: int, height: int, seconds: float, audio: Path,
                 *, png: Path | None = None, footage: Path | None = None, fps: int = 30) -> Path:
    cmd = [ffmpeg_bin(), "-y"]
    if footage is not None:
        cmd += ["-stream_loop", "-1", "-i", str(footage)]
    else:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(png)]
    cmd += ["-i", str(audio)]
    video_filter = (f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p,setsar=1[v]")
    cmd += [
        "-filter_complex", f"{video_filter};[1:a]apad,aresample=44100[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{seconds:.3f}",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2", str(dest),
    ]
    run_cmd(cmd)
    return dest


def _plain_background(dest: Path, width: int, height: int) -> Path:
    from PIL import Image

    if not dest.exists():
        Image.new("RGB", (width, height), (22, 27, 30)).save(dest)
    return dest


def _footage_window(start: float, end: float, needed: float, source_duration: float) -> tuple[float, float]:
    """Use the cited range, extended to cover the narration when the source allows it."""
    start = max(0.0, min(float(start), source_duration))
    end = min(source_duration, max(float(end), start + needed))
    if end - start < needed:
        start = max(0.0, end - needed)
    return start, max(end, start + 0.5)


def _script_units(remake: dict) -> list[dict]:
    units = [{"kind": "opening", "speech": f"{remake['title']}. {remake.get('hook', '')}".strip()}]
    for section in remake.get("sections", []):
        units.append({"kind": "section", "heading": section.get("heading", ""), "speech": section.get("text", ""),
                      "source_start": float(section["source_start"]), "source_end": float(section["source_end"])})
    if str(remake.get("takeaway", "")).strip():
        units.append({"kind": "ending", "speech": remake["takeaway"]})
    for unit in units:
        unit["sentences"] = split_sentences(unit["speech"])
    return [unit for unit in units if unit["sentences"]]


def render_remake_video(
    remake: dict,
    work: Path,
    *,
    source_video: str | None = None,
    aspect_ratio: str = "Original",
    background: str = BACKGROUNDS[0],
    voice: str | None = None,
    rate: int = 0,
    credit: str = "Source lecture",
    tracker: StepProgress = NO_PROGRESS,
) -> dict:
    """Narrate a remake script with local TTS and render it in the clip compilation style.

    Uses REMAKE_VIDEO_STEPS steps of `tracker`."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    use_footage = background == BACKGROUNDS[0] and bool(source_video)
    if use_footage:
        src_w, src_h = media_size(source_video)
        source_duration = media_duration(source_video)
    else:
        src_w, src_h, source_duration = 1920, 1080, 0.0
    width, height = canvas_size(src_w, src_h, aspect_ratio)
    width, height = _even(width), _even(height)

    units = _script_units(remake)
    if not units:
        raise ValueError("The remake script has nothing to narrate.")
    sentences = [sentence for unit in units for sentence in unit["sentences"]]
    tracker.step(f"Synthesizing speech ({len(sentences)} sentences)")
    wavs = iter(synthesize(sentences, work / "speech", voice, rate,
                           on_count=lambda done, total: tracker.count(done, total, "sentences")))

    parts, cues, graphics, timeline = [], [], [], []
    cursor = 0.0
    tracker.step("Rendering title card, sections and takeaway")
    for index, unit in enumerate(units):
        tracker.count(index, len(units), "parts")
        unit_wavs = [next(wavs) for _ in unit["sentences"]]
        narration = work / f"narration_{index:02d}.wav"
        starts = join_wavs(unit_wavs, narration)
        seconds = wav_duration(narration)
        dest = work / f"part_{index:02d}.mp4"
        if unit["kind"] == "section":
            if use_footage:
                clip_start, clip_end = _footage_window(unit["source_start"], unit["source_end"], seconds, source_duration)
                footage = cut_clip(source_video, clip_start, clip_end, work / f"footage_{index:02d}.mp4")
                _render_part(dest, width, height, seconds, narration, footage=footage)
            else:
                plain = _plain_background(work / "background.png", width, height)
                _render_part(dest, width, height, seconds, narration, png=plain)
            if unit["heading"].strip():
                label = _storyboard_panel(work / f"label_{index:02d}.png", [(unit["heading"], "title")],
                                          round(width * 0.84), round(height * 0.25))
                graphics.append({"png": label, "start": cursor, "end": cursor + seconds, "y": round(height * 0.05)})
        else:
            if unit["kind"] == "opening":
                sections = [(remake["title"], "title"), (remake.get("hook", ""), "body")]
            else:
                sections = [("Key takeaway", "credit"), (remake["takeaway"], "title"), (credit, "credit")]
            png = _storyboard_panel(work / f"card_{index:02d}.png", sections, width, height,
                                    opening=unit["kind"] == "opening")
            _render_part(dest, width, height, seconds, narration, png=png)
        actual = media_duration(dest)
        if unit["kind"] == "section":
            for sentence, wav, start in zip(unit["sentences"], unit_wavs, starts):
                cues.extend(_caption_chunks(sentence, cursor + start, wav_duration(wav)))
        timeline.append({"kind": unit["kind"], "start": round(cursor, 3), "duration": round(actual, 3),
                         "heading": unit.get("heading", ""), "sentences": unit["sentences"]})
        parts.append(dest)
        cursor += actual

    tracker.step("Joining parts")
    bare = concat_mp4s(parts, work / "remake_bare.mp4", on_fraction=tracker.fraction)
    srt_path = work / "remake_captions.srt"
    write_srt(cues, srt_path)
    tracker.step("Burning captions and headings")
    output = burn_captions(bare, cues, work / "remake_video.mp4", width, height, work, graphics=graphics,
                           on_fraction=tracker.fraction)
    save_json(work / "remake_timeline.json", timeline)
    return {"video": str(output), "srt": str(srt_path), "duration": media_duration(output),
            "width": width, "height": height, "background": "footage" if use_footage else "cards"}
