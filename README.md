# Educational Short Clip Demo

Gradio app that turns a long lecture video into a short compilation: transcribe → source-grounded concepts → audience-aware nomination and editorial scoring → human keep 1–5 → ffmpeg assemble with captions.

Illustrated HTML: [`docs/pipeline.html`](docs/pipeline.html) (Mermaid + SVG). Four-week plan: [`docs/four-week-plan.md`](docs/four-week-plan.md). Code walkthrough: [`pipeline/README.md`](pipeline/README.md). Product spec: [`gradio_demo_workflow_plan.md`](gradio_demo_workflow_plan.md).

High-level tech stack and features: [Project overview](docs/project-overview.html) (self-contained HTML with SVG workflow and Mermaid source).

## What is in this repo

```text
app.py                      Gradio UI
pipeline/                   transcribe, nominate, rank, cut, assemble, export
prompts/nominate_clips.txt  Ollama system prompt
docs/                       architecture notes
requirements.txt
```

`work/` and `exports/` are local run output (source copies, previews, `compilation.mp4`). They are gitignored and should not be pushed.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on `PATH`
- [Ollama](https://ollama.com/) running locally, with a chat model (default `qwen2.5`)

```bash
ollama pull qwen2.5
pip install -r requirements.txt
```

WhisperX will pull PyTorch and ASR weights on first transcribe.

## Run

```bash
python app.py
```

1. Upload a local lecture mp4 and choose the target audience.
2. Transcribe, then **Nominate + rank clips**.
3. On Review, keep 1–5 on-topic clips (edit start/end if needed).
4. Confirm the selection to automatically generate a storyboard and render its preview.
    In **Package + export**, review the generated draft and preview; edits are optional.
    There is no separate generation button. If generation fails, correct the reported
    issue and confirm the selection again to retry. Use **Preview storyboard** after edits.
5. Enter the human reviewer's name, attest the review, approve the storyboard,
   then export one compilation.

Audience options are General audience (default), Beginners, Students, Professionals,
and Advanced learners. Profiles provide internal selection goals for nomination and
ranking; no topic or learning-objective entry is required. These are generic audience
preferences, not inferred lecture-specific objectives. Changing the audience clears
existing candidates and approval but retains the transcript for re-nomination.

The audience workflow checks sentence boundaries and nearby context, keeps repairs
within your duration limits, and ranks model estimates for audience fit (40%),
completeness (35%) and educational value (25%). Scores include timestamped evidence;
unavailable reviews remain unscored. Incomplete/context-risk candidates and redundant
overlaps are filtered. Duration settings apply when nomination is rerun, without
retranscription. Export metadata retains the audience and selection settings for
post-generation review. See the [rubric](docs/post-generation-rubric.md) for limits.

Do not commit lecture recordings unless you have rights to redistribute them.

## Storyboard and Pacing

One reviewed, complete clip is sufficient to proceed; extra clips are not required.
Keep it and confirm the selection to generate and preview the storyboard. For one
clip, the opening and ending are generated normally and there are no transitions.
Unavailable
model scores still require manual source review. New generated storyboards enforce a
45-120 second total: opening card + selected speech + divider cards + ending. Transition
overlays add no time. The editor shows an estimated total; measured preview and final
export durations are checked too. Out-of-budget selections must be adjusted, not
silently trimmed or padded. Existing legacy exports are unchanged.

A single clip needs enough speech to reach 45 seconds with its 3-second opening and 4-6 second ending:
37 seconds of speech plus a 3-second opening and 5-second ending is valid, but a 30-second excerpt is not.
For a short lone candidate, increase the per-clip maximum (for example, to 60-90
seconds) and rerun nomination, or review a longer complete range within your limits.
Never duplicate footage or weaken context checks merely to meet the duration target.

New compilations start with a separate full-screen opening card: a large centered
headline, optional subtitle, dark green background and gold accent. The centered title
is read aloud using the offline macOS `say` engine, Samantha voice, at 175 words/minute.
Only the title is spoken, not the subtitle. The minimum is 3 seconds (editable from
2-3 seconds); longer narration extends the card instead of truncating or speeding up
the voice. Lecture speech and captions begin after the narration and a short pause.
Measured preview/export duration must still meet the 45-120 second limit.
New narrated storyboards require macOS with the Samantha voice installed; missing
speech synthesis reports an error rather than silently exporting a silent intro.
Previously saved silent or overlay storyboards retain their behavior; confirm the
selection again to generate a narrated card.
Each join can be a clean **Cut**, an **Overlay** on incoming
footage, or a 1-2 second **Divider** for a genuine topic change. Overlay placement is
Upper or Middle; inspect the preview to avoid covering faces or important slides.
Original lecture speech is retained unchanged; synthesized narration is confined to
the opening card and never overlaps the lecture audio.

The 4-6 second ending contains one takeaway, an audience-appropriate reflection
question, editable source attribution and a short CTA. The source URL is in the post,
not a clickable video element. Text budgets and pixel fitting reject overflow rather
than silently truncating claims. Reading-speed budgets are rough safeguards, not a
substitute for watching the output on the target display.

Ollama receives all confirmed excerpts in order and the target audience, and generates
the opening, transition labels, ending synthesis and reflection question together.
The currently selected model is used. A structured response is checked for source IDs,
word limits, readability budgets and total duration, with one correction attempt.
Failures are shown explicitly, without silently substituting metadata. Generation and
model provenance are retained in the exported storyboard. Source credit and CTA remain
safe editable defaults; the model does not invent speaker attribution.

Supporting excerpts and the sections citing them are displayed, but reference validity
does not prove semantic support. Check surrounding source context yourself; do not
imply causation merely because two excerpts are adjacent. Generation never changes
clip ranges or grants human approval. Regeneration clears old preview/approval state;
if rendering fails, generated text remains available for correction and another preview.

Preview is a full-length, maximum-640-pixel render using the same storyboard and
caption timeline as export. Changing clips, text, timings, placement or aspect ratio
invalidates its approval. Reviewer changes revoke approval but retain the preview.
Approval is a named local declaration, not authenticated authorship or publication
approval. `selection.json` records the storyboard, approval revision and measured
timeline; existing exports are never overwritten. Legacy Python callers can still
request the previous static-card assembly by omitting `storyboard`.

## Post-Generation Assessment

Use the [weighted assessment rubric](docs/post-generation-rubric.md) to review a
rendered compilation in **5. Assessment**. Select an export to see its saved score,
or enter a named human review with ratings and evidence to calculate a new assessment.
The tab and `python -m pipeline.assess` generate versioned JSON and Markdown reports.
Missing ratings remain unassessed;
publication approval requires all dimensions to be acceptable and human final review.
