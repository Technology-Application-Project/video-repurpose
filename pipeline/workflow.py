import copy
import json
from pathlib import Path

from pipeline.project import file_hash, save_checkpoint


def controls(state):
    import app
    draft = state["storyboard_draft"]
    opening, ending = draft["opening"], draft["ending"]
    joins = draft["transitions"] + [{"mode": "Cut", "label": ""}] * (app.MAX_KEEP - len(state["approved"]))
    return [opening["title"], opening["subtitle"], opening["seconds"], ending["takeaway"],
            ending["question"], ending["credit"], ending["cta"], ending["seconds"],
            draft["transitions"][0]["seconds"] if draft["transitions"] else 1.5,
            *[join["mode"] for join in joins], *[join["label"] for join in joins],
            draft["overlay_position"], draft["aspect_ratio"]]


def rows(clips):
    import app
    values = []
    for field, empty in (("id", ""), ("title", ""), ("takeaway", ""), ("start", 0), ("end", 0), ("keep", False)):
        values.extend([True if field == "keep" else clip.get(field, empty) for clip in clips])
        values.extend([empty] * (app.MAX_REVIEW - len(clips)))
    return values


def analyze(source, options, progress):
    import app
    state = app.transcribe_for_audience(source, options["audience"], options.get("source_url", ""),
        options["min_seconds"], options["max_seconds"], options["aspect"], options["model"], None,
        options["whisper_model"], progress=progress)[0]
    state["source_sha256"] = file_hash(state["source_video"])
    Path(state["work"]).joinpath("previews").mkdir(exist_ok=True)
    state = app.nominate_for_audience(state, options["model"], options["audience"], options["min_seconds"],
                                     options["max_seconds"], options["automatic"], progress=progress)[0]
    save_checkpoint(state)
    return state


def select(state, clips):
    import app
    state = copy.deepcopy(state)
    if not 1 <= len(clips) <= app.MAX_KEEP or len({clip["id"] for clip in clips}) != len(clips):
        raise ValueError("Select one to five distinct clips.")
    clips = sorted(clips, key=lambda clip: clip["start"])
    changed = [{key: clip.get(key) for key in ("id", "start", "end", "title", "takeaway")} for clip in clips] != [
        {key: clip.get(key) for key in ("id", "start", "end", "title", "takeaway")} for clip in state.get("approved", [])]
    if changed:
        state["selection"] = None
        state.update(storyboard_draft=None, storyboard_approval=None, storyboard_preview_path=None,
                     storyboard_preview_revision=None, storyboard_selection_revision=None, blueprint=None)
    state = app.confirm_selection(state, *rows(clips))[0]
    save_checkpoint(state)
    return state


def generate(state, progress, publish=None):
    import app
    state = select(state, state.get("approved", []))
    published = None
    for update in app.generate_package_storyboard(state, state["aspect_ratio"], state["model"], progress=progress):
        state = update[0]
        blueprint = state.get("blueprint")
        if publish and blueprint and blueprint["revision"] != published:
            published = blueprint["revision"]
            publish(state)
    save_checkpoint(state)
    if state.get("storyboard_error") or not state.get("storyboard_preview_path"):
        raise ValueError(state.get("storyboard_error") or "Preview generation did not complete.")
    return state


def approve(state, reviewer, attested):
    import app
    state = app.approve_package_storyboard(state, attested, reviewer, *controls(state))[0]
    save_checkpoint(state)
    return state


def export(state, progress):
    import app
    approval = state.get("storyboard_approval") or {}
    if approval.get("kind") != "human":
        raise ValueError("A named human must approve the current preview before export.")
    result = app.export_package_storyboard(state, True, approval["reviewer"], *controls(state),
                                           *rows(state["approved"]), progress=progress)
    return result[0], Path(result[2]).parent


def timeline(state):
    path = state.get("storyboard_preview_path")
    if path:
        manifest = Path(path).parent / "assemble" / "timeline.json"
        if manifest.is_file():
            return json.loads(manifest.read_text())
    return []