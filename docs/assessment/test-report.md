# Brevio Studio: Test and Verification Report

Assessment edition | 29 October 2026 | Result: automated checks passed; release gates remain

## 1. Objective and Test Basis

This report records current executable verification of the local Brevio prototype and distinguishes it from earlier experiments, live-provider testing and human acceptance. The test basis is the requirements FR-01 to FR-12 and NFR-01 to NFR-08 in project-report.md, the inspected source tree and the existing Python/Playwright tests. No application behaviour was modified to obtain a passing result during documentation preparation.

The latest observed commit was dc1b1a57eddd7c0ff7092c1b68cafa9fe936adad. The working tree was not clean, so the attached evidence manifest hashes the relevant implementation and test files. Logs are retained unabridged in the documentation pack. The pack does not include credentials, full transcripts or source videos.

## 2. Environment

| Item | Observed value |
| --- | --- |
| OS / architecture | macOS 26.7.1, arm64 |
| Main interpreter | /opt/anaconda3/bin/python, Python 3.13.5 |
| Frontend tools | Node 26.12.0; npm 11.20.0; Vite 8.3.4 |
| API/runtime | FastAPI 0.141.1; Uvicorn 0.29.0 |
| Rendering | FFmpeg 9.0.2; Pillow 11.1.0 |
| Browser projects | Chromium desktop 1440x1000 and mobile 390x844 |
| Model/service conditions | Python suite denied network; provider credentials removed; dotenv disabled |
| Browser fixture | Temporary read-only API on 127.0.0.1:8796; current frontend build; existing real media |

The Python suite enabled RUN_MEDIA_TESTS=1. Model/provider interactions used existing test doubles where defined; real FFmpeg exercised synthetic media paths. This run did not download weights, contact paid providers or run a fresh lecture through live ASR/Ollama/Kokoro/cloud generation.

## 3. Executed Checks and Results

| Evidence ID | Check | Result | Retained evidence |
| --- | --- | --- | --- |
| E-01 | Complete Python unittest suite with FFmpeg integration enabled | 249 tests passed in 125.836 s; unittest reported OK | verification-python.log |
| E-02 | TypeScript project build and Vite production bundle | Passed | verification-build.log |
| E-03 | Configured Oxlint frontend checks | Passed | verification-lint.log |
| E-04 | Existing Playwright suite on desktop and mobile | 10 passed in 14.0 s | verification-browser.log; screenshot evidence in this pack |
| E-05 | Documentation/source snapshot | Generated inventory and SHA-256 hashes | evidence-manifest.json |

No failing test or skipped-test summary was reported in E-01 or E-04. Any library/resource warnings in the raw log remain visible and are not rewritten as test failures or suppressed from the evidence. Runtime figures are observations of these checks, not application processing-time benchmarks.

### 3.1 Commands

The Python command was executed on macOS with the existing sandbox-exec facility:

```bash
env -u GEMINI_API_KEY -u GOOGLE_API_KEY \
  -u XERO_PORTKEY_API_KEY -u PORTKEY_API_KEY \
  -u CLOUD_PRICING_FILE \
  PYTHON_DOTENV_DISABLED=1 RUN_MEDIA_TESTS=1 \
  /usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  /opt/anaconda3/bin/python -m unittest discover -s tests -v
```

Network denial applied to this Python process and its descendants. It is a test safety control, not a claim that the application normally works without all network services. On other operating systems, use an equivalent isolated test environment rather than assuming this macOS command exists.

```bash
npm --prefix frontend run build
npm --prefix frontend run lint
STUDIO_URL=http://127.0.0.1:8796 \
  frontend/node_modules/.bin/playwright test \
  --config frontend/playwright.config.ts --reporter=line \
  --output=work/assessment-browser-tests
```

The browser command requires the isolated server described below. Running it with an arbitrary server address is not equivalent to this controlled fixture setup.

### 3.2 Browser Isolation and Scenario Coverage

