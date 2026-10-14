# Educational Short Clip Demo

Gradio app that turns a long lecture video into a short compilation: transcribe → source-grounded concepts → audience-aware nomination and editorial scoring → automatic (or human) clip selection → ffmpeg assemble with captions.

Illustrated HTML: [`docs/pipeline.html`](docs/pipeline.html) (Mermaid + SVG). Four-week plan: [`docs/four-week-plan.md`](docs/four-week-plan.md). Code walkthrough: [`pipeline/README.md`](pipeline/README.md). Product spec: [`gradio_demo_workflow_plan.md`](gradio_demo_workflow_plan.md).

## What is in this repo

```text
app.py                      Gradio UI
pipeline/                   transcribe, nominate, rank, cut, assemble, export
prompts/nominate_clips.txt  Ollama system prompt
docs/                       architecture notes
requirements.txt
```

`work/` and `exports/` are local run output (source copies, previews, `compilation.mp4`). They are gitignored and should not be pushed. Each run is named `YYYYMMDD-HHMM_<source-file>_xxxx` and each export is written to `exports/<run>/YYYYMMDD-HHMMSS_<audience>_<aspect>_xxxx/` (local time, e.g. `20261012-091830_kids_16x9_5f4d`) with a matching `compilation-<same name>.zip`. `exports/latest` is a symlink to the newest export. Older folders keep their old names and still load.

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) on `PATH`
- [Ollama](https://ollama.com/) running locally, with a chat model (default `qwen2.5`)
- Internet access for Edge TTS neural narration (all audiences); macOS `say` only for retired legacy profiles
- Google Gemini API access for images, Veo video and Lyria music; Google or Portkey for creative text, with reviewed model pricing

```bash
ollama pull qwen2.5
pip install -r requirements.txt
```

WhisperX will pull PyTorch and ASR weights on first transcribe.

## Run

### Cloud setup and spending

Enter your key after `GEMINI_API_KEY=` in the project-root `.env` file. The app loads
this file automatically at startup; it also contains `CLOUD_PRICING_FILE` pointing
to the project pricing configuration. Restart the app after editing `.env`.
Existing shell environment variables take precedence; run `unset GEMINI_API_KEY`
in the launch terminal to use the file instead of an old exported key.

The local `.env` file is Git-ignored and should have owner-only permissions
(`chmod 600 .env`). It stores the key as plaintext, not encrypted storage. Never
put the key in tracked source, pricing files, Gradio forms, logs or exports.
No paid calls are made on app startup.

### Xero Portkey Text Generation

The current pricing configuration selects `text_provider: "portkey"` and
`models.text: "@vertexai-us/gemini-3.5-flash"`. This is your Model Catalog alias;
its existence, underlying model and account access have not been verified.
Request Portkey access in Okta for the **Test** environment and its **Shared Team
Workspace**. Use the matching Test user key. Rotate any key shared in chat, then
set `XERO_PORTKEY_API_KEY` privately in the launch shell before starting the app.
Do not paste keys into chat, tracked code or command examples committed to Git.
The generic `PORTKEY_API_KEY` variable is not used or accepted as a fallback.

The SDK is explicitly pinned to `https://llm-gateway.xgw.xero-test.com/v1`, giving
the chat endpoint `https://llm-gateway.xgw.xero-test.com/v1/chat/completions` and
the `x-portkey-api-key` authentication header. This is the local-access Test
endpoint, not the public Portkey host. UAT and Production are not configured;
they have different endpoints and access rules. Restart the app from the shell
containing the Test key; existing processes do not pick up new shell variables.

Check the Test workspace Model Catalog before using the configured Gemini alias.
The guide's `@bedrock/global.anthropic.claude-sonnet-4-6` is an example, not a
guarantee of availability or a reason to silently switch the configured model.

The shipped `cloud-pricing.json` uses Portkey text, local illustrations and the bundled
music library, with an operator-accepted, unverified USD 0.05 per text/image request.
Replace `usd_per_request`, `price_source` and `checked_at` with reviewed figures,
including Vertex token costs and any gateway fees, allowing selected excerpts and
4096 output tokens per request. Then refresh the estimate and approve a new batch.
The prior Google-direct price and approval do not transfer to this provider.

The adapter uses `Portkey(...).chat.completions.create` with lowercase
`max_tokens` and a JSON-schema response format. SDK retries and request-level
gateway retries are disabled; no fallback route is configured. Ensure the Model
Catalog route also has no account-side fallback. Completed text is cached and
records Portkey provenance. The Test endpoint is included in approval metadata;
switching providers or endpoints invalidates old quotes/batches.

Only storyboard text uses Portkey. Selected excerpts and prompts go through Xero
Test to its configured provider; image/video/music prompts still go directly to Google.
Keep `GEMINI_API_KEY` for media. Portkey does not resolve the existing Google image
quota restriction. To explicitly restore direct Google text, set
`text_provider: "google"`, an accessible Google text model and reviewed pricing,
then approve a new batch. No provider switch happens automatically.

### Local Illustration Media (no image/video/music API)

If the gateway only allows text models, set `"media_provider": "local-illustration"`
(with `"text_provider": "portkey"`) in the pricing file. It is explicit; nothing switches
to it automatically. The text model writes constrained JSON, never code or markup:

- **Images:** layered geometric shapes (rect, ellipse, polygon, line), validated and
  clamped locally, then drawn with Pillow. Invalid shapes are dropped; fewer than 6 usable
  shapes or a nearly blank render fails the request.
- **B-roll:** a 4-second pan and zoom of the approved opening illustration (FFmpeg).
- **Music:** by default the bundled library below; only with `"music_source": "generated"` is it note data rendered by a small local synthesizer (near-silent output is rejected).

Set `models.image` to your Portkey text alias, `models.video` to
`local-ffmpeg-zoompan`, and give image a conservative text-call estimate;
video may be `0`. With `music_source: generated` also set `models.music` and its estimate. Each image (and generated music) request is one Portkey text call counted against
the same caps, and a failed or truncated request is not resubmitted in that batch.
Output is flat conceptual graphics, not photographic imagery, and is labeled
"AI illustration". Google media credentials are not needed in this mode.

### Bundled Music Library

`assets/music/` holds nine original instrumental beds (three per audience, 64 s,
stereo MP3, CC0) described by `manifest.json` and rendered by `tools/generate_music.py`
(needs numpy, scipy and ffmpeg; output is reproducible). The pricing option
`"music_source"` defaults to `"library"`: no music request is sent or priced, and the
track is chosen from the audience's three by a stable hash of the selected clips, so
the same selection always gets the same music. The track's path, hash, licence and
credit are stored in the storyboard like any other media asset, copied into the export
ZIP, and credited in `post.txt`; the post no longer calls it AI-generated. Library
tracks carry their own fades, so the mix skips its extra fade-in. Tracks are 64 s, so
the 30-60 s budget never loops them; an older draft longer than 64 s would loop with
a short dip. Set `"music_source": "generated"` to go back to model-written music.

### Pricing And Approval

`cloud-pricing.example.json` deliberately contains no usable models/prices. Configure
a private JSON file with the same structure and set `CLOUD_PRICING_FILE` to its path.
Specify accessible Google model IDs for `text`, `image`, `video` (Veo supporting
4-second 720p generation) and `music` (Lyria clip). Enter positive conservative
USD-per-request estimates, their pricing URL and checked date. The text estimate must
allow selected excerpts plus up to 4096 output tokens. Consult
[Google pricing](https://ai.google.dev/gemini-api/docs/pricing); account access and
actual billing must be verified by the operator. Unknown pricing blocks generation.

Each approved batch permits at most four text requests (two fitting rounds with one
correction each), two images, one 4-second B-roll video and, only when
`music_source` is `generated`, one instrumental music
request. The displayed estimate is not a billing guarantee. Uncheck/recheck approval
to authorize a new batch; keep approval unchanged to resume the existing batch.
Completed requests are cached and hash-verified. Known video jobs resume by operation
ID; ambiguous non-video requests are never blindly resubmitted. No silent Ollama fallback.
Stopping local work does not guarantee cancellation of provider charges.

The configured providers receive selected excerpts and creative prompts, not the complete video.
Generated visuals are labeled AI illustrations, not evidence of real results/events.
Music is instrumental only; bundled tracks are vetted, generated music should be checked for unwanted vocals and distraction.
Veo audio is discarded. Music is ducked under original speech/narration.

Optional `LOCAL_SFX_MANIFEST` points to JSON containing `file` (relative to the manifest),
`license`, `source`, `credit`, and `redistributable: true`. Supply only effects whose
license permits this use and redistribution. No effects are bundled; without this
manifest the mix has no sound effects. Approved media and narration stems are included
in exports with hashes; export never generates fresh cloud assets or voices.

```bash
python app.py
```

1. Upload a local lecture mp4 and choose the target audience (16:9 is the default aspect ratio).
2. Transcribe, then **Nominate + rank clips**. With **Choose clips automatically** ticked (the default),
   the best-ranked clips that fit the 30-60 s budget (up to 3) are kept and confirmed for you and the
   candidate table marks them `automatic`. The choice is recorded as automatic, with no human review,
   in `selection.json`, the preview status and the Assessment tab. Untick it to review on tab 3.
3. The cloud estimate loads when the page opens (a blocked estimate explains why). Approve the
   batch cost, then **Confirm clips + generate storyboard**. To choose by hand, change a Keep box or a
   clip time on Review first; that switches the record to manual.
    The app fits complete ideas to 30-60 seconds, generates the storyboard with the configured text model,
    measures narration, generates illustrations/B-roll, picks the audience's bundled music track, and renders the preview.
    Inspect the automatic selection-change report: fitting may trim or remove kept clips.
    In **Package + export**, review the preview; the status line names the narrator, music track and accent
    words. Once media exists, changing any text or timing needs **Regenerate storyboard**.
    Confirmation and generation run in one server-side workflow. **Preview storyboard**
    also generates a missing or stale draft automatically. Generation failures appear
    on both Review and Package; export remains locked until preview and human approval.
4. Enter the human reviewer's name, attest the review, approve the storyboard,
   then export one compilation.

Two steps always need a person: approving the cloud cost and approving the storyboard preview.

Opening, bridge and ending cards are styled per audience: Researcher uses a serif face,
thin rules and labels such as "Research question"; General public uses heavy type, coral
diagonal bands and "Worth knowing" / "Up next"; Kids uses a rounded face, stars and
"Let's find out!" / "Next up!". Each card has a kicker chip, a gradient scrim over the
illustration and an eased push-in on the background only (ceiling per audience, see below). Text is rendered as
separate layers and revealed line by line (fade plus a short slide-up), timed from the
measured narration length: the chip first, each title line as the voice reaches it. The
schedule is a fixed function of the wording and the frozen narration audio, so previews and
exports match. TTS provides no word timestamps, so line timing is proportional to characters.

The ending has two beats: the takeaway alone and large while it is spoken, then a
"your move" beat with the question and call to action. The source credit and "AI
illustration" label sit in a small corner label for the whole card. Text is never shrunk
below a readable size (4.5% of the short side); wording that cannot fit fails generation
with a message instead of becoming small. The model is also asked for a hook, bridge and
ending style suited to the audience, grounded in the excerpts.

Generated wording rules (checked after generation, with one automatic correction; older
approved drafts are unaffected): opening titles are at most 7 words with a subtitle of at
most 6; they may not open with throat-clearing such as "In this video", "Today we" or
"This lecture"; General public and Kids titles must be questions; bridge labels are at
most 4 words.

Layout details: bridge and ending type starts at 6-7.5% of the short side and is shrunk
only as far as the text needs. On 9:16 the text block is centred at 58% of the height
(kept above the bottom 15% where it fits) instead of at 50%; 16:9 stays centred. The model
also returns one accent word per opening title, bridge label and ending takeaway; it is
shown in the audience accent colour and dropped if it is not a single word from that text.
The opening and bridge backgrounds drift sideways while they zoom (the opening to the right,
bridges alternating); the ending stays centred. The opening chip adds the measured total
runtime ("WORTH KNOWING · 43 SEC") when the draft has one. A bridge built from a real clip
frame (drafts without generated media) uses the raw frame, not the letterboxed one, and
blurs it so the lecture's own text cannot collide with the label.

Each audience has a look and a motion profile in `media.PRESENTATION` (`look`, `motion`):

| | Researcher | General public | Kids |
|---|---|---|---|
| Card veil over the art | near-charcoal | dark teal | light cream with navy text |
| Chip / accent text | square-ish chip, teal | pill, coral | pill, yellow chip, pink accent text and number badge |
| Zoom ceiling / easing | 1.06 / linear | 1.15 / ease-out | 1.28 / slight overshoot |
| Fade / slide | 0.45s / 1.2% | 0.30s / 3% | 0.18s / 6% |
| Background drift | none | yes | yes |
| Text reveal | whole block | line by line | word by word (titles up to 8 words) |
| Decor entrance | rules wipe in | wedges slide in from corners | stars and dots pop in |
| Ending beat 2 hero | reflection question | reflection question | spoken call to action |

A contrast test renders every audience over bright, dark, saturated and noisy art and
requires 4.5:1 for text and 3:1 for accent text, so a new look cannot ship unreadable.
Decor is separate small layers, so it no longer zooms with the background. The Kids look
is a cream veil (85% opaque at the centre, 70% at the top and bottom), which washes the illustration to pastel by design.

Captions drop filler sounds (um, uh), immediate stutters of function words, and
non-speech tags such as [MUSIC]. Each caption is held through the pause after it, within
its clip. Fast speakers can still exceed the 20 characters/second reading-speed check.

Audience options are **Researcher**, **General public** (default), and **Kids (ages 8-12)**.
Profiles provide internal selection goals for nomination and
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

If no model ranges are usable, or every model nomination is rejected, nomination
tries up to 12 sentence-bounded alternatives spread across the full transcript.
These use the available duration allowance for context and undergo the same editorial
checks; rejected clips are never promoted automatically. If the review list is still
empty, increase Max clip seconds (default 15) and rerun nomination without retranscribing,
or choose a source with a complete explanation. Unavailable scores require manual
source review and do not imply approval.

Do not commit lecture recordings unless you have rights to redistribute them.

## Transcription

Choose the transcription model on the Input tab. Timings below are measured on an Apple
M3 Pro CPU with `int8`, as processing time per second of audio:

| Model | Speed | Notes |
|---|---|---|
| `small` (default) | about 0.13x | Fastest; may misspell technical terms |
| `distil-large-v3` | about 0.3x | No accuracy gain seen on the lectures tested |
| `large-v3` | about 0.5x | Only model that spelled "retrosplenial" and "parahippocampal" correctly |

An 80-minute lecture takes roughly 10 minutes with `small` and about 37 minutes with
`large-v3`. Models download on first use. CPU runs use `int8`; on this machine it matched
`float32` text exactly at the same speed, so it is a memory saving, not a speed-up.

Transcripts are cached in `work/_transcript_cache`, keyed on the file's content hash,
model, compute type, WhisperX version and a format version. Uploading the same file again
with the same model skips transcription; a different model or file transcribes afresh.
Changing audience or clip limits never re-transcribes, and still does not. Delete the
cache folder to force a fresh run.

## Storyboard and Pacing

Clips default to 6-15 seconds. Nomination aims for at least 3 separate clips: if the
model returns fewer, it adds sentence-bounded alternatives that pass the same editorial
review (never padding or relaxing limits). At least 2 short clips are normally needed
to reach the target length. Unavailable
model scores still require manual source review. New generated storyboards enforce a
30-60 second total (storyboards made under the earlier 60-90 second policy stay valid): narrated intro + selected speech + divider cards + ending.
The editor shows an estimated total; measured preview and final
export durations are checked too. Fitting preserves retained clip order, trims only to
complete-sentence ranges, and re-reviews changed ranges locally. It can drop kept clips,
but never adds unkept material or silence padding. Every change is reported.
Legacy exports are unchanged and retain their recorded duration policy.

A single clip needs enough speech to reach 30 seconds with its intro and ending:
about 20 seconds of speech plus a 3-second intro and 6-second ending is the minimum, so
a lone 15-second clip is too short. Narration can extend either card beyond its minimum hold.
Keep another reviewed clip, or raise the per-clip maximum and rerun nomination.
Never duplicate footage or weaken context checks merely to meet the duration target.

New storyboards start with a separate full-screen intro card. The audience-specific
hook is spoken using the audience's voice and rate before lecturer footage starts.
The 2-3 second intro control is a minimum hold, extended to fit the measured speech.
Every generated join uses a **Divider** frame with a short content-based bridge label over
the generated illustrations (alternating opening and ending images, mirrored on later
joins); drafts without generated media fall back to a frame from the following clip. The bridge text is spoken using the same
audience voice and rate as the intro and ending. Its 1-2 second control is a minimum
hold, extended to fit the narration. A single selected clip has no
between-clip transition. Changes to generated text/timing require regeneration so
narration and images cannot silently remain tied to older wording.
Original lecturer speech is retained, without
overlapping generated voices. Intro and divider durations shift all later captions.
Regenerate older storyboard drafts to use these defaults; existing exports are unchanged.

The ending contains one takeaway, an audience-appropriate reflection
question, editable source attribution and a short CTA. The source URL is in the post,
not a clickable video element. Text budgets and pixel fitting reject overflow rather
than silently truncating claims. Reading-speed budgets are rough safeguards, not a
substitute for watching the output on the target display.

New storyboards narrate the takeaway and CTA over the ending card. The 4-6 second
ending control is a minimum hold: measured speech plus a short margin extends it when
necessary, without speeding up or cutting off the voice. The measured compilation
must still fit 30-60 seconds; shorten the wording or adjust selected clips if it does
not. The reflection and source credit remain visual. Narration is included in the SRT
sidecar and its text, voice, rate and duration are recorded in the export timeline.

Audience profiles set wording, voice and speaking rate automatically. All three use
Edge TTS neural voices: Researcher `en-US-SteffanNeural` at -5%, General public
`en-US-AvaNeural` at +0%, Kids `en-US-AnaNeural` at -20%. Profiles record a nominal
words-per-minute rate (166, 175, 140) against a 175 reference; that figure is converted
to the percentage offset Edge expects. It is a relative setting, not a measured pace,
and different voices speak at different base speeds, so compare measured durations in
the export timeline rather than the nominal numbers.
There is no manual voice selector or voice cloning. The target audience determines
the voice for the intro, bridge frames and ending; original lecture audio is unchanged.
Changing audience requires re-nomination and a new storyboard preview and approval.
Audience profile version 3.0 retired the macOS Samantha and Daniel voices. Saved runs
on an older profile version are migrated on load: candidates and the storyboard draft
are cleared and must be regenerated. Previously exported videos do not change, and
their recorded voice and rate are still what the assessment checks them against.

All narration requires internet access for Edge TTS, not AWS. The Edge client is
unofficial and availability is not guaranteed, and this now applies to every audience
rather than Kids alone. Failures block narration explicitly; no substitute voice and no
silent fallback is used. Generated card text is sent to that service for all audiences.
Retired legacy profiles keep their macOS `say` voice so older drafts still render
locally; nothing falls back to `say` when a neural voice fails.
Missing speech synthesis or a missing voice fails explicitly instead of exporting a
silent card. Previously saved storyboards without narration settings stay silent.

Gemini receives fitted excerpts in order and the target audience, and generates
an audience-specific hook, bridges grounded in the following excerpt, ending synthesis,
reflection question and actionable CTA together. Hooks must not promise unsupported
content; bridges must not invent relationships between excerpts.
The configured Google text model is independent of the local Ollama nomination model.
A structured response is checked for source IDs,
word limits, readability budgets and total duration, with one correction attempt.
Failures are shown explicitly, without silently substituting metadata. Generation and
model provenance are retained in the exported storyboard. Source credit remains an
editable default; the model does not invent speaker attribution. Review the generated
CTA for relevance and safety, especially for Kids; audience styling is not a content-safety guarantee.

Supporting excerpts and the sections citing them are displayed, but reference validity
does not prove semantic support. Check surrounding source context yourself; do not
imply causation merely because two excerpts are adjacent. Local fitting may change
ranges, but Gemini cannot change them or grant human approval.
Regeneration clears old preview/approval state;
if rendering fails, generated text remains available for correction and another preview.

Preview is a full-length, maximum-640-pixel render using the same storyboard and
caption timeline as export. Changing clips, text, timings, placement or aspect ratio
invalidates its approval. Reviewer changes revoke approval but retain the preview.
Approval is a named local declaration, not authenticated authorship or publication
approval. `selection.json` records the storyboard, approval revision and measured
timeline; existing exports are never overwritten. Legacy Python callers can still
request the previous static-card assembly by omitting `storyboard`.

## Post-Generation Assessment

**5. Assessment** automatically measures the selected export and saves JSON and
Markdown reports. No final reviewer name, ratings or evidence form is required.
Checks include complete decoding, audio/video streams, duration, aspect ratio,
sidecar caption timing and reading speed, timeline consistency, narration metadata,
narration audio presence, integrated loudness and true peak. **Re-run checks** forces
fresh measurements; otherwise complete reports are reused only when the metric
version and all four artifact hashes match. Exports and their ZIPs are not modified.

Run the same checks from the project root:

```sh
python -m pipeline.assess exports/RUN_ID/EXPORT_ID --automatic
```

See [assessment metrics and limits](docs/post-generation-rubric.md) for thresholds.
Results are technical pass/fail counts, not a subjective quality score. Unavailable
checks remain explicit. These metrics cannot establish factual accuracy, audience
suitability, transcription correctness or publication suitability. The pre-export
storyboard approval step is unchanged. Older editorial reports and their CLI remain
available as legacy history; they are not mixed into the automatic result.
