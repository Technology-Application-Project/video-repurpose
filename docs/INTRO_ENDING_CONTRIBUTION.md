# KQ3 individual contribution: Audience-aware intro and ending review

## Problem and objective
Brevio Studio already prompts its cloud model to create audience-specific opening and ending cards. The team's existing `pipeline/storyboard.py` imposes word limits, question-hook requirements and ending narration budgets. The new work **does not duplicate or replace that generator**. It adds a standalone, offline quality inspection and option-comparison layer to help reviewers distinguish stronger candidate copy from obvious wording problems.

## What I implemented
- `pipeline/intro_ending_review.py` implements `review_intro_ending()` and `compare_opening_options()`.
- Checks opening title (<=7 words), subtitle (<=6), takeaway (<=16), ending question (<=12), and CTA (<=8).
- For General public and Kids, requires an opening question. Warns about generic opening phrases.
- Flags missing/unknown supporting clip IDs and weak lexical match between title/subtitle/takeaway and selected excerpts.
- Validates fixed CTA when there is no source URL and rejects generic CTAs when a URL is present.
- Warns when the ending duration is outside 4–6 seconds or spoken copy is too dense (>28 words or >5 words/sec).
- Ranks externally supplied candidate storyboards by fewer rule warnings, then evidence overlap, then their original order. It never generates new cloud content, edits a storyboard or approves an export.
- Added 14 independent automated tests with examples covering correct content, weak support, audience mismatch, overlong titles, pacing and option ranking.

## Demonstration on Windows (feature branch)
With the new files uploaded into `pipeline/` and `tests/` on `rahul-update`, switch to your branch locally to run:

```bat
cd %USERPROFILE%\Desktop\video-repurpose
git status
git fetch origin
git switch rahul-update
git pull --ff-only origin rahul-update
.venv\Scripts\activate
python -m unittest discover -s tests -p "test_intro_ending_review.py" -v
```

Alternatively, without importing app.py or running cloud generation:

```bat
python -m pipeline.intro_ending_demo
```

The demo prints a JSON comparison from fixed examples. It makes **no cloud or paid API calls**.

## Suggested presentation structure (3 minutes)
1. Show `pipeline/storyboard.py` existing hook and ending constraints to demonstrate understanding of the project baseline.
2. Explain why audience-tailored intros and endings need objective pre-review checks and coherent pacing.
3. Open `intro_ending_review.py` and describe the `review_intro_ending` input/output: storyboard + audience + clip-id-to-excerpt map -> warnings and candidate checks.
4. Run `python -m pipeline.intro_ending_demo` and explain the contrasting candidate outputs.
5. Run 14 tests and show the report; explain that warning count is *not* viewer retention and lexical overlap is *not* a proof of truth.
6. Show your `rahul-update` commits. Explain that the team has not merged the changes into `main` and full UI integration is future work.

## Scope and limitations
The code is a standalone review aid. The Brevio Studio UI does not yet display these warnings, and the live storyboard pipeline does not yet invoke them. Claim only offline review/test functionality until your team accepts and tests an integration. Do not imply experimentally validated retention gains, semantic entailment, or improved model output from this code alone.

## GitHub upload, no change to main
- Switch GitHub branch selector to `rahul-update`.
- Upload `intro_ending_review.py` and `intro_ending_demo.py` into `pipeline/`.
- Upload `test_intro_ending_review.py` into `tests/`.
- Upload `INTRO_ENDING_CONTRIBUTION.md` into `docs/`.
- Commit to `rahul-update`. Do not merge or overwrite original `storyboard.py`.
