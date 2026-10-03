# Four-week improvement plan

**Horizon:** 21 Sep – 18 Oct 2026  
**Goal:** A reliable 3-minute demo. Models nominate clips; a human keeps 3–5 on-topic ones; one compilation exports. Do not build a second product this month.

Workstreams: **Pipeline** · **Eval** · **UX / demo** · **Report**

## Baseline video

- Selected on 26 Sep 2026: https://www.youtube.com/watch?v=eHJnEHyyN1Y
- YouTube video ID: `eHJnEHyyN1Y`. Use this source for the first baseline evaluation and demo rehearsal.
- Local source: `work/baseline/baseline1.mp4` (user supplied).
- Verified media: 919.574 seconds, 1920x1080 VP9 video, 48 kHz Opus audio; 94,710,225 bytes.
- Source SHA-256: `e5b9c3dff1973d86e4e65765293df0687aa785bac41af30d5ff438eb04e1b1bf`.
- Title, channel and reuse permissions remain unverified. Baseline run `1779018321` has reached preview review; no approved compilation has been recorded.
- Initial ASR configuration: existing WhisperX `small`, CPU/float32 on this Mac, batch size 8, automatic language detection. Local nomination model available: `qwen2.5:latest`.
- Upload a lawfully obtained local copy through the existing app and set the source URL to the link above. YouTube ingestion is not implemented.
- Before the first run, record the source file hash, topic, learning objectives, model/settings and keep rubric (on-topic, standalone, one idea).
- Save the transcript and reuse the same source, transcript and settings for comparisons, changing only the factor under evaluation.
- Record candidate count, keep-rate, parse failures, window-fallback rate and wall time per stage; manually check caption alignment and the final compilation.
- Keep source video and generated media out of git. Confirm reuse permissions before redistributing source footage or clips.

### First baseline results (26 Sep)

Existing pipeline, unchanged: WhisperX `small` -> `qwen2.5:latest` -> `all-MiniLM-L6-v2` -> review previews. Topic: counter-conventional entrepreneurial mindsets. Five transcript-derived objectives are an assistant draft, pending human confirmation; clip range 20-45 seconds, original aspect ratio.

Local run artifacts (gitignored): [settings](../work/1779018321/settings.json), [metrics](../work/1779018321/baseline.json), [transcript](../work/1779018321/transcript.json), [SRT](../work/1779018321/transcript.srt), [raw candidates](../work/1779018321/candidates.json), [ranking](../work/1779018321/ranked.json), [review state](../work/1779018321/review.json). These links require the local run outputs and will not resolve in a source-only clone.

| Stage / measure | Observed result |
|---|---|
| Transcription | 120.54 s; English; 174 segments, all with word timings |
| Nomination | 111.53 s; 13 candidates |
| Ranking | 12.03 s; top 10 retained |
| Preview generation | 54.53 s; 10 clips |
| Total measured stages | 298.63 s; excludes manual review and export |
| Fallback candidates | 0 / 13 (0%); not a per-request fallback metric |
| Requested-duration violations | 3 / 13: c6 13.844 s, c9 49.250 s, c11 49.668 s |
| Parse failures | Unavailable: existing nomination code does not expose a count |
| Keep-rate / export time | Pending human selection / not run |

Checks passed: all transcript and candidate ranges finite and within the source duration; transcript starts ordered; candidate IDs unique; ranking descending; all preview durations within 0.25 seconds of their nominated ranges. Speech alignment and visual quality are not yet manually verified. A TorchCodec shared-library warning appeared, but transcription and word timing generation completed.

### Review queue

Scores are embedding similarity, not quality probabilities. Notes below compare generated metadata with transcript excerpts; they are not human keep decisions.

