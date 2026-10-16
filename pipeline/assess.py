from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pipeline.utils import ffmpeg_bin, ffprobe_bin, run_cmd, save_json


RUBRIC_VERSION = "1.1"
DIMENSIONS = {
    "source_fidelity": ("Source fidelity", 25),
    "learning_value": ("Audience fit and learning value", 25),
    "standalone_coherence": ("Standalone coherence", 20),
    "captions": ("Captions", 15),
    "audio_visuals": ("Audio and visuals", 15),
}
GATES = (
    "claims_supported",
    "cuts_preserve_context",
    "captions_readable",
    "human_final_review",
)
ARTIFACTS = ("compilation.mp4", "captions.srt", "post.txt", "selection.json")
OBJECTIVE_VERSION = "2.2"


def _metric(key, label, passed, value, threshold):
    return {"id": key, "label": label, "status": "unavailable" if passed is None else "pass" if passed else "fail",
            "value": value, "threshold": threshold}


def _caption_metrics(text: str, duration: float, audience: str | None = None) -> tuple[list[dict], list]:
    import srt

    limit = 20 if audience in ("Researcher", "General public") else 17
    policy = audience if audience in ("Researcher", "General public", "Kids") else "Unknown audience; conservative fallback"
    try:
        cues = list(srt.parse(text))
    except (srt.SRTParseError, ValueError) as exc:
        return [_metric("caption_parse", "SRT parsing", False, str(exc), "Valid, nonempty SRT")], []
    metrics = [_metric("caption_parse", "SRT parsing", bool(cues), len(cues), "At least one cue")]
    if not cues:
        return metrics, cues
    invalid = []
    speeds = []
    previous_end = 0.0
    for index, cue in enumerate(cues, 1):
        start, end = cue.start.total_seconds(), cue.end.total_seconds()
        if start < 0 or end <= start or end > duration + 0.1 or start < previous_end - 0.05 or not cue.content.strip():
            invalid.append(index)
        previous_end = end
        if end > start:
            speeds.append((len(" ".join(cue.content.split())) / (end - start), start, " ".join(cue.content.split())))
    metrics.append(_metric("caption_timing", "Sidecar cue timing", not invalid,
                          {"invalid_cues": invalid}, "Positive, ordered cues within video; overlap tolerance 0.05s"))
    fastest = max(speeds, default=(0.0, 0.0, ""))
    maximum = fastest[0]
    metrics.append(_metric("caption_speed", "Sidecar reading speed", maximum <= limit,
                          {"max_characters_per_second": round(maximum, 2),
                           "limit_characters_per_second": limit, "audience_policy": policy,
                           "cues_above_limit": sum(speed[0] > limit for speed in speeds),
                           "fastest_cue_start_seconds": round(fastest[1], 2), "fastest_cue_text": fastest[2]},
                          f"Maximum {limit} characters/second ({policy}; English caption policy)"))
    return metrics, cues


def _timeline_metrics(selection: dict, duration: float) -> list[dict]:
    timeline = selection.get("timeline")
    if not timeline:
        return [_metric("timeline", "Timeline metadata consistency", None, "No exported timeline", "Contiguous timeline matching clips and video")]
    if not isinstance(timeline, list):
        return [_metric("timeline", "Timeline metadata consistency", False, "Timeline is not a list", "Timeline list required")]
    valid = True
    cursor = 0.0
    clips = []
    try:
        for item in timeline:
            start, seconds = float(item["start"]), float(item["duration"])
            valid &= math.isfinite(start) and math.isfinite(seconds) and start >= 0 and seconds > 0 and abs(start - cursor) <= 0.1
            valid &= item["kind"] in {"opening", "clip", "divider", "ending"}
            cursor = start + seconds
            if item["kind"] == "clip":
                clips.append(item)
        expected = selection["clips"]
        valid &= 1 <= len(expected) <= 5
        valid &= [clip["clip_id"] for clip in clips] == [clip["id"] for clip in expected]
        valid &= all(abs(float(source["end"]) - float(source["start"]) - float(clip["duration"])) <= 0.15
                     for source, clip in zip(expected, clips))
        valid &= abs(cursor - duration) <= 0.2
    except (KeyError, TypeError, ValueError):
        valid = False
    return [_metric("timeline", "Timeline metadata consistency", bool(valid),
                    {"items": len(timeline), "clip_count": len(clips)},
                    "Contiguous within 0.1s; selected clip durations within 0.15s; total within 0.2s")]