A temporary application root was created with one existing three-clip export, exports/d7d0b4b604/20261011-221858-152ae4, linked into an isolated fixture library. The saved compilation was also exposed as the fixture's local source. Current frontend assets were served from the new production build. Real server mutations were rejected by a read-only middleware; the workflow tests intercepted and fulfilled their expected mutations in the browser. Provider credentials and dotenv loading were disabled in that server process. Existing production sessions, approvals and exports were not rewritten.

This fixture proves playback and interaction against existing media, not that the source fixture is a newly analysed lecture. The temporary server was stopped after the checks. Browser output was directed to work/assessment-browser-tests so earlier screenshot evidence was not overwritten.

Each of the following five scenarios ran once per viewport:

1. Blueprint visibility on Clips and planned-versus-measured result on Studio.
2. Technical/structure checks, blueprint wording comparison and audience rubric guidance.
3. Real saved-media decoding, seeking, nonblank canvas sample, timeline navigation and mobile inspector.
4. Mocked source-to-export interaction, selection edits, disabled approval until name/attestation, and one expected generation/export action.
5. Source-page pipeline/privacy guidance and audience-specific voice information.

The tests also check horizontal overflow and browser page errors in their scenarios. This is not a full accessibility, cross-browser or device-hardware certification. The mobile project uses a simulated viewport/touch configuration, not a physical phone.

## 4. Coverage Matrix

| Requirement area | Representative current tests | Coverage limitation |
| --- | --- | --- |
| Intake/media access, FR-01 | StudioApiTests.test_stale_revision_and_invalid_upload; test_catalog_and_byte_range | No exhaustive hostile media corpus or upload-load test |
| Transcript caching, FR-02 | TranscriptCacheTests: content/model/compute cache keys, hit, corruption, model reuse | No new real ASR accuracy measurement |
| Evidence and ranking, FR-03 | Pipeline candidate, excerpt, sentence-boundary and editorial tests | Model judgement/semantic correctness not established |
| Selection and fit, FR-04/06 | AutoSelectTests; StoryboardTests fitting and duration cases | Synthetic fixtures do not cover every lecture or narration pace |
| Audiences/layout, FR-05/08 | CardLayoutTests, media and audience-profile tests | Human readability and audience suitability still require review |
| Blueprint/privacy, FR-07; NFR-02 | BlueprintTests; CloudPayloadTests.test_blueprint_request_carries_only_text_fields; test_storyboard_request_carries_only_text_fields | Field allowlists, not end-to-end traffic/retention auditing |
| Approval, FR-09 | API adapter contract, failed-generation invalidation, storyboard revision and tamper tests | Reviewer name is not authenticated; human review not performed by tests |
| Frozen export, FR-10 | Export asset hashes, narration reuse, versioned names and media tests | No destructive disk-failure/backup recovery exercise |
| Checks, FR-11 | StructureCheckTests; objective assessment tests; browser checks view | Technical/structural metrics are not factual or pedagogic evaluation |
| Batch safeguards, FR-12 | CloudWorkflowTests: cache tampering, failed-call reuse, request caps and SDK mocks | No fresh live model access, pricing or account-policy verification |
| API boundary, NFR-01/03 | Untrusted host/origin, missing token, file allowlist, symlink escape, stale revision, interrupted jobs | Not a penetration test or public-hosting security approval |
| UI, NFR-05/06 | 10 Playwright cases; TypeScript build; Oxlint | No independent user study, screen-reader audit or exhaustive navigation test |

## 5. Interpretation of Results

All executed automated checks passed for the captured snapshot. That supports the specific contracts asserted by those tests and the build configuration used. It does not establish complete branch coverage, absence of vulnerabilities, live model quality, correct provider billing or successful installation on another machine. No coverage percentage is claimed because no coverage instrument was run.

The FFmpeg tests exercise actual local rendering with controlled inputs. Narration/provider fixtures and saved-browser media must not be described as a fresh live end-to-end generation. Similarly, passing the application's tests does not mean every saved export passes its caption-speed check or is approved for publication.