| Preview | Source seconds | Score | Initial review note |
|---|---|---|---|
| [c4](../work/1779018321/previews/c4.mp4) | 395.748-421.739 | 0.6779 | Narrow-market principle; Nike takeaway is not supported within this range. |
| [c9](../work/1779018321/previews/c9.mp4) | 644.394-693.644 | 0.4580 | Too long; mixes customer deposits with next topic; borrowing title mismatches most speech. |
| [c6](../work/1779018321/previews/c6.mp4) | 247.388-261.232 | 0.4190 | Too short; yes-to-customers title mismatches problem-first speech. |
| [c3](../work/1779018321/previews/c3.mp4) | 256.050-291.300 | 0.4043 | Product-change examples; surgical-forceps takeaway is outside this range. |
| [c10](../work/1779018321/previews/c10.mp4) | 806.742-839.878 | 0.3226 | Omits the speaker's later ethical/legal caveat; reject or rework context. |
| [c1](../work/1779018321/previews/c1.mp4) | 68.920-92.658 | 0.3202 | General introduction; Lynda example is outside this range. |
| [c11](../work/1779018321/previews/c11.mp4) | 469.024-518.692 | 0.2516 | Too long; Nike niche-market example needs matching metadata and tighter ending. |
| [c7](../work/1779018321/previews/c7.mp4) | 512.207-534.261 | 0.2431 | Nike expansion; ends with transition to the next topic. |
| [c13](../work/1779018321/previews/c13.mp4) | 630.469-662.287 | 0.2360 | Customer-deposit funding; review whether opening has enough context. |
| [c8](../work/1779018321/previews/c8.mp4) | 593.367-622.907 | 0.2205 | Tesla customer targeting/sales; cuts before explicit cash-before-production explanation. |

### Immediate next steps

1. Confirm or revise the draft topic/objectives, then review the previews against on-topic, standalone, one-idea criteria. Do not automatically keep the top scores.
2. Completed: preserve the raw run, add duration regressions and enforce limits after normalization. A saved-candidate replay retains 10 of 13 candidates with zero duration violations; see the check below.
3. Excerpt-only metadata drafts and quote checks are implemented (comparison below). Human grounding/context review remains necessary, especially for the regulation caveat. Check apparent ASR errors such as "writing the float" and "bag borrow" against the audio before editing captions.
4. Record human keep decisions and any range/title edits; approve 3-5 clips before exporting one compilation. Measure export time and inspect caption sync, cards and final output.
5. Complete the saved-transcript smoke workflow, detailed export progress and dependency pinning; add the remaining evaluation lectures after this first baseline.

### Revised shortlist ready for approval (26 Sep)

Four assistant-proposed edits are saved in [shortlist-v1](../work/8f29052abf/shortlist-v1.json), with excerpts and preview mappings in the [shortlist review record](../work/8f29052abf/shortlist-v1/review.json). These are new proposal IDs, not the earlier candidate IDs. No clip is human-approved, and no final compilation has been exported.

| Proposal / preview | Source range | Length | Revised content |
|---|---|---|---|
| [s1](../work/8f29052abf/shortlist-v1/s1.mp4) | 161.755-205.562 s | 43.807 s | Saying Yes to New Customer Needs: retains the customer request and now includes the entrepreneur's delivery despite unfamiliar technology. |
| [s2](../work/8f29052abf/shortlist-v1/s2.mp4) | 280.157-302.984 s | 22.827 s | Start with Problems, Not Products: Coke variants followed by the problem-first conclusion. |
| [s3](../work/8f29052abf/shortlist-v1/s3.mp4) | 469.024-500.267 s | 31.243 s | Design for a Specific Customer: restores the elite-distance-runner setup before shoe design details. |
| [s4](../work/8f29052abf/shortlist-v1/s4.mp4) | 617.745-662.287 s | 44.542 s | Customer Payments Fund Development: restores the Tesla Roadster setup before the customer-deposit examples. |

Speech totals 142.419 seconds. Proposed packaging is a 1.5-second intro and 3-second end card, approximately 146.919 seconds overall before encoder rounding. The regulation clip is excluded; this shortlist does not cover every lecture objective.

