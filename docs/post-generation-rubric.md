# Post-Generation Assessment

## Automatic Technical Checks

Metrics version 2.0 is the default final assessment. Selecting an export in
**5. Assessment** runs the checks automatically, without a reviewer or subjective
ratings. The report shows measured values, explicit thresholds and pass/fail counts.
Unavailable measurements are not treated as passes. A failed check makes the
technical result failed; otherwise unavailable checks make it incomplete.

| Check | Threshold or requirement |
|---|---|
| Export metadata | Readable selection JSON object |
| Audio/video streams | Both present |
| Full media decode | FFmpeg completes without decoder errors |
| Duration | 30-60 seconds for new videos; recorded 60-90 and 45-120 policies for earlier exports |
| Aspect ratio | Explicit target within 0.02 ratio units; Original is not applicable |
| SRT parsing | Valid, nonempty sidecar |
| Cue timing | Positive, ordered, nonempty cues within video; 0.05s overlap and 0.1s end tolerance |
| Reading speed | At most 20 characters/second, including spaces after whitespace normalization |
| Timeline consistency | Clip IDs/order match selection; contiguous within 0.1s; clip lengths within 0.15s; total within 0.2s |
| Narration metadata | Enabled cards have expected text, saved voice/rate and sufficient hold time |
| Narration sidecar | Text matches; start within 0.05s and end within 0.1s of recorded narration |
| Narration audio | Peak above -60 dBFS in every narrated interval, measured on decoded 8 kHz mono audio |
| Generated media | Exported inventory hashes match approved assets; narration stems checked separately from music |
| Integrated loudness | -24 to -14 LUFS, measured by FFmpeg loudnorm input statistics |
| True peak | At most -1 dBTP |

These are configured delivery targets, not universal quality standards. The result
does not score factual correctness, semantic source fidelity, educational value or
audience suitability. Sidecar checks do not verify transcription accuracy, burned-in
contrast or text placement. Audible intervals do not prove the spoken words or voice
identity. Publication suitability stays **not assessed**, even if all technical
checks pass. The existing pre-export storyboard approval remains unchanged.

For Researcher, General public and Kids (ages 8-12), assess writing and visual
treatment. Review every automatic trim/removal against surrounding source context.
Generated images/B-roll must be marked as illustrations, never invented experimental
evidence, data, people or events. Overlays must not conceal important source diagrams.
Listen to the final mix: stem audibility does not prove speech is intelligible under
music. Check for unwanted vocals, distracting effects and redistribution permissions.

Reports are saved under `assessments/ASSESSMENT_ID/assessment.json` and
`assessment.md`. SHA-256 hashes bind each report to the MP4, SRT, post and selection.
Complete measurements are reused only for matching hashes and metric version.
**Re-run checks** or `--force` creates a fresh report. Changes during measurement
reject the report. No exports or existing ZIPs are overwritten.

```sh
python -m pipeline.assess exports/RUN_ID/EXPORT_ID --automatic
python -m pipeline.assess exports/RUN_ID/EXPORT_ID --automatic --force
```

## Legacy Editorial Rubric

Version 1.1. Assess the rendered compilation, caption sidecar and published text
against the source lecture and the audience saved with the export. This is an editorial
rubric, not a calibrated engagement predictor or the candidate relevance score.

## Rating Anchors

Use integer scores: 0 unacceptable, 1 major issues, 2 needs revision,
3 acceptable, 4 strong. Use `null` when evidence is insufficient. Do not substitute
an automatic measurement for an unperformed content, visual or listening review.

| Dimension | Weight | 0 | 1 | 2 | 3 | 4 |
|---|---:|---|---|---|---|---|
| Source fidelity | 25% | Central claim contradicts the source or a cut reverses its meaning. | Major unsupported claim or essential caveat omitted. | Broadly faithful but an overgeneralization or contextual qualification needs correction. | Claims match the selected source and essential caveats survive; attribution or context can be tightened. | Claims, scope, attribution and caveats checked against surrounding source context; no material discrepancy. |
| Audience fit and learning value | 25% | False central concept or inaccessible to the intended audience. | Major prerequisite mismatch or no usable explanation. | Some useful explanation, but depth, prerequisite context or example-to-principle connections need revision. | Clear explanations/examples with suitable depth and sufficient context for the exported audience. | Especially well-matched depth, concrete application and accurate synthesis for that audience. |
| Standalone coherence | 20% | Unintelligible or materially misleading sequence. | Mid-sentence cuts or missing essential setup/conclusion. | Understandable excerpts, but unresolved references, abrupt transitions or incomplete examples. | Complete ideas, enough setup and sensible order; only minor flow issues. | Self-contained opening, connected progression and a satisfying conclusion without redundancy. |
| Captions | 15% | Absent, unusable or materially wrong. | Frequent errors, unreadable text or severe timing mismatch. | Local wording, timing, wrapping or reading-speed problems need correction. | Accurate and readable on the target display; no material timing or coverage issue. | Terminology verified, clean readable segmentation, no loss/occlusion and consistently close speech alignment. |
| Audio and visuals | 15% | Playback fails or essential material cannot be heard/seen. | Major clipping, obstruction, illegibility or disruptive edit defects. | Watchable, but loudness, transitions, framing or card reading time needs adjustment. | Clear speech, suitable loudness, legible cards and unobstructed visuals throughout. | Consistent polished sound, framing, pacing and transitions across intended viewing formats. |