Historical suite counts and experiments in the four-week plan are retained as development evidence. They are not combined with the 249 current tests into a larger total. The report does not treat earlier failing renderer tests as current failures when the present suite passes, nor does it claim this documentation work fixed those older failures.

## 6. Open Verification and Release Gates

| ID | Unverified area / impact | Required next test and acceptance evidence | Owner role |
| --- | --- | --- | --- |
| GAP-01 | Current provider access, actual spend and output quality | Permission-cleared, explicitly budgeted live run; ledger, invoice/usage evidence and human review | Operator + reviewer |
| GAP-02 | Fresh ASR and Kokoro behaviour on target machine | Real lecture transcription and offline narration for all three audiences; audio/wording inspection | Developer + reviewer |
| GAP-03 | Rights and cloud data governance | Source permissions, model/asset licence review, gateway retention/region/access approval | Content owner + institutional/gateway administrator |
| GAP-04 | Accessibility and usability | Keyboard/screen-reader/contrast review and observed representative user tasks | UX reviewer |
| GAP-05 | Performance and capacity | Timings by stage, warm/cold cache, CPU/RAM/disk peaks on permission-cleared short/long sources | Developer |
| GAP-06 | Deployment portability and recovery | Clean-machine setup; interrupted-job scenarios; backed-up export restore with hash/playback checks | Operator |
| GAP-07 | Adversarial security and dependencies | Dependency/SBOM review, hostile inputs, prompt-injection assessment and boundary testing | Security reviewer |
| GAP-08 | Educational benefit / time savings | Predefined comparative study with independent source-fidelity review and appropriate consent | Project team + facilitator |

These are verification gaps, not automatically confirmed defects. No assigned individual, completion date or facilitator acceptance is invented. Public deployment remains out of scope until authentication, isolation and operational controls are designed and tested.

## 7. Proposed Human Acceptance Protocol

For an authorised live run, record the source owner/permission, source hash, audience, model/configuration versions, spend cap and source URL before generation. Choose a representative source for each audience and both aspect ratios over the evaluation set. Record candidate and fitted clip ranges, cloud ledger outcome, measured runtime and output hashes.

An independent human reviewer should watch the complete preview against its source and record findings for source fidelity, context completeness, caption accuracy/readability, audio intelligibility, visual legibility, age suitability and rights. A misleading or unsupported claim, missing permission or unreadable essential text blocks publication even if technical checks pass. Record corrective action and the exact regenerated revision; never carry approval across changed content.

The facilitator must confirm whether these project-specific acceptance conditions meet the assessment expectations. This protocol is proposed; it was not performed by the automated browser checks.

## 8. Evidence Handling and Reproduction

Use the verification logs for exact command output and evidence-manifest.json for the hashed source/test/log inventory. Selected browser screenshots are included under evidence/ with an explicit distinction between mocked UI states and saved-media playback. Screenshots are supporting evidence, not proof of a new generated video.

To reproduce, use the captured source snapshot, compatible dependencies and the no-network test command; build/lint the frontend; create a read-only isolated library with a valid saved export; run the two Playwright projects; and compare results. Differences in dependencies, fixtures or native tools must be recorded rather than hidden by rerunning until a desired number appears.

The assessment documentation does not distribute source lectures, private configuration, gateway request bodies or raw work directories. A manifest hash of source code establishes identity, not ownership or a cryptographic signature by the team. Retain the original logs and obtain any required facilitator sign-off separately.

## Appendix: Browser Evidence

These screenshots were captured by the passing automated browser tests. Both use deliberately mocked text and state, not a new live AI output or a human-approved project.

![Desktop blueprint view: mocked three-clip planning scenario.](evidence/blueprint-desktop.png)

![Mobile checks view: mocked technical and structural assessment scenario.](evidence/checks-mobile.png)