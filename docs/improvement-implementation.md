# Improvement Implementation

## Scope

Audience-aware highlights and narrated Q&A, local-first synthetic question speech,
and final-render review. Preserve original answer audio and source context.
Publication analytics, cloud TTS, cloning, translation, arbitrary timelines and
automatic posting are deferred.

## Progress

- [x] Regression suite for actual-excerpt ranking and fallback provenance.
- [x] Finite, in-bounds edited ranges and rejection of stale candidate IDs.
- [x] Selection approval tied to source, transcript, topic, objectives and ordered edits.
- [x] Review edits invalidate approval; export cannot self-approve live rows.
- [x] New transcripts clear old review rows. Edited ranges do not retain stale scores.
- [x] Versioned exports staged before publication; prior exports are never deleted.
- [x] Post-normalization duration limits and excerpt-only metadata drafts with verbatim quote checks, neutral failure labels and range-edit evidence invalidation.
- [ ] Audience brief independent of transcription; result caching by source/settings.
- [ ] Evidence-backed content-fit and engagement rubrics, objective coverage and overlap handling.
- [ ] Editable highlights and Q&A storyboards with duration budgets.
- [ ] Local TTS feasibility/license gate, question generation and pair approval.
- [ ] Shared measured timeline, narration, caption rendering and loudness normalization.
- [ ] Render-quality report, final-artifact approval and evaluation on rights-cleared lectures.

This is an incremental migration. Selection revisions currently cover review content,
not packaging settings or a final-artifact hash. Generated titles/quotes are no longer
used to inflate relevance. Titles and takeaways are regenerated from each final
excerpt; supporting quotes are checked for a verbatim match. This does not establish
factual support or context completeness, which still require human review.
There is no engagement score or narrated Q&A in the current application yet.

The audience workflow now extracts draft lecture concepts with validated source
references, repairs sentence boundaries within the duration budget, and requests a
context/completeness review before generating final metadata. Candidate estimates
weight audience fit 40%, completeness 35%, and educational value 25%. Ratings require
verbatim source evidence; unavailable reviews stay unscored. Overlap suppression is
implemented, but model judgment and audience suitability still need human evaluation.
This is not an engagement score. Audience/profile versions, lecture concepts and
duration settings are retained in export metadata. Rubric 1.1 judges learning value
against that exported audience; legacy exports remain audience unknown.

A [post-generation rubric](post-generation-rubric.md), available in the Export tab's technical check
and through the CLI, now calculates
weighted scores from evidence-backed reviewer ratings. Reports are versioned and
bound to hashes of the final video, captions, post and selection. Missing dimensions
remain unassessed, and approval requires acceptable ratings plus explicit gates and
human final review. The tab loads existing scores, supports new human reviews and
provides report downloads. This does not provide automatic perceptual assessment.

## Current Export Contract

Successful files live under `exports/{run_id}/{export_id}/`, retaining the familiar
`compilation.mp4`, `captions.srt`, `post.txt`, `selection.json` and `compilation.zip`.
Temporary delivery files are cleaned after failures. Internal raw cuts and assembly
artifacts live under `work/{run_id}/exports/{export_id}/` for isolation and diagnosis.
Existing exports are left untouched. Public selections omit local preview paths.

## Verification

Run from the project root with the application dependencies installed:

```sh
python -m unittest discover -s tests -p 'test_pipeline.py'
RUN_MEDIA_TESTS=1 python -m unittest discover -s tests -p 'test_media.py'
python -m compileall -q app.py pipeline tests
```

Default unit tests mock embeddings, rendering and service calls; they do not download
models or invoke WhisperX/Ollama. The optional media test generates temporary synthetic
audio/video and runs real ffmpeg export with captions. It verifies basic export timing
and output presence, not speech alignment, perceptual quality or factual correctness.

## Next Gates

1. Verify a local voice and redistribution terms on the target Mac before selecting a TTS backend.
2. Add validated audience/profile contracts and source/configuration caching; keep model caches.
3. Add anchored per-dimension scores with evidence and unavailable states, never viral probabilities.
4. Generate supported questions from approved answers, then let the reviewer edit/reorder pairs.
5. Render from one versioned storyboard; measure speech duration before caption placement.
6. Require final rendered-video review, compare with the baseline and reconcile legacy documentation.