def _narration_metrics(video: Path, selection: dict, cues: list) -> list[dict]:
    storyboard = selection.get("storyboard")
    if not storyboard:
        return [_metric("narration", "Narration verification", None, "No storyboard metadata", "Storyboard required")]
    opening, ending = storyboard.get("opening", {}), storyboard.get("ending", {})
    expected = []
    if opening.get("mode") == "Card" and opening.get("narration"):
        expected.append(("opening", opening.get("title", "")))
    expected.extend(("divider", join.get("label", "")) for join in storyboard.get("transitions", [])
                    if join.get("mode") == "Divider" and join.get("narration"))
    if ending.get("narration"):
        expected.append(("ending", " ".join(text if text.endswith((".", "?", "!")) else f"{text}."
                                           for text in (ending.get("takeaway", ""), ending.get("cta", "")))))
    cards = [item for item in selection.get("timeline", []) if item.get("narration")]
    if not expected and not cards:
        return [{"id": "narration", "label": "Narration verification", "status": "not_applicable",
                 "value": "Narration disabled in exported storyboard", "threshold": "Only required for narrated cards"}]
    profile = storyboard.get("narration_profile") or {}
    valid = len(cards) == len(expected)
    captions_match = len(cards) == len(expected)
    try:
        for card, (kind, text) in zip(cards, expected):
            speech = card["narration"]
            seconds = float(speech["seconds"])
            valid &= card["kind"] == kind and speech["text"] == text
            valid &= speech["voice"] == profile.get("voice") and speech["rate"] == profile.get("rate")
            valid &= math.isfinite(seconds) and seconds > 0 and seconds + 0.15 <= float(card["duration"])
            captions_match &= any(" ".join(cue.content.split()) == " ".join(text.split())
                                  and abs(cue.start.total_seconds() - card["start"] - 0.15) <= 0.05
                                  and abs(cue.end.total_seconds() - card["start"] - 0.15 - seconds) <= 0.1 for cue in cues)
    except (KeyError, TypeError, ValueError):
        valid = captions_match = False
    metrics = [_metric("narration_schedule", "Narration schedule and profile metadata", bool(valid), len(cards),
                       "Every enabled card has expected text, saved voice/rate and enough hold time"),
               _metric("narration_captions", "Narration sidecar alignment", bool(captions_match), len(expected),
                       "Text matches; start within 0.05s and end within 0.1s of recorded narration")]
    if not valid:
        metrics.append(_metric("narration_audio", "Audible narration intervals", None, "Invalid narration schedule", "Peak above -60 dBFS in every narrated interval"))
        return metrics
    try:
        peaks = []
        for card in cards:
            source, offset = video, card["start"] + 0.15
            if storyboard.get("media_assets"):
                from pipeline.project import file_hash
                asset = selection.get("asset_inventory", {}).get(f"narration_{card['narration']['cache_key']}")
                if not asset:
                    raise ValueError("Exported narration stem is missing; music cannot prove narration is audible.")
                source = (video.parent / asset["file"]).resolve()
                if not source.is_relative_to(video.parent.resolve()) or file_hash(source) != card["narration"]["sha256"]:
                    raise ValueError("Exported narration stem changed.")
                offset = 0
            result = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", str(offset), "-i", str(source),
                                     "-t", str(card["narration"]["seconds"]), "-map", "0:a:0", "-ac", "1", "-ar", "8000",
                                     "-f", "f32le", "-"], capture_output=True, check=True)
            samples = array.array("f")
            samples.frombytes(result.stdout)
            peak = max((abs(sample) for sample in samples), default=0.0)
            peaks.append({"kind": card["kind"], "start": card["start"], "peak_amplitude": round(peak, 6)})
        metrics.append(_metric("narration_audio", "Audible narration intervals",
                               all(item["peak_amplitude"] > 0.001 for item in peaks), peaks,
                               "Peak above -60 dBFS; generated-media exports use verified narration stems. Human listening must verify the final mix and spoken words."))
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        metrics.append(_metric("narration_audio", "Audible narration intervals", None, str(exc), "Decodable narration audio"))
    return metrics


