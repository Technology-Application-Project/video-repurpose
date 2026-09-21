from __future__ import annotations

import shutil
import sys
from pathlib import Path

import gradio as gr

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.cut import make_preview
from pipeline.export import DEFAULT_CTA, export_clips
from pipeline.nominate import list_ollama_models, nominate_candidates
from pipeline.rank import rank_candidates
from pipeline.transcribe import transcribe_video
from pipeline.utils import (
    MAX_KEEP,
    MAX_REVIEW,
    MIN_KEEP,
    as_video_path,
    excerpt_for_range,
    ffmpeg_bin,
    media_duration,
    new_run_id,
    parse_objectives,
    run_dir,
)

ASPECT_RATIOS = ["Original", "16:9", "9:16", "1:1"]


def empty_state() -> dict:
    return {
        "run_id": None,
        "work": None,
        "source_video": None,
        "topic": "",
        "objectives": [],
        "source_url": "",
        "min_s": 20.0,
        "max_s": 45.0,
        "aspect_ratio": "Original",
        "model": "qwen2.5",
        "transcript": None,
        "candidates": [],
        "approved": [],
        "confirmed": False,
    }


def ping_stack(current_model: str | None):
    notes = []
    try:
        ffmpeg_bin()
        notes.append("ffmpeg ready")
    except Exception as exc:
        notes.append(f"ffmpeg missing: {exc}")

    models, ollama_msg = list_ollama_models()
    notes.append(ollama_msg)
    if not models:
        return gr.update(choices=["qwen2.5"], value="qwen2.5"), " · ".join(notes)

    value = current_model if current_model in models else models[0]
    if current_model and current_model not in models:
        exact = next((m for m in models if m.lower() in {"qwen2.5", "qwen2.5:latest"}), None)
        value = exact or next((m for m in models if "qwen2.5" in m.lower()), models[0])
    return gr.update(choices=models, value=value), " · ".join(notes)


def _blank_row():
    return (
        gr.update(visible=False),
        None,
        "",
        "",
        "",
        "",
        None,
        None,
        False,
    )


def _row_from_candidate(cand: dict, visible: bool = True):
    return (
        gr.update(visible=visible),
        cand.get("preview_path"),
        cand.get("id", ""),
        cand.get("title", ""),
        cand.get("takeaway", ""),
        f"{cand.get('score', 0):.3f} | {cand.get('reason', '')}",
        float(cand.get("start", 0)),
        float(cand.get("end", 0)),
        False,
    )


def transcribe(video, topic, objectives_text, source_url, min_s, max_s, aspect_ratio, model, state, progress=gr.Progress()):
    state = dict(state or empty_state())
    path = as_video_path(video)
    if not path:
        raise gr.Error("Upload a local video first.")
    if not (topic or "").strip():
        raise gr.Error("Enter a teaching topic.")
    objectives = parse_objectives(objectives_text)
    if not objectives:
        raise gr.Error("Enter 3–5 learning objectives, one per line.")

    progress(0.05, desc="Checking ffmpeg")
    ffmpeg_bin()
    run_id = new_run_id()
    work = run_dir(run_id)
    suffix = Path(path).suffix or ".mp4"
    stored = work / f"source{suffix}"
    shutil.copy2(path, stored)

    progress(0.2, desc="Transcribing with WhisperX (first run is slow)")
    transcript = transcribe_video(str(stored), work)
    segments = transcript.get("segments") or []
    if not segments:
        raise gr.Error("WhisperX produced an empty transcript.")

    state.update(
        {
            "run_id": run_id,
            "work": str(work),
            "source_video": str(stored),
            "topic": topic.strip(),
            "objectives": objectives,
            "source_url": (source_url or "").strip(),
            "min_s": float(min_s),
            "max_s": float(max_s),
            "aspect_ratio": aspect_ratio or "Original",
            "model": model or "qwen2.5",
            "transcript": transcript,
            "candidates": [],
            "approved": [],
            "confirmed": False,
        }
    )
    viewer = transcript_markdown(segments)
    status = (
        f"Transcribed {len(segments)} segments · language `{transcript.get('language')}` · "
        f"run `{run_id}`. Next: nominate + rank."
    )
    return (
        state,
        status,
        viewer,
        "Transcript ready. Click **Nominate + rank clips**.",
        [],
        "**Kept: 0 / 3–5 required**",
        gr.update(interactive=False),
        gr.update(interactive=False),
    )


