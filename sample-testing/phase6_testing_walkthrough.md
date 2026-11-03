# Phase 6 Testing Walkthrough

## Demonstration order

1. Run `python -m pytest -q` and show the test summary.
2. Run `python demo.py`.
3. Show `demo_output/qa_report_pass.json`: all synthetic checks pass, then the recorded educator approval returns `approved`.
4. Show `demo_output/qa_report_blocked.json`: an unsupported claim and missing context produce critical failures and status `blocked`.
5. Show `demo_output/reliability_demo.json`: 9 successful jobs from 10 attempts equals 90%, the proposed processing reliability target.
6. Explain that real video, caption and learner measurements still need integration and ethics-approved evidence.

## Suggested explanation

“My Phase 6 tests evaluate whether the educational teaser can safely pass the quality gate. I wrote checks for the approved blueprint identity and hash, authorized source, 60-to-90-second duration, claim timestamps, source support, context, the required five-part structure, captions, audio-video sync, source link and learner evidence. Critical failures block release regardless of the overall score.”

“The pytest suite checks both successful and failing cases, including duration boundaries, mismatched metadata, altered blueprint content, unsupported claims, incomplete captions and learner results below the target. The demo uses synthetic JSON so I can demonstrate the gate behavior without presenting mock values as real evaluation results.”

“When all checks pass, the status remains pending until an educator is recorded as the reviewer. A critical failure produces blocked; another failed target produces revise. The next step is to connect these checks to the real Phase 1-to-5 contracts and media measurements, then run the ethics-approved learner evaluation and document actual evidence.”

## Test evidence to save for the project

For real runs, retain the test run date, code version, blueprint ID/version/hash, input file references, expected and observed results, severity, failure codes, retest outcome, learner sample/method and educator decision. Keep learner data anonymized and do not store children's faces or voices for evaluation.