### Storyboard Review

Use the existing dimensions, without an extra score or relaxed publication gate:

- **Source fidelity:** the opening promises only what the selected excerpts deliver;
	transition labels do not invent causal links; the ending preserves qualifications.
- **Audience fit:** opening wording and the reflection question suit the saved audience.
- **Standalone coherence:** the first thought is complete, joins have a defensible
	relationship, and the ending resolves the opening without introducing a new claim.
- **Captions:** overlays never cover captions; inserted dividers do not shift speech alignment.
- **Audio and visuals:** opening and transition labels avoid essential slide content
	and faces; the end screen is readable; joins preserve words and do not overlap voices.

Record timestamps at the opening, every join and the ending. Storyboard approval
only authorizes rendering its reviewed revision; the final full-resolution export
still needs visual, listening and source-context review before publication.

## Evidence and Calculation

Audience comes from the export, never the current Input dropdown. New exports store
the audience, profile version, selection goals, inferred lecture concepts, duration
limits and editorial scoring version in `selection.json`. Old exports remain
audience unknown; do not infer an audience from the current UI. Saved version 1.0
reviews retain their original Learning value label and ratings.

Beginners need definitions and prerequisite context; students need explanations and
worked examples; professionals need applications and trade-offs; advanced learners
need mechanisms and evidence; general audiences need accessible, concrete relevance.
Fidelity, caption and audiovisual standards are not relaxed for any audience.

Every scored dimension requires evidence: cite source and output time ranges,
specific claims, observed defects, or a measurement report. The evaluator supplies
the ratings; the scorer validates and calculates them, it does not watch the video.
Technical evidence may be recorded under an unassessed dimension.

Weighted points = dimension weight multiplied by rating divided by 4.
The overall score is the sum of weighted points, out of 100, only when all five
dimensions are assessed. Otherwise report earned points and assessed weight
separately. Never normalize the assessed subset into a headline score.

Useful diagnostic evidence includes caption word coverage, cue overlaps,
speech-alignment offsets, complete video decoding and loudness measurements.
ASR agreement is not factual accuracy. A -16 LUFS integrated / at most -1.5 dBTP
target is a configurable delivery preference, not a universal platform requirement.
Readability and sound quality require viewing and listening to the final artifact.

## Publication Gates

All dimensions must score at least 3, and all four gates must explicitly pass:

- `claims_supported`: published claims are supported by the source and caveats retained.
- `cuts_preserve_context`: cuts and sequence do not omit essential setup or change meaning.
- `captions_readable`: final burned-in captions are readable and do not obscure essential content.
- `human_final_review`: a named human has watched/listened to the final video and reviewed its accompanying text.

Gate statuses are `pass`, `fail` or `not_assessed`, with evidence for pass/fail.
A failed or unassessed gate blocks approval regardless of total score. An assistant
cannot attest human final review. This is a local reviewer declaration, not an
authenticated signature or automatic publishing permission.

## Legacy Manual Workflow

The former final-assessment rating form is no longer in the app. Older editorial
reports are preserved and can still be generated with the legacy CLI below. They
are separate from automated technical reports and do not change their results.

## Run an Assessment

From the project root, create a review template bound to the current export:

```sh
python -m pipeline.assess exports/RUN_ID/EXPORT_ID review.json --template
```

Complete the reviewer name, ratings and evidence using these anchors, then run:

```sh
python -m pipeline.assess exports/RUN_ID/EXPORT_ID review.json
```

Reports are saved as `assessments/ASSESSMENT_ID/assessment.json` and
`assessment.md` inside the export directory, alongside the input `review.json`.
Every assessment gets a new directory. Existing video, captions, text, selection
and ZIP are untouched. Assessment reports are not inserted into the original ZIP.

SHA-256 hashes bind the review to the video, caption sidecar, post and selection.
Missing or changed hashes reject the review. If any of these files changes, create
a new template and reassess; a previously saved report does not approve new content.
These legacy commands retain editorial scoring and artifact validation. The Gradio
tab uses the automatic metrics described above instead.

## Candidate Scores Are Separate

Before export, the audience workflow uses model-estimated 0-4 ratings weighted as
audience suitability 40%, standalone completeness 35%, educational value 25%.
Every dimension includes an explanation and a verbatim quote anchored to a selected
transcript segment. These weights are provisional editorial choices, not calibrated
probabilities of quality or engagement. No embedding score is presented as a quality
percentage. Legacy callers without an audience can still use similarity ranking.

Sentence repair can extend at most two adjacent segments on each side. Semantic
review can request one further nearby extension and must review the resulting range
again. All repairs obey the chosen duration limits; neither the transcript nor the
speaker's words are rewritten. Missing punctuation may conservatively reject an
otherwise usable candidate. Clips identified as incomplete or context-changing are
excluded regardless of rating. Model failures remain unscored and require manual
review, rather than receiving an invented score. Overlapping and near-identical
excerpts are suppressed after ranking; this is not a guarantee of semantic diversity.

Changing a clip range clears its score and supporting evidence. Human range edits
must stay within the chosen duration budget. Model judgments and exact quote checks
cannot establish semantic support or factual truth; publication still requires the
separate final-artifact review above.