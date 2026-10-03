from __future__ import annotations

import copy
import json
import math
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import gradio as gr

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.assemble import assemble_compilation
from pipeline.cut import cut_clip, make_preview
from pipeline.assess import DIMENSIONS, GATES, RUBRIC_VERSION, artifact_hashes, assess_export, assessment_markdown, export_context, score_review
from pipeline.contracts import selection_revision, validate_range
from pipeline.export import DEFAULT_CTA, export_clips
from pipeline.editorial import EDITORIAL_VERSION, infer_lecture_context
from pipeline.nominate import list_ollama_models, nominate_candidates
from pipeline.rank import rank_candidates
from pipeline.transcribe import transcribe_video
from pipeline.storyboard import (TRANSITION_MODES, draft_storyboard, generate_storyboard,
                                storyboard_revision, validate_compilation_duration, validate_storyboard)
from pipeline.utils import (
    EXPORT_ROOT,
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
AUDIENCE_GOALS = {
    "General audience": [
        "Explain the lecture's key ideas in accessible language without assumed specialist knowledge.",
        "Use concrete examples that show why the ideas matter in everyday life.",
        "Present self-contained insights while preserving important context and caveats.",
    ],
    "Beginners": [
        "Introduce foundational concepts and explain unfamiliar terms.",
        "Use simple examples with enough setup for someone new to the subject.",
        "Explain one idea at a time without assuming prior subject knowledge.",
    ],
    "Students": [
        "Explain central concepts and the reasoning behind them.",
        "Use worked examples that connect theory to application.",
        "Clarify common misconceptions and important distinctions for study.",
    ],
    "Professionals": [
        "Explain practical applications of the lecture's ideas in professional work.",
        "Highlight actionable methods, decisions and real-world examples.",
        "Preserve implementation trade-offs, constraints and limitations.",
    ],
    "Advanced learners": [
        "Explain deeper mechanisms and nuanced arguments beyond introductory definitions.",
        "Examine evidence, assumptions, limitations and competing interpretations.",
        "Connect advanced concepts through detailed examples and analysis.",
    ],
}
DEFAULT_AUDIENCE = "General audience"
AUDIENCE_PROFILE_VERSION = "1.0"


def empty_state() -> dict:
    return {
        "run_id": None,
        "work": None,
        "source_video": None,
        "source_duration": None,
        "topic": "",
        "objectives": [],
        "target_audience": DEFAULT_AUDIENCE,
        "audience_profile_version": AUDIENCE_PROFILE_VERSION,
        "lecture_context": None,
        "source_url": "",
        "min_s": 20.0,
        "max_s": 45.0,
        "aspect_ratio": "Original",
        "model": "qwen2.5",
        "transcript": None,
        "candidates": [],
        "approved": [],
        "confirmed": False,
        "approval_revision": None,
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
        candidate_score_summary(cand),
        float(cand.get("start", 0)),
        float(cand.get("end", 0)),
        False,
    )


def candidate_score_summary(candidate):
    if not candidate.get("editorial_version"):
        return f"Legacy similarity: {candidate.get('score', 0):.3f} | {candidate.get('reason', '')}"
    score = candidate.get("score")
    heading = "Not scored" if score is None else f"Editorial estimate: {score:.1f}/100"
    lines = [f"{heading} | {candidate.get('editorial_status')}", candidate.get("editorial_reason", "")]
    for key, rating in candidate.get("editorial_ratings", {}).items():
        evidence = rating["evidence"]
        lines.append(f"{key}: {rating['score']}/4 - {rating['reason']} "
                     f"[{evidence['start']:.3f}-{evidence['end']:.3f}s] {evidence['quote']}")
    original = candidate.get("original_range")
    if original and (original["start"], original["end"]) != (candidate["start"], candidate["end"]):
        lines.append(f"Boundary extended from {original['start']:.3f}-{original['end']:.3f}s.")
    return "\n".join(lines)


def invalidate_range_score():
    return "Range changed. Editorial score and evidence unavailable for the edited excerpt; re-nominate to rescore."


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
            "source_duration": media_duration(stored),
            "topic": topic.strip(),
            "objectives": objectives,
            "source_url": (source_url or "").strip(),
            "min_s": float(min_s),
            "max_s": float(max_s),
            "aspect_ratio": aspect_ratio or "Original",
            "model": model or "qwen2.5",
            "transcript": transcript,
            "lecture_context": None,
            "candidates": [],
            "approved": [],
            "confirmed": False,
            "approval_revision": None,
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
        f"**Kept: 0 / {MIN_KEEP}–{MAX_KEEP} required**",
        gr.update(interactive=False),
        gr.update(interactive=False),
        *(value for _ in range(MAX_REVIEW) for value in _blank_row()),
    )


def audience_settings(target_audience):
    if target_audience not in AUDIENCE_GOALS:
        raise gr.Error("Select a target audience from the list.")
    return f"Lecture highlights for {target_audience.lower()}", list(AUDIENCE_GOALS[target_audience])