def transcript_markdown(segments: list[dict], limit: int = 80) -> str:
    lines = ["| Start | End | Text |", "|---:|---:|---|"]
    for seg in segments[:limit]:
        text = str(seg.get("text", "")).replace("|", "\\|")
        lines.append(f"| {seg['start']:.2f} | {seg['end']:.2f} | {text} |")
    if len(segments) > limit:
        lines.append(f"\n_{len(segments) - limit} more segments omitted._")
    return "\n".join(lines)


def nominate_and_rank(state, model, progress=gr.Progress()):
    state = dict(state or empty_state())
    if not state.get("transcript"):
        raise gr.Error("Run transcription first.")
    models, msg = list_ollama_models()
    if not models:
        raise gr.Error(msg)

    segments = state["transcript"]["segments"]
    progress(0.15, desc="Ollama nominating clips")
    duration = media_duration(state["source_video"])
    candidates = nominate_candidates(
        model=model or state.get("model") or "qwen2.5",
        topic=state["topic"],
        objectives=state["objectives"],
        segments=segments,
        min_s=float(state["min_s"]),
        max_s=float(state["max_s"]),
        duration=duration,
    )
    progress(0.55, desc="Ranking against learning objectives")
    ranked = rank_candidates(candidates, state["objectives"], segments)
    progress(0.7, desc="Building previews")
    work = Path(state["work"])
    for cand in ranked:
        preview = work / "previews" / f"{cand['id']}.mp4"
        make_preview(state["source_video"], cand["start"], cand["end"], preview)
        cand["preview_path"] = str(preview)

    state["candidates"] = ranked
    state["approved"] = []
    state["confirmed"] = False
    state["model"] = model or state.get("model")
    table = [
        [
            c["id"],
            c["score"],
            c["start"],
            c["end"],
            c["title"],
            c["takeaway"],
        ]
        for c in ranked
    ]
    status = f"Nominated {len(candidates)} · showing Top {len(ranked)} for human review. Keep 3–5 on-topic clips."
    rows = []
    for i in range(MAX_REVIEW):
        if i < len(ranked):
            rows.extend(_row_from_candidate(ranked[i]))
        else:
            rows.extend(_blank_row())
    return (
        state,
        status,
        table,
        "**Kept: 0 / 3–5 required**",
        gr.update(interactive=False),
        gr.update(interactive=False),
        *rows,
    )


def count_kept(state, *keeps):
    state = dict(state or empty_state())
    n = sum(1 for keep in keeps if keep)
    if not (MIN_KEEP <= n <= MAX_KEEP):
        state["confirmed"] = False
    msg = f"**Kept: {n} / {MIN_KEEP}–{MAX_KEEP} required**"
    return (
        state,
        msg,
        gr.update(interactive=(MIN_KEEP <= n <= MAX_KEEP)),
        gr.update(interactive=bool(state.get("confirmed"))),
    )


def _read_rows(cids, titles, takeaways, starts, ends, keeps, state) -> list[dict]:
    segments = (state.get("transcript") or {}).get("segments") or []
    lookup = {c["id"]: c for c in state.get("candidates") or []}
    kept = []
    for cid, title, takeaway, start, end, keep in zip(cids, titles, takeaways, starts, ends, keeps):
        if not keep or not cid:
            continue
        start_f, end_f = float(start), float(end)
        start_f = max(0.0, start_f)
        base = dict(lookup.get(cid) or {"id": cid})
        base.update(
            {
                "id": cid,
                "title": (title or base.get("title") or cid).strip(),
                "takeaway": (takeaway or base.get("takeaway") or "").strip(),
                "start": round(start_f, 3),
                "end": round(end_f, 3),
                "excerpt": excerpt_for_range(segments, start_f, end_f) if segments else base.get("excerpt", ""),
            }
        )
        if base["end"] <= base["start"]:
            raise gr.Error(f"{cid}: end must be after start.")
        kept.append(base)
    return kept