def measure_export(export_dir: Path) -> dict:
    video = export_dir / "compilation.mp4"
    metrics = []
    try:
        selection = json.loads((export_dir / "selection.json").read_text(encoding="utf-8"))
        if not isinstance(selection, dict):
            raise ValueError("Selection metadata must be an object")
        metrics.append(_metric("metadata", "Export metadata", True, "Readable JSON object", "Readable selection.json"))
        if (selection.get("storyboard") or {}).get("media_assets"):
            from pipeline.project import file_hash
            inventory = selection.get("asset_inventory") or {}
            verified = bool(inventory)
            for asset in inventory.values():
                path = (export_dir / asset["file"]).resolve()
                verified &= path.is_relative_to(export_dir.resolve()) and path.is_file() and file_hash(path) == asset["sha256"]
            metrics.append(_metric("media_provenance", "Exported media hashes", verified, len(inventory),
                                   "All approved media and narration bytes match the exported inventory"))
    except (OSError, ValueError) as exc:
        selection = {}
        metrics.append(_metric("metadata", "Export metadata", False, str(exc), "Readable selection.json"))
    try:
        probe = json.loads(run_cmd([ffprobe_bin(), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)]).stdout)
        duration = float(probe["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid media duration")
        streams = probe["streams"]
        visual = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
        audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
        metrics.append(_metric("streams", "Video and audio streams", bool(visual and audio),
                               {"video": bool(visual), "audio": bool(audio)}, "Both streams present"))
        from pipeline.storyboard import LEGACY_DURATION_BUDGET, validate_compilation_duration
        budget = (selection.get("storyboard") or {}).get("duration_budget", LEGACY_DURATION_BUDGET)
        try:
            validate_compilation_duration(duration, budget)
            duration_valid = True
        except ValueError:
            duration_valid = False
        metrics.append(_metric("duration", "Compilation duration", duration_valid, round(duration, 3),
                               f"{budget.get('min_seconds')}-{budget.get('max_seconds')} seconds"))
        aspect = selection.get("aspect_ratio", "Original")
        expected_ratio = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1}.get(aspect)
        if visual and expected_ratio:
            ratio = float(visual["width"]) / float(visual["height"])
            metrics.append(_metric("aspect_ratio", "Output aspect ratio", abs(ratio - expected_ratio) <= 0.02,
                                   f"{visual['width']}x{visual['height']}", f"{aspect}; ratio tolerance 0.02"))
        else:
            metrics.append({"id": "aspect_ratio", "label": "Output aspect ratio", "status": "not_applicable" if visual else "unavailable",
                            "value": aspect, "threshold": "Explicit target aspect ratio required"})
        caption_metrics, cues = _caption_metrics((export_dir / "captions.srt").read_text(encoding="utf-8-sig"), duration,
                            (selection.get("selection_context") or {}).get("target_audience"))
        metrics.extend(caption_metrics)
        timeline_metrics = _timeline_metrics(selection, duration)
        metrics.extend(timeline_metrics)
        if timeline_metrics[0]["status"] == "pass":
            metrics.extend(_narration_metrics(video, selection, cues))
        else:
            metrics.append(_metric("narration", "Narration verification", None, "Timeline missing or invalid", "Valid timeline required"))
    except (OSError, RuntimeError, KeyError, TypeError, ValueError, AttributeError, ZeroDivisionError) as exc:
        metrics.append(_metric("media_analysis", "Media analysis", False, str(exc), "Readable media and sidecar files"))
    try:
        run_cmd([ffmpeg_bin(), "-v", "error", "-xerror", "-i", str(video), "-map", "0:v:0?", "-map", "0:a:0?", "-f", "null", "-"])
        metrics.append(_metric("decode", "Full media decode", True, "No decoder errors", "All video/audio packets decode"))
    except (OSError, RuntimeError) as exc:
        metrics.append(_metric("decode", "Full media decode", False, str(exc), "All video/audio packets decode"))
    try:
        result = run_cmd([ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(video), "-map", "0:a:0", "-vn",
                          "-af", "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
        levels, _ = json.JSONDecoder().raw_decode(result.stderr[result.stderr.rfind("{"):])
        loudness, peak = float(levels["input_i"]), float(levels["input_tp"])
        metrics.append(_metric("loudness", "Integrated loudness", math.isfinite(loudness) and -24 <= loudness <= -14,
                               loudness if math.isfinite(loudness) else "No measurable signal", "-24 to -14 LUFS (configured speech target)"))
        metrics.append(_metric("true_peak", "Audio true peak", math.isfinite(peak) and peak <= -1,
                               peak if math.isfinite(peak) else "No measurable signal", "At most -1 dBTP"))
    except (OSError, RuntimeError, KeyError, TypeError, ValueError) as exc:
        metrics.append(_metric("audio_levels", "Audio level measurement", None, str(exc), "Measurable audio stream"))
    counts = {status: sum(metric["status"] == status for metric in metrics) for status in ("pass", "fail", "unavailable", "not_applicable")}
    return {"assessment_type": "objective", "metric_version": OBJECTIVE_VERSION,
            "selection_context": selection.get("selection_context") or {}, "metrics": metrics, "counts": counts,
            "technical_status": "failed" if counts["fail"] else "incomplete" if counts["unavailable"] else "passed",
            "publication_status": "not_assessed", "limitations": [
                "Technical checks do not establish factual accuracy, source fidelity, educational value, or audience suitability.",
                "SRT checks do not verify transcription accuracy, burned-in text contrast, or visual placement.",
                "Audio presence does not verify spoken words or voice identity. Thresholds are configured targets, not universal quality standards."]}


def selection_label(context: dict) -> str:
    """How the clips were chosen; older exports did not record it."""
    selection = (context or {}).get("selection") or {}
    if selection.get("mode") == "automatic":
        return "Clip selection: automatic, no human review of the clip choice."
    if selection.get("mode") == "manual":
        return "Clip selection: chosen by a human reviewer."
    return "Clip selection: not recorded (older export)."


def _measurement_text(metric: dict) -> str:
    measured = metric["value"]
    if isinstance(measured, dict):
        return "; ".join(f"{key.replace('_', ' ')}: {json.dumps(item, ensure_ascii=False)}" for key, item in measured.items())
    if isinstance(measured, list) and all(isinstance(item, dict) and "peak_amplitude" in item for item in measured):
        return "; ".join(f"{item['kind']} at {item['start']:.2f}s: peak {item['peak_amplitude']:.4f}" for item in measured)
    return str(measured)


def objective_markdown(assessment: dict) -> str:
    counts = assessment["counts"]
    lines = ["# Automated Technical Assessment", "", f"Metrics version: {assessment['metric_version']}",
             "", f"Target audience: {assessment.get('selection_context', {}).get('target_audience') or 'Unknown'}", "",
             selection_label(assessment.get("selection_context")), "",
             f"Technical result: **{assessment['technical_status']}**. "
             f"{counts['pass']} passed; {counts['fail']} failed; {counts['unavailable']} unavailable; {counts['not_applicable']} not applicable.",
             "", "| Check | Result | Measurement | Threshold |", "|---|---|---|---|"]
    for metric in assessment["metrics"]:
        value = _measurement_text(metric).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {metric['label']} | {metric['status']} | {value} | {metric['threshold']} |")
    lines += ["", "## Scope", "", *assessment["limitations"], "", "Publication suitability: **not assessed**.",
              "This report applies only to the recorded artifact hashes."]
    return "\n".join(lines)


_STATUS_ORDER = {"fail": 0, "unavailable": 1, "pass": 2, "not_applicable": 3}
_STATUS_BADGE = {"fail": "FAIL", "unavailable": "UNAVAILABLE", "pass": "PASS", "not_applicable": "N/A"}
_STATUS_BANNER = {"failed": ("fail", "{fail} OF {total} CHECKS FAILED"),
                  "incomplete": ("unavailable", "INCOMPLETE: {unavailable} CHECKS COULD NOT RUN"),
                  "passed": ("pass", "ALL CHECKS PASSED")}
# check id -> (what it checks in plain words, what to do when it does not pass)
_GUIDE = {
    "metadata": ("The export's information file can be read.", "Export again; selection.json is missing or damaged."),
    "media_provenance": ("Every recorded media and voice file is exactly the one that was approved.",
                         "Export again; a media file changed or is missing."),
    "streams": ("The video has both picture and sound.", "Export again; the file lacks a picture or audio track."),
    "duration": ("The video length is inside the target range.", "Pick different clips or shorter cards, then export again."),
    "aspect_ratio": ("The picture shape matches the chosen format.", "Export again with the intended aspect ratio."),
    "caption_parse": ("The caption file (SRT) is valid and not empty.", "Export again; the caption file is unreadable."),
    "caption_timing": ("Captions appear in order and stay inside the video.", "Export again; caption timing is inconsistent."),
    "caption_speed": ("Caption text fits the audience-specific English reading-speed ceiling; readability still needs human review.",
                      "Review the flagged passage. Adjust cue grouping or timing without losing speech sync, or choose a slower clip. "
                      "Do not remove meaning or raise the limit just to pass."),
    "timeline": ("The export notes match the clips and the video length.", "Export again; the timeline notes disagree with the video."),
    "narration_schedule": ("Each narrated card has the right text, voice and enough time.", "Regenerate the storyboard and export again."),
    "narration_captions": ("Narration captions line up with the recorded voice.", "Regenerate the storyboard and export again."),
    "narration_audio": ("The narrator can actually be heard on each narrated card.", "Regenerate the storyboard; narration may be silent."),
    "narration": ("Narration could be checked.", "Fix the timeline problem above, then re-run."),
    "decode": ("The whole video plays through without errors.", "Export again; the file is damaged."),
    "loudness": ("Overall volume is in a comfortable range.", "Re-export; the audio mix is too quiet or too loud."),
    "true_peak": ("No sound peak is loud enough to distort.", "Re-export; the audio mix peaks too high."),
    "audio_levels": ("Audio volume could be measured.", "Check the video has a readable audio track."),
    "media_analysis": ("The video and caption files could be analysed.", "Export again; a file is unreadable."),
}


def _friendly_value(metric: dict) -> str:
    """One plain sentence for the measurement, falling back to the raw text."""
    key, value = metric["id"], metric["value"]
    try:
        if key == "duration":
            return f"{value:.1f} s"
        if key == "loudness":
            return f"{value:.1f} LUFS"
        if key == "true_peak":
            return f"{value:.1f} dBTP"
        if key == "caption_parse":
            return f"{value} captions"
        if key == "media_provenance":
            return f"{value} files verified"
        if key in {"narration_schedule", "narration_captions"}:
            return f"{value} narrated cards checked"
        if key == "caption_timing":
            return "All captions in order" if not value["invalid_cues"] else f"Problem captions: {value['invalid_cues']}"
        if key == "timeline":
            return f"{value['items']} sections, {value['clip_count']} clips"
        if key == "streams":
            return "Picture and sound present" if value["video"] and value["audio"] else f"Picture: {value['video']}; sound: {value['audio']}"
        if key == "caption_speed":
            text = (f"Fastest caption: {value['max_characters_per_second']:.2f} characters/second; "
                    f"{value['cues_above_limit']} caption(s) too fast")
            if value.get("fastest_cue_text") and value["cues_above_limit"] > 0:
                text += f'. Worst: "{value["fastest_cue_text"]}" at {value["fastest_cue_start_seconds"]:.1f} s'
            return text
        if key == "narration_audio" and isinstance(value, list):
            audible = sum(item["peak_amplitude"] > 0.001 for item in value)
            return f"{audible} of {len(value)} narrated cards audible"
    except (KeyError, TypeError, ValueError):
        pass
    return _measurement_text(metric)


def objective_html(assessment: dict) -> str:
    """Colour-coded summary for the app: overall banner, failures first, plain-language wording."""
    from html import escape

    counts = assessment["counts"]
    metrics = assessment["metrics"]
    tone, headline = _STATUS_BANNER.get(assessment["technical_status"], ("unavailable", "NOT ASSESSED"))
    headline = headline.format(total=len(metrics), **counts)
    chips = "".join(f'<span class="chip chip-{key}"><b>{counts[key]}</b> {label}</span>'
                    for key, label in (("pass", "passed"), ("fail", "failed"), ("unavailable", "unavailable"), ("not_applicable", "n/a"))
                    if counts[key] or key in {"pass", "fail"})
    attention = [metric["label"] for metric in metrics if metric["status"] in {"fail", "unavailable"}]
    note = (f'<div class="banner-note">Needs attention: {escape("; ".join(attention))}</div>' if attention else
            '<div class="banner-note">The file measures well. A person still has to watch it before it is published.</div>')
    context = assessment.get("selection_context") or {}
    audience = escape(str(context.get("target_audience") or "Unknown"))
    rows = []
    for metric in sorted(metrics, key=lambda item: _STATUS_ORDER.get(item["status"], 4)):
        status = metric["status"]
        what, fix = _GUIDE.get(metric["id"], ("", ""))
        what_html = f'<div class="cell-what">{escape(what)}</div>' if what else ""
        fix_html = f'<div class="cell-fix"><b>What to do:</b> {escape(fix)}</div>' if fix and status in {"fail", "unavailable"} else ""
        raw_html = (f'<details class="cell-raw"><summary>Technical detail</summary>{escape(_measurement_text(metric))}</details>'
                    if status != "pass" else "")
        rows.append(
            f'<tr class="row-{status}"><td class="cell-status"><span class="badge badge-{status}">'
            f'{_STATUS_BADGE.get(status, escape(status.upper()))}</span></td>'
            f'<td class="cell-check">{escape(metric["label"])}{what_html}</td>'
            f'<td>{escape(_friendly_value(metric))}{fix_html}{raw_html}</td>'
            f'<td class="cell-limit">{escape(str(metric["threshold"]))}</td></tr>')
    limits = "".join(f"<li>{escape(text)}</li>" for text in assessment.get("limitations", []))
    return (
        f'<div class="assess"><div class="banner banner-{tone}"><div class="banner-title">{escape(headline)}</div>'
        f'<div class="chips">{chips}</div>{note}</div>'
        f'<p class="assess-meta">Target audience: <b>{audience}</b>. {escape(selection_label(context))} '
        f'Metrics version {escape(str(assessment["metric_version"]))}.</p>'
        '<table class="assess-table"><thead><tr><th>Result</th><th>Check</th><th>What we found</th><th>Target</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        f'<details class="assess-scope"><summary>Scope and limits</summary><ul>{limits}</ul>'
        '<p>Publication suitability: <b>not assessed</b>. This report applies only to the recorded artifact hashes.</p></details></div>')


def assess_export_automatically(export_dir: Path, *, expected_hashes: dict | None = None, force: bool = False) -> Path:
    hashes = artifact_hashes(export_dir)
    if expected_hashes is not None and expected_hashes != hashes:
        raise ValueError("Export artifacts changed. Reload the export before assessing it.")
    if not force:
        for path in sorted(export_dir.glob("assessments/*/assessment.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if (cached.get("assessment_type") == "objective" and cached.get("metric_version") == OBJECTIVE_VERSION
                    and cached.get("artifact_sha256") == hashes and not cached.get("counts", {}).get("unavailable", 1)
                    and path.with_suffix(".md").is_file()):
                    return path.parent
            except (OSError, ValueError):
                continue
    assessment = measure_export(export_dir)
    if artifact_hashes(export_dir) != hashes:
        raise ValueError("Export artifacts changed during assessment. Reload and run again.")
    assessment.update(artifact_sha256=hashes, created_at=datetime.now(timezone.utc).isoformat())
    destination = export_dir / "assessments" / uuid.uuid4().hex[:12]
    destination.mkdir(parents=True, exist_ok=False)
    save_json(destination / "assessment.json", assessment)
    (destination / "assessment.md").write_text(objective_markdown(assessment), encoding="utf-8")
    return destination


def score_review(review: dict) -> dict:
    reviewer = review.get("reviewer") or {}
    if reviewer.get("kind") not in {"human", "assistant"} or not str(reviewer.get("name") or "").strip():
        raise ValueError("A named human or assistant reviewer is required.")
    ratings = review.get("ratings") or {}
    gates = review.get("gates") or {}
    if set(ratings) - set(DIMENSIONS) or set(gates) - set(GATES):
        raise ValueError("Unknown rubric dimension or publication gate.")
    dimensions = []
    earned_points = 0.0
    assessed_weight = 0
    blockers = []
    for key, (label, weight) in DIMENSIONS.items():
        if key == "learning_value" and review.get("rubric_version", "1.0") == "1.0":
            label = "Learning value"
        rating = ratings.get(key) or {}
        score = rating.get("score")
        evidence = rating.get("evidence") or []
        if not isinstance(evidence, list) or any(not isinstance(item, str) or not item.strip() for item in evidence):
            raise ValueError(f"{key}: evidence must be a list of nonempty references or observations.")
        if score is not None and (type(score) is not int or not 0 <= score <= 4):
            raise ValueError(f"{key}: score must be an integer from 0 to 4, or null.")
        if score is not None and not evidence:
            raise ValueError(f"{key}: a score requires evidence.")
        points = None if score is None else weight * score / 4
        if points is None:
            blockers.append(f"{label}: not assessed")
        else:
            earned_points += points
            assessed_weight += weight
            if score < 3:
                blockers.append(f"{label}: revision required ({score}/4)")
        dimensions.append({"id": key, "label": label, "weight": weight, "score": score,
                           "weighted_points": points, "evidence": evidence})
    gate_results = {}
    for key in GATES:
        gate = gates.get(key) or {}
        status = gate.get("status", "not_assessed")
        evidence = gate.get("evidence") or ""
        if status not in {"pass", "fail", "not_assessed"}:
            raise ValueError(f"{key}: invalid gate status.")
        if not isinstance(evidence, str) or (status != "not_assessed" and not evidence.strip()):
            raise ValueError(f"{key}: an assessed gate requires evidence.")
        if key == "human_final_review" and status == "pass" and reviewer["kind"] != "human":
            raise ValueError("An assistant cannot attest human final review.")
        gate_results[key] = {"status": status, "evidence": evidence}
        if status != "pass":
            blockers.append(f"{key}: {status}")
    complete = assessed_weight == 100
    return {
        "rubric_version": review.get("rubric_version", "1.0"),
        "selection_context": review.get("selection_context") or {},
        "reviewer": reviewer,
        "dimensions": dimensions,
        "earned_points": earned_points,
        "assessed_weight_percent": assessed_weight,
        "overall_score": earned_points if complete else None,
        "assessment_status": "complete" if complete else "partial",
        "publication_status": "approved" if not blockers else "blocked",
        "publication_blockers": blockers,
        "gates": gate_results,
    }


def artifact_hashes(export_dir: Path) -> dict[str, str]:
    hashes = {}
    names = list(ARTIFACTS)
    try:
        selection = json.loads((export_dir / "selection.json").read_text(encoding="utf-8"))
        for asset in selection.get("asset_inventory", {}).values():
            path = (export_dir / asset["file"]).resolve()
            if not path.is_relative_to(export_dir.resolve()):
                raise ValueError("Export asset path escapes its directory.")
            names.append(asset["file"])
    except json.JSONDecodeError:
        pass
    for name in names:
        with (export_dir / name).open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[name] = digest.hexdigest()
    return hashes


def export_context(export_dir: Path) -> dict:
    selection = json.loads((export_dir / "selection.json").read_text(encoding="utf-8"))
    return selection.get("selection_context") or {}


def assessment_markdown(assessment: dict) -> str:
    lines = ["# Post-Generation Assessment", "", f"Rubric: {assessment['rubric_version']}",
             f"Reviewer: {assessment['reviewer']['name']} ({assessment['reviewer']['kind']})", ""]
    context = assessment.get("selection_context") or {}
    lines += [f"Target audience: {context.get('target_audience') or 'Unknown (legacy export)' }.", selection_label(context), ""]
    if assessment["overall_score"] is None:
        lines.append("Overall score: **not available; assessment incomplete**.")
    else:
        lines.append(f"Overall score: **{assessment['overall_score']:.2f}/100**.")
    lines += [f"Earned weighted points: {assessment['earned_points']:.2f}; "
              f"assessed weight: {assessment['assessed_weight_percent']}%.",
              "Missing dimensions are not scored as zero or normalized away.",
              f"Publication: **{assessment['publication_status']}**.", "",
              "| Dimension | Weight | Rating | Points |", "|---|---:|---:|---:|"]
    for dimension in assessment["dimensions"]:
        rating = "Not assessed" if dimension["score"] is None else f"{dimension['score']}/4"
        points = "-" if dimension["weighted_points"] is None else f"{dimension['weighted_points']:.2f}"
        lines.append(f"| {dimension['label']} | {dimension['weight']}% | {rating} | {points} |")
    lines += ["", "## Evidence", ""]
    for dimension in assessment["dimensions"]:
        lines.append(f"### {dimension['label']}")
        lines.extend(f"- {item}" for item in dimension["evidence"])
        lines.append("")
    lines += ["## Publication Gates", ""]
    for key, gate in assessment["gates"].items():
        lines.append(f"- {key}: {gate['status']}. {gate['evidence']}")
    lines += ["", "## Blockers", ""]
    lines.extend(f"- {blocker}" for blocker in assessment["publication_blockers"])
    lines += ["", "Approval applies only to the exact artifact hashes in assessment.json.", ""]
    return "\n".join(lines)


def assess_export(export_dir: Path, review: dict) -> Path:
    current_hashes = artifact_hashes(export_dir)
    if review.get("artifact_sha256") != current_hashes:
        raise ValueError("Review artifact hashes are missing or stale. Review the current export again.")
    review = {**review, "selection_context": export_context(export_dir)}
    assessment = score_review(review)
    assessment.update(artifact_sha256=current_hashes, created_at=datetime.now(timezone.utc).isoformat())
    destination = export_dir / "assessments" / uuid.uuid4().hex[:12]
    destination.mkdir(parents=True, exist_ok=False)
    save_json(destination / "review.json", review)
    save_json(destination / "assessment.json", assessment)
    (destination / "assessment.md").write_text(assessment_markdown(assessment), encoding="utf-8")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure an exported compilation or score a legacy editorial review.")
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("review", type=Path, nargs="?", help="Optional legacy editorial review JSON")
    parser.add_argument("--automatic", action="store_true", help="Run objective technical checks without a reviewer")
    parser.add_argument("--force", action="store_true", help="Recompute automatic checks instead of using a matching report")
    parser.add_argument("--template", action="store_true", help="Create a blank review bound to the current export.")
    arguments = parser.parse_args()
    if arguments.automatic or (arguments.review is None and not arguments.template):
        if arguments.review is not None or arguments.template:
            parser.error("Automatic checks do not use a review file or template.")
        print(assess_export_automatically(arguments.export_dir, force=arguments.force))
        return
    if arguments.review is None or arguments.force:
        parser.error("Legacy review/template mode requires a review path and does not accept --force.")
    if arguments.template:
        review = review_template(arguments.export_dir)
        with arguments.review.open("x", encoding="utf-8") as stream:
            json.dump(review, stream, indent=2)
        print(arguments.review)
        return
    review = json.loads(arguments.review.read_text(encoding="utf-8"))
    print(assess_export(arguments.export_dir, review))


def review_template(export_dir: Path) -> dict:
    return {
    "rubric_version": RUBRIC_VERSION,
    "selection_context": export_context(export_dir),
        "reviewer": {"kind": "human", "name": ""},
        "artifact_sha256": artifact_hashes(export_dir),
        "ratings": {key: {"score": None, "evidence": []} for key in DIMENSIONS},
        "gates": {key: {"status": "not_assessed", "evidence": ""} for key in GATES},
    }


if __name__ == "__main__":
    main()