def transcribe_for_audience(video, target_audience, source_url, min_s, max_s, aspect_ratio, model, state, progress=gr.Progress()):
    topic, objectives = audience_settings(target_audience)
    result = transcribe(video, topic, "\n".join(objectives), source_url, min_s, max_s, aspect_ratio, model, state, progress=progress)
    result[0]["target_audience"] = target_audience
    return result


def change_target_audience(target_audience, state):
    topic, objectives = audience_settings(target_audience)
    state = dict(state or empty_state())
    state.update(target_audience=target_audience, topic=topic, objectives=objectives,
                 candidates=[], approved=[], confirmed=False, approval_revision=None)
    status = "Audience changed. Nominate clips again." if state.get("transcript") else "Run transcription first."
    return (state, status, [], f"**Kept: 0 / {MIN_KEEP}–{MAX_KEEP} required**",
            gr.update(interactive=False), gr.update(interactive=False),
            *(value for _ in range(MAX_REVIEW) for value in _blank_row()))


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
    selected_model = model or state.get("model") or "qwen2.5"
    audience = state.get("target_audience")
    if audience and (not state.get("lecture_context") or state["lecture_context"].get("status") == "unavailable"
                     or state["lecture_context"].get("model") != selected_model):
        progress(0.05, desc="Extracting source-grounded lecture concepts")
        state["lecture_context"] = infer_lecture_context(selected_model, segments)
    context = state.get("lecture_context") or {}
    concepts = context.get("concepts", [])
    if concepts:
        state["topic"] = "; ".join(item["label"] for item in concepts[:3])
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
        target_audience=audience,
        lecture_context=context,
    )
    progress(0.55, desc="Ranking against selection goals")
    ranked = rank_candidates(candidates, state["objectives"], segments, target_audience=audience)
    progress(0.7, desc="Building previews")
    work = Path(state["work"])
    for cand in ranked:
        preview = work / "previews" / f"{cand['id']}.mp4"
        make_preview(state["source_video"], cand["start"], cand["end"], preview)
        cand["preview_path"] = str(preview)

    state["candidates"] = ranked
    state["approved"] = []
    state["confirmed"] = False
    state["approval_revision"] = None
    state["model"] = model or state.get("model")
    table = [
        [
            c["id"],
            c.get("score"),
            c.get("editorial_status", "legacy_similarity"),
            c["start"],
            c["end"],
            c["title"],
            c["takeaway"],
        ]
        for c in ranked
    ]
    rejected = sum(candidate.get("editorial_status") == "rejected" for candidate in candidates)
    status = (f"Nominated {len(candidates)} · excluded {rejected} incomplete/context-risk clips · "
              f"showing {len(ranked)} after overlap filtering. Editorial scores are model estimates, not publication approval.")
    if len(ranked) == 1:
        speech_seconds = float(ranked[0]["end"]) - float(ranked[0]["start"])
        status += " One candidate is enough to proceed after review; no between-clip transitions are needed."
        if speech_seconds + 3 + 6 < 45:
            status += (f" This excerpt is {speech_seconds:.1f}s; a 3s opening and even a 6s ending cannot reach the 45s minimum. "
                       "Increase Max clip seconds and rerun nomination, or extend to a complete source explanation "
                       "within the selected limits. Do not pad or duplicate speech to meet the target.")
    if len(ranked) < MIN_KEEP:
        status += f" Fewer than {MIN_KEEP} usable clips: adjust duration limits and rerun; do not force incomplete cuts."
        reasons = [f"{candidate['id']}: {candidate.get('editorial_reason', 'Review required')}"
                   for candidate in candidates if candidate.get("editorial_status") == "rejected"]
        if reasons:
            status += "\n\nExcluded candidates:\n" + "\n".join(f"- {reason}" for reason in reasons)
    if context.get("status") in {"partial", "unavailable"}:
        status += f" Lecture concept extraction: {context['status']}."
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
        f"**Kept: 0 / {MIN_KEEP}–{MAX_KEEP} required**",
        gr.update(interactive=False),
        gr.update(interactive=False),
        *rows,
    )


def nominate_for_audience(state, model, target_audience, min_s, max_s, progress=gr.Progress()):
    topic, goals = audience_settings(target_audience)
    minimum, maximum = float(min_s), float(max_s)
    if not all(math.isfinite(value) for value in (minimum, maximum)) or not 0 < minimum <= maximum:
        raise gr.Error("Clip duration limits must be finite, positive and ordered.")
    state = dict(state or empty_state())
    state.update(target_audience=target_audience, audience_profile_version=AUDIENCE_PROFILE_VERSION,
                 objectives=goals, topic=topic, min_s=minimum, max_s=maximum)
    return nominate_and_rank(state, model, progress=progress)


def change_duration_limits(min_s, max_s, target_audience, state):
    result = change_target_audience(target_audience, state)
    result[0].update(min_s=float(min_s), max_s=float(max_s))
    return (result[0], "Duration changed. Nominate clips again.", *result[2:])


