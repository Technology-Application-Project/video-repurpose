# Brevio Studio — Workflow Plan

Simple end-to-end plan for a Gradio demo app.  
Goal: turn a long educational video into 3–5 short, on-topic clips for social platforms.

This document is the spec for later codegen. Keep the first version linear: one pipeline, human approval in the middle, packaging last.

---

## 1. Product goal

Upload or select a long lecture/podcast video, declare the teaching topic, then:

1. Transcribe with timestamps
2. Let an LLM nominate candidate clips by topic
3. Score candidates against learning objectives with embeddings
4. **Human reviews Top N and keeps 3–5 on-topic clips**
5. Cut, package, and export shorts with source CTA

The app does **not** auto-publish. It produces files + suggested post text.

---

## 2. Demo principles

- Text-first: find clips from the transcript, then cut video.
- Human-in-the-loop is required before ffmpeg cutting.
- Keep 3–5 clips only if they match the topic. Hook quality is secondary.
- Packaging (title card, captions, CTA) happens **after** clips are approved.
- CTA points to the **source video**, not “follow me”.
- Reuse existing local tools: WhisperX, Ollama (`qwen2.5`), SentenceTransformers, ffmpeg.

---

## 3. Whole pipeline

```text
[1 Input]
   long video + topic + learning objectives + source URL
        ↓
[2 Transcribe] WhisperX → transcript.json / .srt (word-level timestamps)
        ↓
[3 Nominate] LLM groups sentences into 20–45s candidates by topic
        ↓
[4 Rank] embedding cosine similarity vs learning objectives
        ↓
[5 Human review] listen/read Top N, keep 3–5 on-topic clips
        ↓
[6 Cut] ffmpeg cut on sentence boundaries (internal raw cuts only)
        ↓
[7 Assemble] intro + selected clips + captions + takeaway/CTA end card
        ↓
[8 Export] compilation.mp4 + captions.srt + post.txt
```

Raw cuts stay in the working directory and are not exported.

---

## 4. Step details

### Step 1 — Input

User provides:

| Field | Required | Example |
|---|---|---|
| `source_video` | yes | `lecture.mp4` |
| `topic` | yes | Which country has the best education system? |
| `learning_objectives` | yes, 3–5 lines | How education systems are measured |
| `source_url` | yes for CTA | YouTube/original URL |
| `clip_length_range` | no | 20–45 seconds |
| `platform` | no | tiktok / shorts / reels / bilibili |
| `series_prefix` | no | 1/5, 2/5 |

### Step 2 — Transcribe

- Extract audio, run WhisperX (`small` is enough for demo).
- Save `transcript.json` (segments + words + start/end) and `transcript.srt`.
- Show transcript in the UI so the user can trust timestamps before later steps.

### Step 3 — LLM nominate (broad search)

Call local Ollama via `ollama.chat` (MVP model: `qwen2.5`). Same pattern as `ollama_chat.py`. Require JSON output (`format="json"` if the model supports it).

Input: topic + timestamped transcript (chunk if long).  
Output: JSON list of candidates:

```json
{
  "candidates": [
    {
      "id": "c1",
      "start": 5.915,
      "end": 22.407,
      "title": "Exam ranking is not the whole story",
      "takeaway": "High test scores do not automatically mean the best system.",
      "reason": "Self-contained claim about how we judge education systems.",
      "quote": "Asian schools often get the best results..."
    }
  ]
}
```

Rules for the prompt:

- 20–45 seconds
- one knowledge point
- understandable without earlier context
- skip greetings, guest intros, ads
- do **not** invent timestamps; only use transcript times
- prefer cutting on sentence end

### Step 4 — Embedding rank (topic check)

- Encode each learning objective and each candidate text with `all-MiniLM-L6-v2`.
- Score = max cosine similarity to any objective (or mean of top-2).
- Sort descending. Keep Top N (default N = 10) for human review.
- Show score next to each card so the user sees why it ranked.

Embedding does **not** decide the final cut. It only ranks.

### Step 5 — Human review (required)

UI shows Top N as cards:

- video/audio preview from `start`–`end`
- transcript excerpt
- title, takeaway, similarity score
- checkbox: Keep
- optional: edit `start` / `end` / title

Human must judge:

1. On-topic?
2. Standalone / complete?
3. One idea, not chat?

Hook/opening is optional. If the idea is right but the in-point is weak, user adjusts timestamps instead of discarding.

**Gate:** packaging and export stay disabled until 3–5 clips are kept.

### Step 6 — Cut

For each kept clip:

```bash
ffmpeg -ss {start} -to {end} -i source.mp4 -c copy raw_clip_{id}.mp4
```

If copy-cut lands on a bad keyframe, re-encode that clip. Use WhisperX word times so cuts are on sentence boundaries, not round seconds.

### Step 7 — Package (after approval)

