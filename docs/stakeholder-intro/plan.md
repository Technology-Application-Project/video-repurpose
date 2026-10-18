# Brevio Studio: stakeholder introduction plan

A 86-second narrated introduction to Brevio Studio for stakeholders who have not used it. It explains what the app does, how a lecture becomes a reviewed short video, and why the output can be trusted.

## Audience and goal

| | |
|---|---|
| Audience | Sponsors, programme owners and teaching or communications leads. Not technical. |
| Goal | They understand the four steps, see the safeguards, and agree to trial it on one lecture. |
| Length | 86 seconds (target 60-90). About 210 spoken words. |
| Ask at the end | Try it on one lecture and send feedback. |

## Key messages

1. One long lecture becomes a short video written for a chosen audience.
2. The app plans the story first, then builds it, then checks the result against the plan.
3. A person approves every export. Nothing is published automatically.
4. Most work runs locally; cloud use is limited to short, capped text requests.

## Storyboard and voice-over

Times are when each line starts. Visuals use the real app with a scripted, mocked session, so nothing is generated or charged while recording.

| Time | Scene | On screen | Voice-over |
|---|---|---|---|
| 0:01 | Hook | Title card: "One long lecture. One short, reviewed video." | Brevio Studio turns one long lecture into a short, reviewed video, for the audience you choose. |
| 0:07 | Problem | Card with three points: Long, Slow, Risky. | Lectures hold great ideas, but few people watch an hour of video. Cutting highlights by hand is slow, and fully automatic tools are hard to trust. |
| 0:17 | Step 1 · Source | Source screen: choose Kids, open Advanced settings, type the source link. | You start with a lecture video and pick the audience: researchers, the general public, or kids. A source link is optional. |
| 0:25 | Step 2 · Clips | Analyze, then the clip list: untick and retick a clip, Set in. | Brevio transcribes the lecture on your own computer and suggests the strongest clips. You keep up to five and fine tune each one. |
| 0:33 | Step 2 · Blueprint | Generate preview; the Blueprint panel shows clip count, opening, bridges and ending. | Before writing anything, it plans the story: clip count, opening idea, bridges and ending. The finished video is checked against that plan. |
| 0:42 | Step 3 · Studio | Preview plays; timeline sections and Source evidence are clicked in turn. | It builds a preview with a narrated opening, bridges and ending, plus illustrations, music and captions, each section beside its source evidence. |
| 0:52 | Step 3 · Approval | Reviewer name, "I reviewed the preview", Approve preview. | Nothing is exported until a named reviewer has watched the preview and approved it. |
| 0:58 | Step 4 · Export | Export, then the Technical check and Structure check tabs, including "Does the story follow the plan?". | Every export gets two plain-language checks: technical, for media, captions and loudness, and structure, for order, bridges, narration and the plan. |
| 1:07 | Safeguards | Card: Local, Gateway, Human. | Transcription, voices and rendering run locally. Only short text requests use the company gateway, capped at five per run. A person always decides. |
| 1:18 | Close | Card: "Faster highlights. Consistent quality. A human in control." | Faster highlights, consistent quality, and a human in control. Try it on one lecture, and tell us what to improve. |

## Deliverables

All files are in `docs/stakeholder-intro/`.

| File | Use |
|---|---|
| `brevio-studio-intro.mp4` | Final video, 1440x900, narrated, with on-screen captions. |
| `voice-over.wav` | Narration alone on the video timeline (mono, 48 kHz), for re-editing. |
| `transcript.srt` | Sentence-level subtitles aligned to the voice-over. |
| `transcript.txt` | Readable transcript with scene labels and start times. |
| `script.json` | Source of truth for the wording, voice and speed. |

The voice is local Kokoro `af_heart` at speed 1.05, with no cloud text-to-speech. The voice-over is synthetic, so mention that if it is shared outside the team.

## Regenerating

Edit `script.json`, then from the project root:

```
python tools/make_voiceover.py
cd frontend && STUDIO_URL=http://127.0.0.1:8791 node scripts/record-intro.mjs
```

The first command writes the scene audio and measured timings to `work/stakeholder-intro/`. The second uses an isolated server on port 8791 that must already be running (`python -m uvicorn api.main:create_app --factory --port 8791`). Each scene's length follows its narration, so changing the wording changes the runtime; the script reports any scene whose on-screen actions overrun its narration.

## Review before sharing

- Check each claim: "up to five clips", "capped at five per run" (the text-request cap per batch) and "company gateway" (the Xero Portkey gateway). Reword "company gateway" if stakeholders are outside Xero.
- The example lecture, clips and export shown are real local files; replace them if the source lecture should not be shown.
- No outcome or time-saving numbers are claimed because none have been measured. Add them if you have a trial result.
- Listen once for pronunciation, and confirm the pace works for non-native listeners.