def count_kept(state, *keeps):
    state = dict(state or empty_state())
    n = sum(1 for keep in keeps if keep)
    state["confirmed"] = False
    state["approval_revision"] = None
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
        if cid not in lookup:
            raise gr.Error("Unknown candidate. Nominate clips again before confirming.")
        try:
            start_f, end_f = float(start), float(end)
            validate_range(start_f, end_f, float(state["source_duration"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise gr.Error(f"{cid}: invalid clip range ({exc}).") from exc
        base = dict(lookup[cid])
        if base.get("editorial_version"):
            if not float(state["min_s"]) <= round(end_f - start_f, 3) <= float(state["max_s"]):
                raise gr.Error(f"{cid}: edited clip must remain within the selected duration limits.")
        if (round(start_f, 3), round(end_f, 3)) != (base["start"], base["end"]):
            base.pop("score", None)
            for key in ("editorial_ratings", "context_preserved", "standalone"):
                base.pop(key, None)
            if base.get("editorial_version"):
                base.update(editorial_status="range_edited", editorial_reason="Edited range requires a new editorial review.")
            base["score_status"] = "Range edited; relevance score requires recalculation."
            base["quote"] = ""
            base["metadata_status"] = "range_edited"
            base["reason"] = "Range edited; review title, takeaway and context against the new excerpt."
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
        raise gr.Error(f"Keep {MIN_KEEP}–{MAX_KEEP} on-topic clips before confirming.")
    state["approved"] = kept
    state["confirmed"] = True
    state["approval_revision"] = selection_revision(state, kept)
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
    storyboard=None,
    storyboard_approval=None,
):
    state = dict(state or empty_state())
    n = MAX_REVIEW
    cids = list(row_values[0:n])
    titles = list(row_values[n : 2 * n])
    takeaways = list(row_values[2 * n : 3 * n])
    starts = list(row_values[3 * n : 4 * n])
    ends = list(row_values[4 * n : 5 * n])
    keeps = list(row_values[5 * n : 6 * n])
    if not state.get("confirmed"):
        raise gr.Error(f"Confirm {MIN_KEEP}–{MAX_KEEP} clips on the Review tab first.")
    kept = _read_rows(cids, titles, takeaways, starts, ends, keeps, state)
    if state.get("approval_revision") != selection_revision(state, kept):
        raise gr.Error("Selection changed. Confirm the current clips on the Review tab again.")
    clips = [dict(c) for c in state.get("approved") or []]
    if selection_revision(state, clips) != state.get("approval_revision"):
        raise gr.Error("Approved selection changed. Confirm the current clips again.")
    if not (MIN_KEEP <= len(clips) <= MAX_KEEP):
        raise gr.Error(f"Keep {MIN_KEEP}–{MAX_KEEP} on-topic clips before export.")

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
        storyboard=storyboard,
        storyboard_approval=storyboard_approval,
        selection_context={
            "target_audience": state.get("target_audience"),
            "audience_profile_version": state.get("audience_profile_version"),
            "audience_goals": state.get("objectives", []),
            "lecture_context": state.get("lecture_context"),
            "min_seconds": state.get("min_s"),
            "max_seconds": state.get("max_s"),
            "editorial_version": EDITORIAL_VERSION,
        },
    )
    status = f"Exported compilation to `{compilation}`."
    note = (
        "This file is the assembled Demo/YouTube cut: intro + selected clips + takeaway/CTA. "
        "Captions are burned in. Put the source URL in the description; the on-screen CTA has no clickable link."
    )
    return state, status, compilation, f"{posts}\n\n{note}", str(zip_path)


def _confirmed_storyboard_selection(state):
    clips = state.get("approved") or []
    if not state.get("confirmed") or not MIN_KEEP <= len(clips) <= MAX_KEEP:
        raise gr.Error(f"Confirm {MIN_KEEP}-{MAX_KEEP} clips on the Review tab first.")
    revision = selection_revision(state, clips)
    if state.get("approval_revision") != revision:
        raise gr.Error("Selection changed. Confirm the clips again.")
    return clips, revision


def invalidate_package_storyboard(state):
    state = dict(state or empty_state())
    state["storyboard_approval"] = None
    state["storyboard_preview_revision"] = None
    return state, "Storyboard changed. Preview and approve again.", None, False, gr.update(interactive=False)


def storyboard_control_updates(state):
    state = state or {}
    try:
        _, revision = _confirmed_storyboard_selection(state)
    except gr.Error:
        return gr.update(interactive=False), gr.update(interactive=False)
    has_draft = bool(state.get("storyboard_draft")) and state.get("storyboard_selection_revision") == revision
    has_preview = has_draft and bool(state.get("storyboard_preview_revision"))
    return gr.update(interactive=has_draft), gr.update(interactive=has_preview)


def revoke_storyboard_approval(state):
    state = dict(state or empty_state())
    state["storyboard_approval"] = None
    return state, "Storyboard approval required.", gr.update(interactive=False)


def draft_package_storyboard(state, aspect_ratio, *, generated=None):
    state = dict(state or empty_state())
    clips, revision = _confirmed_storyboard_selection(state)
    draft = copy.deepcopy(generated) if generated is not None else draft_storyboard(
        clips, state.get("target_audience", DEFAULT_AUDIENCE), state.get("source_url") or "")
    draft["aspect_ratio"] = aspect_ratio
    state.update(storyboard_draft=draft, storyboard_selection_revision=revision,
                 storyboard_approval=None, storyboard_preview_revision=None)
    opening, ending = draft["opening"], draft["ending"]
    joins = draft["transitions"] + [{"mode": "Cut", "label": ""}] * (MAX_KEEP - len(clips))
    values = [opening["title"], opening["subtitle"], opening["seconds"], ending["takeaway"],
              ending["question"], ending["credit"], ending["cta"], ending["seconds"], 1.5,
              *[join["mode"] for join in joins], *[join["label"] for join in joins], draft["overlay_position"]]
    sections = [("Opening", draft["opening"]), ("Ending", draft["ending"]),
                *[(f"Join {index + 1}", join) for index, join in enumerate(draft["transitions"])]]
    evidence = [[item[key] for key in ("clip_id", "start", "end", "quote")] +
                [", ".join(name for name, section in sections if item["clip_id"] in section["evidence_ids"])]
                for item in draft["evidence"]]
    return (state, "Draft ready. Claims and transition relationships need source review.", None, False,
            gr.update(interactive=False), evidence, *values,
            *[gr.update(visible=index < len(clips) - 1) for index in range(MAX_KEEP - 1)])


def generate_package_storyboard(state, aspect_ratio, model, progress=gr.Progress()):
    state = dict(state or empty_state())
    state.update(storyboard_draft=None, storyboard_selection_revision=None,
                 storyboard_approval=None, storyboard_preview_revision=None)
    unchanged = [gr.skip() for _ in range(11 + 3 * (MAX_KEEP - 1))]
    try:
        clips, _ = _confirmed_storyboard_selection(state)
    except gr.Error as exc:
        yield state, f"Storyboard not generated: {exc}", None, False, gr.update(interactive=False), *unchanged
        return
    yield state, "Generating storyboard from all selected excerpts...", None, False, gr.update(interactive=False), *unchanged
    try:
        progress(0.05, desc="Generating storyboard with Ollama")
        draft = generate_storyboard(model or state.get("model") or "qwen2.5", clips,
                                    state.get("target_audience", DEFAULT_AUDIENCE), state.get("source_url") or "")
        result = draft_package_storyboard(state, aspect_ratio, generated=draft)
        state = result[0]
        yield (state, "Generated draft ready. Rendering preview...", *result[2:])
    except Exception as exc:
        yield state, f"Storyboard generation failed: {exc}. Correct the issue, then confirm the selection again to retry.", None, False, gr.update(interactive=False), *unchanged
        return
    values = [*result[6:-(MAX_KEEP - 1)], aspect_ratio]
    try:
        preview = preview_package_storyboard(state, *values, progress=progress)
        yield (*preview, *result[5:])
    except Exception as exc:
        yield (state, f"Storyboard generated, but preview failed: {exc}. Correct the issue and use Preview storyboard.",
               None, False, gr.update(interactive=False), *result[5:])


def confirm_and_generate_storyboard(state, aspect_ratio, model, *row_values, progress=gr.Progress()):
    confirmed = confirm_selection(state, *row_values)
    for generated in generate_package_storyboard(confirmed[0], aspect_ratio, model, progress=progress):
        yield (*confirmed[1:3], *confirmed[6:], *generated)


def storyboard_duration_status(state, *values):
    clips = (state or {}).get("approved") or []
    if not clips:
        return "Compilation duration: no confirmed clips. Target: 45-120s."
    try:
        speech = sum(float(clip["end"]) - float(clip["start"]) for clip in clips)
        dividers = sum(values[9 + index] == "Divider" for index in range(len(clips) - 1)) * float(values[8])
        opening = (state.get("storyboard_draft") or {}).get("opening") or {"mode": "Card"}
        opening_seconds = float(values[2]) if opening.get("mode") == "Card" else 0
        total = opening_seconds + speech + dividers + float(values[7])
        if not math.isfinite(total):
            raise ValueError("Non-finite duration")
        label = "within target" if 45 <= total <= 120 else "outside target; adjust selection or timing"
        narration_note = " Opening may extend to finish title narration; measured duration is checked after rendering." if opening.get("narration") else ""
        return f"Estimated compilation: **{total:.1f}s / 45-120s** ({label}). Opening {opening_seconds:.1f}s + speech {speech:.1f}s + dividers {dividers:.1f}s + ending {float(values[7]):.1f}s.{narration_note}"
    except (TypeError, ValueError, IndexError):
        return "Compilation duration unavailable: check timing controls. Target: 45-120s."


def _read_storyboard(state, values):
    clips, revision = _confirmed_storyboard_selection(state)
    if not state.get("storyboard_draft") or state.get("storyboard_selection_revision") != revision:
        raise gr.Error("Draft the storyboard for the current selection first.")
    if len(values) != 11 + 2 * (MAX_KEEP - 1):
        raise gr.Error("Incomplete storyboard controls.")
    title, subtitle, opening_seconds, takeaway, question, credit, cta, ending_seconds, join_seconds = values[:9]
    draft = copy.deepcopy(state["storyboard_draft"])
    draft["aspect_ratio"] = values[-1]
    draft["overlay_position"] = values[-2]
    draft["source_url"] = state.get("source_url") or ""
    draft["opening"].update(title=(title or "").strip(), subtitle=(subtitle or "").strip(), seconds=float(opening_seconds))
    draft["ending"].update(takeaway=(takeaway or "").strip(), question=(question or "").strip(),
                           credit=(credit or "").strip(), cta=(cta or "").strip(), seconds=float(ending_seconds))
    for index, join in enumerate(draft["transitions"]):
        join.update(mode=values[9 + index], label=(values[9 + MAX_KEEP - 1 + index] or "").strip(),
                    seconds=float(join_seconds))
    try:
        validate_storyboard(draft, clips)
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc
    if draft.get("generation") and any(draft[key] != state["storyboard_draft"][key] for key in ("opening", "ending", "transitions")):
        draft["generation"]["status"] = "edited_model_draft"
    return draft, storyboard_revision(revision, draft)


def preview_package_storyboard(state, *values, progress=gr.Progress()):
    state = dict(state or empty_state())
    draft, revision = _read_storyboard(state, values)
    work = Path(state["work"]) / "storyboards" / revision / "preview"
    preview = work / "preview.mp4"
    if not preview.exists():
        raw_clips = []
        for index, clip in enumerate(state["approved"]):
            progress((index + 1) / (len(state["approved"]) + 2), desc="Preparing storyboard preview")
            raw_clips.append(cut_clip(state["source_video"], clip["start"], clip["end"],
                                      work / f"raw_{index}.mp4", copy=False))
        pending = work / "pending.mp4"
        assemble_compilation(raw_clips, pending, intro_title="", clips=state["approved"],
                             segments=state["transcript"]["segments"], storyboard=draft, preview=True,
                             aspect_ratio=draft["aspect_ratio"], work=work / "assemble", srt_path=work / "captions.srt")
        pending.replace(preview)
    if "duration_budget" in draft:
        validate_compilation_duration(media_duration(preview))
    state.update(storyboard_preview_revision=revision, storyboard_approval=None)
    return state, "Preview ready. Review the wording, clip joins, source evidence and text placement.", str(preview), False, gr.update(interactive=False)


def approve_package_storyboard(state, attested, reviewer, *values):
    state = dict(state or empty_state())
    draft, revision = _read_storyboard(state, values)
    if not attested or not str(reviewer or "").strip():
        raise gr.Error("A named human must review the preview and source evidence before approving.")
    if state.get("storyboard_preview_revision") != revision:
        raise gr.Error("Preview this storyboard revision before approving it.")
    state["storyboard_approval"] = {"revision": revision, "reviewer": reviewer.strip(), "kind": "human",
                                    "approved_at": datetime.now(timezone.utc).isoformat()}
    return state, "Storyboard approved for export. Final publication assessment is still required.", gr.update(interactive=True)


def export_package_storyboard(state, attested, reviewer, *values, progress=gr.Progress()):
    field_count = 11 + 2 * (MAX_KEEP - 1)
    draft, revision = _read_storyboard(state, values[:field_count])
    approval = state.get("storyboard_approval") or {}
    if not attested or approval.get("revision") != revision or approval.get("reviewer") != str(reviewer or "").strip():
        raise gr.Error("Storyboard changed or not approved. Preview and approve the current storyboard.")
    ending, opening = draft["ending"], draft["opening"]
    return do_export(state, ending["cta"], opening["title"], opening["subtitle"], opening["seconds"],
                     ending["takeaway"], ending["seconds"], draft["aspect_ratio"], *values[field_count:],
                     progress=progress, storyboard=draft, storyboard_approval=approval)


def _assessment_export_dir(export_key: str) -> Path:
    root = EXPORT_ROOT.resolve()
    destination = (root / (export_key or "")).resolve()
    if destination.parent.parent != root or not destination.is_dir():
        raise ValueError("Select a versioned export from the list.")
    return destination


def refresh_assessment_exports(video=None):
    exports = sorted(EXPORT_ROOT.glob("*/*/selection.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    choices = [path.parent.relative_to(EXPORT_ROOT).as_posix() for path in exports
               if all((path.parent / name).is_file() for name in ("compilation.mp4", "captions.srt", "post.txt"))]
    preferred = None
    if video:
        try:
            preferred = Path(as_video_path(video)).resolve().parent.relative_to(EXPORT_ROOT.resolve()).as_posix()
        except (TypeError, ValueError):
            pass
    return gr.update(choices=choices, value=preferred if preferred in choices else next(iter(choices), None))


def load_assessment_export(export_key):
    blank_fields = ["", *(["not_assessed"] * len(DIMENSIONS)), *([""] * len(DIMENSIONS)),
                    *(["not_assessed"] * len(GATES)), *([""] * len(GATES))]
    if not export_key:
        return None, None, "", "No export selected.", [], *blank_fields
    try:
        destination = _assessment_export_dir(export_key)
        hashes = artifact_hashes(destination)
        context = {"export_key": export_key, "artifact_sha256": hashes}
        context["selection_context"] = export_context(destination)
        audience_note = f"Target audience: {context['selection_context'].get('target_audience') or 'Unknown (legacy export)'}."
        summary = f"{audience_note}\n\nNot assessed."
        files = []
        saved = sorted(destination.glob("assessments/*/review.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        if saved:
            review = json.loads(saved[0].read_text(encoding="utf-8"))
            if review.get("artifact_sha256") == hashes:
                review["selection_context"] = context["selection_context"]
                summary = assessment_markdown(score_review(review))
                files = [str(saved[0].parent / name) for name in ("assessment.md", "assessment.json")
                         if (saved[0].parent / name).is_file()]
            else:
                summary = "Saved assessment is stale. Current export requires a new review."
        return (context, str(destination / "compilation.mp4"),
            audience_note + "\n\n" + (destination / "post.txt").read_text(encoding="utf-8"), summary, files, *blank_fields)
    except (OSError, ValueError) as exc:
        raise gr.Error(f"Could not load assessment: {exc}") from exc


def save_ui_assessment(context, export_key, reviewer_name, *fields):
    if not context:
        raise gr.Error("Select an exported compilation first.")
    if context.get("export_key") != export_key:
        raise gr.Error("Export changed. Wait for the selected compilation to load.")
    dimension_count = len(DIMENSIONS)
    gate_count = len(GATES)
    if len(fields) != 2 * dimension_count + 2 * gate_count:
        raise gr.Error("Incomplete assessment form.")
    try:
        ratings = {}
        for index, key in enumerate(DIMENSIONS):
            value = fields[index]
            if value not in {"not_assessed", "0", "1", "2", "3", "4"}:
                raise ValueError(f"{key}: invalid rating.")
            evidence = str(fields[dimension_count + index] or "").strip()
            ratings[key] = {"score": None if value == "not_assessed" else int(value),
                            "evidence": [evidence] if evidence else []}
        gates = {key: {"status": fields[2 * dimension_count + index],
                       "evidence": str(fields[2 * dimension_count + gate_count + index] or "").strip()}
                 for index, key in enumerate(GATES)}
        review = {"reviewer": {"kind": "human", "name": (reviewer_name or "").strip()},
                  "rubric_version": RUBRIC_VERSION, "selection_context": context["selection_context"],
                  "artifact_sha256": context["artifact_sha256"], "ratings": ratings, "gates": gates}
        destination = assess_export(_assessment_export_dir(context["export_key"]), review)
        return assessment_markdown(score_review(review)), [str(destination / name) for name in ("assessment.md", "assessment.json")]
    except (OSError, ValueError, KeyError) as exc:
        raise gr.Error(f"Assessment not saved: {exc}") from exc


def invalidate_ui_assessment():
    return "Review changed. Score pending recalculation.", []


def build_app():
    with gr.Blocks(title="Educational Short Clip Demo") as demo:
        state = gr.State(empty_state())
        gr.Markdown(
            "# Educational Short Clip Demo\n"
            "Transcribe a long lecture, let Ollama nominate clips, rank them against "
            f"audience needs, **human-keep {MIN_KEEP}–{MAX_KEEP} on-topic clips**, then package for social."
        )
        stack_status = gr.Markdown("Checking Ollama and ffmpeg...")

        with gr.Tabs():
            with gr.Tab("1. Input"):
                video = gr.Video(label="Source video (local mp4)", sources=["upload"])
                target_audience = gr.Dropdown(list(AUDIENCE_GOALS), value=DEFAULT_AUDIENCE, label="Target audience")
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
                    headers=["id", "editorial estimate / 100", "score status", "start", "end", "title", "takeaway"],
                    label="Ranked candidates (read-only)",
                    interactive=False,
                    wrap=True,
                )

            with gr.Tab("3. Review"):
                gr.Markdown(
                    "Listen to Top N. Keep a clip only if it is **on-topic**, standalone, "
                    "and one knowledge point. A weak opening is OK — adjust start/end instead of discarding."
                )
                kept_md = gr.Markdown(f"**Kept: 0 / {MIN_KEEP}–{MAX_KEEP} required**")
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
                                meta = gr.Textbox(label="Editorial score / evidence", lines=6, interactive=False)
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
                gr.Markdown("### Storyboard")
                storyboard_status = gr.Markdown("Confirm clips before drafting a storyboard.")
                storyboard_duration = gr.Markdown("Compilation duration: no confirmed clips. Target: 45-120s.")
                intro_title = gr.Textbox(label="Opening title / question (up to 10 words)")
                intro_subtitle = gr.Textbox(label="Opening subtitle (optional, up to 8 words)")
                overlay_position = gr.Radio(["Upper", "Middle"], value="Upper", label="Overlay position")
                with gr.Row():
                    intro_seconds = gr.Slider(2.0, 3.0, value=3.0, step=0.1, label="Opening minimum seconds")
                    end_seconds = gr.Slider(4.0, 6.0, value=5.0, step=0.5, label="Ending seconds")
                end_takeaway = gr.Textbox(label="Ending takeaway (up to 16 words)", lines=2)
                end_question = gr.Textbox(label="Reflection question (up to 12 words)")
                source_credit = gr.Textbox(label="Source credit", value="Source lecture")
                cta_text = gr.Textbox(value="Full lecture in description.", label="Ending CTA (up to 8 words)")
                transition_seconds = gr.Slider(1.0, 2.0, value=1.5, step=0.1, label="Transition label / divider seconds")
                join_groups, join_modes, join_labels = [], [], []
                for index in range(MAX_KEEP - 1):
                    with gr.Row(visible=False) as join_group:
                        join_modes.append(gr.Dropdown(TRANSITION_MODES, value="Cut", label=f"Join {index + 1} to {index + 2}"))
                        join_labels.append(gr.Textbox(label=f"Join {index + 1} label (up to 8 words)"))
                    join_groups.append(join_group)
                storyboard_evidence = gr.Dataframe(headers=["clip", "source start", "source end", "source excerpt", "referenced by"],
                                                  interactive=False, wrap=True, label="Storyboard source evidence")
                pack_groups, pack_titles, pack_notes = [], [], []
                for i in range(MAX_KEEP):
                    with gr.Group(visible=False) as pg:
                        pt = gr.Textbox(label=f"Clip {i + 1} title", interactive=False)
                        pn = gr.Textbox(label=f"Clip {i + 1} takeaway", interactive=False)
                    pack_groups.append(pg)
                    pack_titles.append(pt)
                    pack_notes.append(pn)
                preview_storyboard_btn = gr.Button("Preview storyboard", interactive=False)
                storyboard_preview = gr.Video(label="Storyboard preview", interactive=False)
                storyboard_reviewer = gr.Textbox(label="Storyboard reviewer name")
                storyboard_attested = gr.Checkbox(label="I reviewed the preview and source context; claims, joins and text placement are supported.")
                approve_storyboard_btn = gr.Button("Approve storyboard", variant="primary", interactive=False)
                export_btn = gr.Button("Export compilation", variant="primary", interactive=False)
                export_status = gr.Markdown(f"Confirm {MIN_KEEP}–{MAX_KEEP} clips before export.")
                compilation_video = gr.Video(label="compilation.mp4", interactive=False)
                post_view = gr.Textbox(label="Generated post.txt", lines=14)
                zip_file = gr.File(label="Download zip")

            with gr.Tab("5. Assessment"):
                assessment_context = gr.State(None)
                with gr.Row():
                    assessment_export = gr.Dropdown(choices=[], label="Export to assess", scale=5)
                    refresh_exports = gr.Button("Refresh exports", scale=1)
                with gr.Row():
                    with gr.Column(scale=3):
                        assessment_video = gr.Video(label="Final exported video", interactive=False)
                        assessment_post = gr.Textbox(label="Exported post", lines=5, interactive=False)
                    with gr.Column(scale=2):
                        assessment_summary = gr.Markdown("No export selected.", elem_id="assessment-summary")
                        assessment_files = gr.File(label="Assessment reports", file_count="multiple", interactive=False)
                with gr.Accordion("Rating anchors", open=False):
                    rubric = (ROOT / "docs" / "post-generation-rubric.md").read_text(encoding="utf-8")
                    gr.Markdown(rubric.split("## Rating Anchors", 1)[1].split("## Evidence and Calculation", 1)[0])
                reviewer_name = gr.Textbox(label="Reviewer name (new human assessment)")
                rating_inputs, rating_evidence = [], []
                score_choices = [("Not assessed", "not_assessed"), ("0 - Unacceptable", "0"),
                                 ("1 - Major issues", "1"), ("2 - Needs revision", "2"),
                                 ("3 - Acceptable", "3"), ("4 - Strong", "4")]
                for label, weight in DIMENSIONS.values():
                    with gr.Row():
                        rating_inputs.append(gr.Dropdown(score_choices, value="not_assessed", label=f"{label} ({weight}%)", scale=1))
                        rating_evidence.append(gr.Textbox(label=f"{label} evidence / timestamps", lines=2, scale=3))
                gate_inputs, gate_evidence = [], []
                for key in GATES:
                    label = key.replace("_", " ").capitalize()
                    with gr.Row():
                        gate_inputs.append(gr.Dropdown(
                            [("Not assessed", "not_assessed"), ("Pass", "pass"), ("Fail", "fail")],
                            value="not_assessed", label=label, scale=1,
                        ))
                        gate_evidence.append(gr.Textbox(label=f"{label} evidence", lines=2, scale=3))
                save_assessment = gr.Button("Calculate + save assessment", variant="primary")

        assessment_fields = [reviewer_name, *rating_inputs, *rating_evidence, *gate_inputs, *gate_evidence]
        storyboard_fields = [intro_title, intro_subtitle, intro_seconds, end_takeaway, end_question,
                             source_credit, cta_text, end_seconds, transition_seconds, *join_modes, *join_labels,
                             overlay_position, aspect_ratio]
        storyboard_reset_outputs = [state, storyboard_status, storyboard_preview, storyboard_attested, export_btn]
        storyboard_draft_outputs = [*storyboard_reset_outputs, storyboard_evidence, *storyboard_fields[:-1], *join_groups]
        state.change(storyboard_control_updates, [state], [preview_storyboard_btn, approve_storyboard_btn],
                 concurrency_id="audience_pipeline")
        for field in [state, *storyboard_fields]:
            field.change(storyboard_duration_status, [state, *storyboard_fields], [storyboard_duration],
                          concurrency_id="audience_pipeline")
        preview_storyboard_btn.click(preview_package_storyboard, [state, *storyboard_fields], storyboard_reset_outputs,
                                     concurrency_id="audience_pipeline")
        approve_storyboard_btn.click(approve_package_storyboard,
                                     [state, storyboard_attested, storyboard_reviewer, *storyboard_fields],
                                     [state, storyboard_status, export_btn], concurrency_id="audience_pipeline")
        for field in storyboard_fields:
            field.input(invalidate_package_storyboard, [state], storyboard_reset_outputs, concurrency_id="audience_pipeline")
        for field in (storyboard_reviewer, storyboard_attested):
            field.input(revoke_storyboard_approval, [state], [state, storyboard_status, export_btn],
                         concurrency_id="audience_pipeline")
        assessment_outputs = [assessment_summary, assessment_files]
        assessment_load_outputs = [assessment_context, assessment_video, assessment_post, *assessment_outputs, *assessment_fields]
        assessment_export.change(
            load_assessment_export, [assessment_export], assessment_load_outputs,
            concurrency_id="assessment_review",
        )
        refresh_exports.click(refresh_assessment_exports, [], [assessment_export]).success(
            load_assessment_export, [assessment_export], assessment_load_outputs,
            concurrency_id="assessment_review",
        )
        save_assessment.click(
            save_ui_assessment, [assessment_context, assessment_export, *assessment_fields], assessment_outputs,
            concurrency_id="assessment_review",
        )
        for field in assessment_fields:
            field.input(invalidate_ui_assessment, [], assessment_outputs, concurrency_id="assessment_review")

        review_outputs = []
        for index in range(MAX_REVIEW):
            review_outputs.extend(
                [groups[index], previews[index], cids[index], titles[index], takeaways[index], metas[index], starts[index], ends[index], keeps[index]]
            )
        transcribe_btn.click(
            transcribe_for_audience,
            [video, target_audience, source_url, min_s, max_s, aspect_ratio, model, state],
            [state, input_status, transcript_view, cand_status, cand_table, kept_md, confirm_btn, export_btn, *review_outputs],
            concurrency_id="audience_pipeline",
        ).success(invalidate_package_storyboard, [state], storyboard_reset_outputs, concurrency_id="audience_pipeline")
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
        nominate_btn.click(nominate_for_audience, [state, model, target_audience, min_s, max_s],
                   nominate_outputs, concurrency_id="audience_pipeline").success(
                       invalidate_package_storyboard, [state], storyboard_reset_outputs, concurrency_id="audience_pipeline")
        target_audience.input(change_target_audience, [target_audience, state], nominate_outputs,
                      concurrency_id="audience_pipeline")
        for duration_control in (min_s, max_s):
            duration_control.input(change_duration_limits, [min_s, max_s, target_audience, state],
                                   nominate_outputs, concurrency_id="audience_pipeline")

        def bind_refresh(index):
            def _run(start, end, st):
                return refresh_one_preview(index, start, end, st)

            return _run

        for i, btn in enumerate(refresh_btns):
            btn.click(bind_refresh(i), [starts[i], ends[i], state], [previews[i]])

        for box in keeps:
            box.change(count_kept, [state, *keeps], [state, kept_md, confirm_btn, export_btn], concurrency_id="audience_pipeline")

        for field in [*titles, *takeaways, *starts, *ends]:
            field.input(count_kept, [state, *keeps], [state, kept_md, confirm_btn, export_btn], concurrency_id="audience_pipeline")
        for index in range(MAX_REVIEW):
            for field in (starts[index], ends[index]):
                field.input(invalidate_range_score, [], [metas[index]])

        confirm_outputs = [review_status, kept_md]
        for i in range(MAX_KEEP):
            confirm_outputs.extend([pack_groups[i], pack_titles[i], pack_notes[i]])
        confirm_btn.click(
            confirm_and_generate_storyboard,
            [state, aspect_ratio, model, *cids, *titles, *takeaways, *starts, *ends, *keeps],
            [*confirm_outputs, *storyboard_draft_outputs],
            concurrency_id="audience_pipeline",
        )
        for field in [*titles, *takeaways, *starts, *ends, *keeps, target_audience, min_s, max_s, video]:
            field.input(invalidate_package_storyboard, [state], storyboard_reset_outputs, concurrency_id="audience_pipeline")
        export_btn.click(
            export_package_storyboard,
            [state, storyboard_attested, storyboard_reviewer, *storyboard_fields, *cids, *titles, *takeaways, *starts, *ends, *keeps],
            [state, export_status, compilation_video, post_view, zip_file],
            concurrency_id="audience_pipeline",
        ).success(refresh_assessment_exports, [compilation_video], [assessment_export])
        demo.load(ping_stack, [model], [model, stack_status])
        demo.load(refresh_assessment_exports, [], [assessment_export])
    return demo


if __name__ == "__main__":
    build_app().queue().launch()