Do in this order:

1. Crop/scale to 1080×1920 (center, keep talking head)
2. Burn captions from the clip’s SRT slice
3. Overlay 1–2s title card: question or takeaway, not lecture name
4. Loudness normalize (~-14 LUFS)
5. End card 1–2s: source CTA, no URL burned in as giant text

Optional later (not MVP): F5-TTS voiceover for title/CTA, intro image card, BGM.

### Step 8 — Export + CTA

Per clip, write a folder:

```text
exports/clip_{id}/
  clip.mp4
  captions.srt
  post.txt
```

`post.txt` template:

```text
{title}

{takeaway}

Full source: {source_url}
Approx. original time: {start}–{end}
Series: {i}/{total}
```

On-screen CTA line (spoken or subtitled):

> Full discussion in the original lecture. Link in the description.

Platform note in UI: TikTok/Reels often need the URL in comments or bio; YouTube/Bilibili can put it in the description.

---

## 5. Gradio UI (demo)

One page, four tabs. State is kept in memory / a temp working dir.

### Tab A — Input

- `gr.Video` or file upload
- `gr.Textbox` topic
- `gr.Textbox` learning objectives (one per line)
- `gr.Textbox` source URL
- Slider: clip min/max seconds (20–45)
- Dropdown: platform
- Dropdown: Ollama model (default `qwen2.5`)
- Button: `Run transcription`

### Tab B — Transcript + candidates

- Transcript viewer
- Button: `Nominate + rank clips`
- Table/gallery of ranked candidates (read-only here)

### Tab C — Human review (core)

- Top N cards
- Each card: preview, text, score, Keep checkbox, start/end number inputs
- Counter: `Kept: 4 / 3–5 required`
- Button: `Confirm selection` (enabled only when 3–5 kept)

### Tab D — Package + export

- Intro title + 1–2s intro duration
- Editable **key takeaway** and **CTA** for the end card
- Button: `Export compilation`
- Preview: `compilation.mp4` (intro + selected clips with burned captions + takeaway/CTA end card)
- Download zip: `compilation.mp4`, `captions.srt`, `post.txt`
- Separate raw cuts are **not** exported

Progress: `gr.Progress` or status markdown between steps. Fail loudly if Ollama / ffmpeg is missing. On Tab A, show a short status like `Ollama: qwen2.5 ready` after a `ollama.list()` ping.

---

## 6. App structure (for later codegen)

```text
app.py                  # Gradio entry
pipeline/
  transcribe.py         # WhisperX
  nominate.py           # Ollama JSON candidates
  rank.py               # SentenceTransformer scoring
  cut.py                # ffmpeg cut
  assemble.py           # intro + captions + takeaway/CTA end card
  package.py            # crop, captions, title, CTA
  export.py             # folders + post.txt + optional compilation
prompts/
  nominate_clips.txt
```

Shared working dir per run: `work/{run_id}/`.

LLM client (MVP): official `ollama` Python package.

```python
import ollama

response = ollama.chat(
    model="qwen2.5",
    messages=[
        {"role": "system", "content": nominate_system_prompt},
        {"role": "user", "content": nominate_user_prompt},
    ],
    format="json",
)
```

Default: `http://localhost:11434`. Make the model name a Gradio dropdown (`qwen2.5` first). LM Studio is out of MVP scope.

---

## 7. MVP vs later

**MVP (demo day)**

- Local mp4 upload (not YouTube download)
- WhisperX
- Ollama (`qwen2.5`) nominate
- MiniLM rank
- Human keep 3–5
- ffmpeg cut + burn SRT + title text + post.txt CTA
- Optional: Assemble compilation (intro + raw cuts) for Demo/YouTube

**Later**

- YouTube ingest
- LM Studio / other OpenAI-compatible backends
- F5-TTS intro/CTA voice
- Auto series numbering
- Thumbnail export
- Batch multiple videos

Out of scope: auto-posting, background music ducking, face tracking.

---

## 8. Demo script (2–3 minutes)

1. Upload lecture clip + paste topic and 3 learning objectives + source URL.
2. Show transcript appear.
3. Show ranked candidates; point at a high-score on-topic row and a low-score greeting.
4. In Review, play two clips, keep 3 on-topic ones, tweak one timestamp.
5. Export; play one packaged short; show `post.txt` with source link.

Talking point: models suggest, **human decides topic fit**, packaging never runs before approval.

---

## 9. Acceptance checks

- Cannot export with 0 or 6+ kept clips.
- Candidate timestamps must fall inside the transcript.
- Kept clips are on-topic (human), not merely “has a hook”.
- Output video is 9:16 when packaging is on.
- Captions match the cut range.
- CTA mentions the source video; URL lives in `post.txt`, not as the first-frame hook.
- No packaging files are generated before `Confirm selection`.