Checks passed: source checksum matches; four unique, ordered, non-overlapping ranges; all boundaries exist in the transcript; each duration is within 20-45 seconds. All four previews have audio and measured durations within 0.25 seconds of their ranges. Rendering took 20.24 seconds. No perceptual audio/video or caption-alignment verification is claimed.

Review s1's opening reference to the earlier business for standalone context. Check the apparent ASR error at source 623.167-625.028 seconds in s4 against the audio before final captions. Transcript wording has not been silently corrected, and numeric claims remain attributed to the speaker rather than independently verified.

Next gate: the user reviews these previews and explicitly approves or requests edits to s1-s4 and the draft objectives/labels. Approval must be tied to this proposal's exact ranges and labels, not copied from a previous run. Caption correction and final-artifact inspection remain separate from selection approval.

### Metadata comparison (26 Sep)

Run `8f29052abf` reuses the duration-valid baseline ranges and transcript. Each clip is labeled independently using only its final excerpt, without the original label, objectives or surrounding lecture. Nonempty title/takeaway strings and a verbatim supporting quote are required. Failed responses get a neutral timestamp title and empty takeaway/quote, never the old unsupported claims. Matching a quote does not prove that a summary follows from it or that a clip is standalone; all labels remain drafts.

Editing a range clears its old quote and marks metadata for renewed review. Human title/takeaway edits are retained. Approval rules are unchanged.

- Metadata generation: 42.80 s for 10 clips; 9 drafts, 1 unavailable. Reranking: 12.99 s. No new ASR or nomination.
- Validation: 17 unit tests pass; all duration and supporting-quote checks pass. Scores for shared ranges match the original baseline. Ten preview durations verified; three missing previews rendered and seven existing previews reused.
- Local artifacts: [metadata candidates](../work/8f29052abf/candidates.json), [ranking](../work/8f29052abf/ranked.json), [metrics](../work/8f29052abf/baseline.json), [review state](../work/8f29052abf/review.json). Original artifacts are unchanged. Use the new IDs below.
- Future nominations incur one extra model call per candidate. Quote matching cannot detect all unsupported implications or omitted context.

The following wording is an assistant review proposal, not an approved selection or an overwrite of model output. Check the audio before adopting it.

| New ID / preview | Original ID | Proposed title / takeaway | Remaining review issue |
|---|---|---|---|
| [c4](../work/1779018321/previews/c4.mp4) | c4 | Think Narrow, Not Broad: The speaker contrasts entrepreneurs' narrow target markets with large companies' preference for big markets. | Metadata unavailable; opening refers back to John. |
| [c3](../work/1779018321/previews/c3.mp4) | c3 | Questioning Product Changes: The speaker questions whether detergent speckles or Coke variants count as innovation. | Draft overstates formula changes; ending lacks a conclusion. |
| [c8](../work/1779018321/previews/c10.mp4) | c10 | Regulatory Approval and New Ventures: The speaker questions whether regulators would have approved Uber's initial proposal. | Rework or reject: later ethical/legal caveat is omitted. Do not approve as-is. |
| [c1](../work/1779018321/previews/c1.mp4) | c1 | Counter-Conventional Mindsets: The speaker contrasts six entrepreneurial mindsets with corporate and business-school practices. | General introduction, not an actionable lesson. |
| [c6](../work/1779018321/previews/c7.mp4) | c7 | From Running to Other Sports: The speaker describes Nike expanding to tennis and basketball. | Draft implies international-market targeting; opening depends on context and ending changes topic. |
| [c10](../work/1779018321/previews/c13.mp4) | c13 | Customer Deposits Fund Development: The speaker describes deposits financing engineering, tooling and factory preparation. | Check the opening Roadster reference for standalone context. |
| [c7](../work/1779018321/previews/c8.mp4) | c8 | Tesla's Early Customers: The speaker describes a road show aimed at wealthy, environmentally concerned early adopters. | Does not include later cash-before-production explanation. |
| [c9](../work/8f29052abf/previews/c9.mp4) | c12 | Applying Entrepreneurial Mindsets: The speaker asks which mindsets listeners use, can learn or teach, and could apply to a challenge. | Depends on knowing the earlier mindsets. |
| [c2](../work/8f29052abf/previews/c2.mp4) | c2 | A Customer's New Request: A customer requests satellite broadcasts of training events to stores across Brazil. | Ends before the entrepreneur's response. |
| [c5](../work/8f29052abf/previews/c5.mp4) | c5 | Shoe Design for Runners: The speaker proposes stability, cushioning and lighter weight to improve running shoes. | Opening depends on earlier context. |