def confirm_selection(state, *row_values):
    state = dict(state or empty_state())
    n = MAX_REVIEW
    cids = list(row_values[0:n])
    titles = list(row_values[n : 2 * n])
    takeaways = list(row_values[2 * n : 3 * n])
    starts = list(row_values[3 * n : 4 * n])
    ends = list(row_values[4 * n : 5 * n])
    keeps = list(row_values[5 * n : 6 * n])
    kept = _read_rows(cids, titles, takeaways, starts, ends, keeps, state)
    if not (MIN_KEEP <= len(kept) <= MAX_KEEP):
        raise gr.Error("Keep 3–5 on-topic clips before confirming.")
    state["approved"] = kept
    state["confirmed"] = True
    status = f"Confirmed {len(kept)} clips. Packaging is unlocked on tab 4."
    joined = "\n".join(
        f"• {(c.get('takeaway') or c.get('title') or '').strip()}"
        for c in kept
        if (c.get("takeaway") or c.get("title"))
    )
    pack_updates = []
    for i in range(MAX_KEEP):
        if i < len(kept):
            pack_updates.extend(
                [
                    gr.update(visible=True),
                    kept[i]["title"],
                    kept[i].get("takeaway", ""),
                ]
            )
        else:
            pack_updates.extend([gr.update(visible=False), "", ""])
    return (
        state,
        status,
        f"**Kept: {len(kept)} / {MIN_KEEP}–{MAX_KEEP} required · confirmed**",
        gr.update(interactive=True),
        state.get("topic") or "Highlights",
        joined,
        *pack_updates,
    )


def refresh_one_preview(index: int, start, end, state):
    state = dict(state or empty_state())
    candidates = state.get("candidates") or []
    if index >= len(candidates) or not state.get("source_video"):
        return None
    cand = candidates[index]
    start_f = float(start if start is not None else cand["start"])
    end_f = float(end if end is not None else cand["end"])
    if end_f <= start_f:
        raise gr.Error("End must be after start.")
    dest = Path(state["work"]) / "previews" / f"{cand['id']}_edit.mp4"
    make_preview(state["source_video"], start_f, end_f, dest)
    return str(dest)


def do_export(
    state,
    cta_text,
    intro_title,
    intro_subtitle,
    intro_seconds,
    end_takeaway,
    end_seconds,
    aspect_ratio,
    *row_values,
    progress=gr.Progress(),
):
    state = dict(state or empty_state())
    n = MAX_REVIEW
    cids = list(row_values[0:n])
    titles = list(row_values[n : 2 * n])
    takeaways = list(row_values[2 * n : 3 * n])
    starts = list(row_values[3 * n : 4 * n])
    ends = list(row_values[4 * n : 5 * n])
    keeps = list(row_values[5 * n : 6 * n])
    if cids:
        kept = _read_rows(cids, titles, takeaways, starts, ends, keeps, state)
        if kept:
            state["approved"] = kept
            state["confirmed"] = MIN_KEEP <= len(kept) <= MAX_KEEP
    if not state.get("confirmed"):
        raise gr.Error("Confirm 3–5 clips on the Review tab first.")
    clips = [dict(c) for c in state.get("approved") or []]
    if not (MIN_KEEP <= len(clips) <= MAX_KEEP):
        raise gr.Error("Keep 3–5 on-topic clips before export.")

    progress(0.2, desc="Assembling compilation")
    _, posts, zip_path, compilation = export_clips(
        run_id=state["run_id"],
        source_video=state["source_video"],
        segments=state["transcript"]["segments"],
        clips=clips,
        source_url=state.get("source_url") or "",
        work=Path(state["work"]),
        cta_text=cta_text or DEFAULT_CTA,
        intro_title=(intro_title or state.get("topic") or "Highlights").strip(),
        intro_subtitle=(intro_subtitle or "").strip(),
        intro_seconds=float(intro_seconds or 1.5),
        end_takeaway=(end_takeaway or "").strip(),
        end_seconds=float(end_seconds or 3.0),
        aspect_ratio=(aspect_ratio or state.get("aspect_ratio") or "Original"),
    )
    status = f"Exported compilation to `{compilation}`."
    note = (
        "This file is the assembled Demo/YouTube cut: intro + selected clips + takeaway/CTA. "
        "Captions are burned in. Put the source URL in the description; the on-screen CTA has no clickable link."
    )
    return state, status, compilation, f"{posts}\n\n{note}", str(zip_path)