Next gate: confirm objectives, review or revise ranges and labels, then explicitly approve 3-5 clips. Keep-rate and final export remain pending.

### Duration enforcement check (26 Sep)

The normalizer now validates the final millisecond-rounded, segment-snapped range against the source duration and requested minimum/maximum. Out-of-range clips are rejected rather than trimmed through speech. The same normalization applies to Ollama and fallback-window candidates; fewer candidates may survive.

- Verification: 13 pipeline unit tests pass, including baseline violations, snapping expansion, exact duration boundaries, serialized timestamp limits and existing approval/export safeguards.
- [Replay report](../work/1779018321/duration-check-4421b0701e.json): 13 saved candidates in, 10 retained, zero duration violations. Rejected original IDs: `c6`, `c9`, `c11`.
- This is a normalization-only replay, not a new model run or reranking. The original transcript, candidates, ranking and previews remain unchanged.
- Replay candidate IDs are regenerated. Do not reuse old IDs, preview mappings or approvals with these candidates. The review queue above still refers to the original baseline.
- Topic/objectives confirmation, title/excerpt grounding, caption review and human approval of 3-5 clips remain pending. No compilation was exported.

---

## Week 1 — Stabilize (21–27 Sep)

- Pin Python/deps. A second laptop must run from the README.
- Smoke test from a saved `transcript.json` (no WhisperX): nominate → rank → export.
- Show export progress: cut / intro / concat / captions / zip.
- Pick 4–6 lectures and a keep rubric: on-topic, standalone, one idea.

## Week 2 — Measure clip quality (28 Sep – 4 Oct)

- Ablation: topic only vs topic + objectives (same transcript).
- Ablation: rank with excerpt vs title + takeaway only.
- Show which learning objective produced the score.
- Drop clips that overlap a higher-scored clip by more than 60% time.
- Log parse failures, window-fallback rate, time per stage.

## Week 3 — Polish the output (5–11 Oct)

- Speed caption burn; keep captions on the first spoken second after intro.
- Re-nominate without re-transcribing.
- Lock a 2–3 min demo script on one lecture.
- Align the report with current code: aspect ratio, one compilation (not per-clip shorts).

## Week 4 — Freeze and write up (12–18 Oct)

- Two rehearsals. Time ASR on the demo machine; start from a saved transcript if it is slow.
- Bug-fix only: keep-gate, caption sync, Review end-time applied on export.
- Write method, eval table, limitations (local tools, copyright, no auto-post).
- GitHub tag, source only — no lecture mp4s.

---

## Out of scope

YouTube download, auto-publish, BGM, face tracking, wiring `package.py` back to per-clip shorts, swapping demo ASR to Qwen3-ASR.

## Week-4 acceptance

- Non-author can set up and export one compilation from the README.
- Cannot export with 0 or 6+ kept clips. Review end-time edits appear in the file.
- Captions start with clip 1 speech; intro/end cards have none.
- Eval table exists: keep-rate, fallback rate, wall time per stage.
- Talk track: models suggest, human decides topic fit, packaging waits for approval.