def build_app():
    with gr.Blocks(title="Educational Short Clip Demo") as demo:
        state = gr.State(empty_state())
        gr.Markdown(
            "# Educational Short Clip Demo\n"
            "Transcribe a long lecture, let Ollama nominate clips, rank them against "
            "learning objectives, **human-keep 3–5 on-topic clips**, then package for social."
        )
        stack_status = gr.Markdown("Checking Ollama and ffmpeg...")

        with gr.Tabs():
            with gr.Tab("1. Input"):
                video = gr.Video(label="Source video (local mp4)", sources=["upload"])
                topic = gr.Textbox(
                    label="Teaching topic",
                    placeholder="Which country has the best education system?",
                )
                objectives = gr.Textbox(
                    label="Learning objectives (one per line, 3–5)",
                    lines=5,
                    placeholder="How education systems are measured\nEnrolment vs test scores\nWhy global rankings can mislead",
                )
                source_url = gr.Textbox(label="Source video URL (for CTA / post.txt)")
                with gr.Row():
                    min_s = gr.Slider(10, 40, value=20, step=1, label="Min clip seconds")
                    max_s = gr.Slider(20, 90, value=45, step=1, label="Max clip seconds")
                with gr.Row():
                    aspect_ratio = gr.Dropdown(
                        ASPECT_RATIOS,
                        value="Original",
                        label="Aspect ratio",
                    )
                    model = gr.Dropdown(["qwen2.5"], value="qwen2.5", label="Ollama model", allow_custom_value=True)
                transcribe_btn = gr.Button("Run transcription", variant="primary")
                input_status = gr.Markdown("Upload a video, then transcribe.")

            with gr.Tab("2. Transcript + candidates"):
                transcript_view = gr.Markdown("_Transcript will appear here._")
                nominate_btn = gr.Button("Nominate + rank clips", variant="primary")
                cand_status = gr.Markdown("Run transcription first.")
                cand_table = gr.Dataframe(
                    headers=["id", "score", "start", "end", "title", "takeaway"],
                    label="Ranked candidates (read-only)",
                    interactive=False,
                    wrap=True,
                )

            with gr.Tab("3. Review"):
                gr.Markdown(
                    "Listen to Top N. Keep a clip only if it is **on-topic**, standalone, "
                    "and one knowledge point. A weak opening is OK — adjust start/end instead of discarding."
                )
                kept_md = gr.Markdown("**Kept: 0 / 3–5 required**")
                confirm_btn = gr.Button("Confirm selection", variant="primary", interactive=False)
                review_status = gr.Markdown("Nominate clips first.")
                groups, previews, cids, titles, takeaways, metas, starts, ends, keeps, refresh_btns = (
                    [],
                    [],
                    [],
                    [],
                    [],
                    [],
                    [],
                    [],
                    [],
                    [],
                )
                for i in range(MAX_REVIEW):
                    with gr.Group(visible=False) as group:
                        gr.Markdown(f"### Candidate {i + 1}")
                        with gr.Row():
                            preview = gr.Video(label="Preview", interactive=False)
                            with gr.Column():
                                cid = gr.Textbox(label="ID", interactive=False)
                                title = gr.Textbox(label="Title")
                                takeaway = gr.Textbox(label="Takeaway", lines=2)
                                meta = gr.Textbox(label="Score / reason", interactive=False)
                                with gr.Row():
                                    start = gr.Number(label="Start (s)")
                                    end = gr.Number(label="End (s)")
                                keep = gr.Checkbox(label="Keep (on-topic)")
                                refresh = gr.Button("Update preview")
                    groups.append(group)
                    previews.append(preview)
                    cids.append(cid)
                    titles.append(title)
                    takeaways.append(takeaway)
                    metas.append(meta)
                    starts.append(start)
                    ends.append(end)
                    keeps.append(keep)
                    refresh_btns.append(refresh)

            with gr.Tab("4. Package + export"):
                gr.Markdown(
                    "Export **one** compilation for Demo/YouTube: intro + selected clips with captions, "
                    "then a key-takeaway / CTA end card. Separate raw clips are not exported."
                )
                intro_title = gr.Textbox(label="Compilation intro title", placeholder="What is deep learning?")
                intro_subtitle = gr.Textbox(
                    label="Compilation intro subtitle",
                    placeholder="Neural nets, data, and why it took over AI",
                )
                with gr.Row():
                    intro_seconds = gr.Slider(1.0, 2.0, value=1.5, step=0.1, label="Intro card seconds")
                    end_seconds = gr.Slider(2.0, 5.0, value=3.0, step=0.5, label="End card seconds")
                end_takeaway = gr.Textbox(label="Key takeaway (end card)", lines=5)
                cta_text = gr.Textbox(value=DEFAULT_CTA, label="CTA (end card, no URL)")
                pack_groups, pack_titles, pack_notes = [], [], []
                for i in range(MAX_KEEP):
                    with gr.Group(visible=False) as pg:
                        pt = gr.Textbox(label=f"Clip {i + 1} title", interactive=False)
                        pn = gr.Textbox(label=f"Clip {i + 1} takeaway", interactive=False)
                    pack_groups.append(pg)
                    pack_titles.append(pt)
                    pack_notes.append(pn)
                export_btn = gr.Button("Export compilation", variant="primary", interactive=False)
                export_status = gr.Markdown("Confirm 3–5 clips before export.")
                compilation_video = gr.Video(label="compilation.mp4", interactive=False)
                post_view = gr.Textbox(label="Generated post.txt", lines=14)
                zip_file = gr.File(label="Download zip")

        transcribe_btn.click(
            transcribe,
            [video, topic, objectives, source_url, min_s, max_s, aspect_ratio, model, state],
            [state, input_status, transcript_view, cand_status, cand_table, kept_md, confirm_btn, export_btn],
        )
        nominate_outputs = [
            state,
            cand_status,
            cand_table,
            kept_md,
            confirm_btn,
            export_btn,
        ]
        for i in range(MAX_REVIEW):
            nominate_outputs.extend(
                [groups[i], previews[i], cids[i], titles[i], takeaways[i], metas[i], starts[i], ends[i], keeps[i]]
            )
        nominate_btn.click(nominate_and_rank, [state, model], nominate_outputs)

        def bind_refresh(index):
            def _run(start, end, st):
                return refresh_one_preview(index, start, end, st)

            return _run

        for i, btn in enumerate(refresh_btns):
            btn.click(bind_refresh(i), [starts[i], ends[i], state], [previews[i]])

        for box in keeps:
            box.change(count_kept, [state, *keeps], [state, kept_md, confirm_btn, export_btn])

        confirm_outputs = [state, review_status, kept_md, export_btn, intro_title, end_takeaway]
        for i in range(MAX_KEEP):
            confirm_outputs.extend([pack_groups[i], pack_titles[i], pack_notes[i]])
        confirm_btn.click(
            confirm_selection,
            [state, *cids, *titles, *takeaways, *starts, *ends, *keeps],
            confirm_outputs,
        )
        export_btn.click(
            do_export,
            [state, cta_text, intro_title, intro_subtitle, intro_seconds, end_takeaway, end_seconds, aspect_ratio, *cids, *titles, *takeaways, *starts, *ends, *keeps],
            [state, export_status, compilation_video, post_view, zip_file],
        )
        demo.load(ping_stack, [model], [model, stack_status])
    return demo


if __name__ == "__main__":
    build_app().queue().launch()
