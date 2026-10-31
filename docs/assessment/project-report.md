# Brevio Studio: Comprehensive Project Report

Assessment edition | 29 October 2026 | Local single-user prototype

Team: Hari Chandana Sai Achalla; Nirmit Hasmukhbhai Badarukhiya; Spring Dai; Duc Ha; Pooja Kantamneni; Rahul Kumar.

## Executive Summary

Brevio Studio converts a locally supplied lecture into a short, audience-aware video compilation. It combines local transcription and source-grounded clip selection with cloud-assisted story planning, local narration, illustrated cards, captions and music. A named person must approve the actual rendered preview before export. The current target is a measured 30-60 seconds for Researcher, General public or Kids audiences. The system exports files; it does not publish to social platforms.

The delivered application is a React/TypeScript browser interface served by a local FastAPI service, backed by Python media-processing modules and FFmpeg. It is not a multi-user hosted service. Raw lecture media stays in the local processing workflow; selected text and creative prompts cross the configured cloud boundary. Local rendering of illustrations does not mean that their descriptions are generated offline.

Fresh verification for this report passed 249 Python tests with real FFmpeg integration enabled and network access denied, the frontend production build and lint checks, and 10 Playwright checks across desktop and mobile viewports. Browser workflow mutations were mocked; a read-only temporary library supplied existing real media. These results establish substantial automated regression coverage, not live provider availability, source factual correctness, legal permission, educational benefit or production readiness.

The main unresolved release gates are source/media rights, deployment-specific cloud retention and access approval, fresh authorised provider testing, systematic accessibility and user evaluation, and dependency/security review. The project is suitable for a supervised prototype demonstration subject to those limits, not unrestricted publication or public hosting.

## 1. Purpose, Scope and Evidence

Long recordings contain valuable explanations, but locating a self-contained moment, retaining its context, writing an accessible introduction and preparing a captioned short require several editing steps. Brevio explores whether a source-grounded workflow can assist this work while leaving the publication decision with a human reviewer. Reduced editing time and improved learning are hypotheses to evaluate, not measured outcomes of this report.

The assessment baseline is the inspected working tree, not a clean tagged release. The latest commit observed was `dc1b1a57eddd7c0ff7092c1b68cafa9fe936adad` (24 October 2026); the tree also contained uncommitted documentation and presentation changes. The evidence manifest records hashes of the inspected implementation and test files. Those hashes, rather than the commit alone, identify the documented snapshot.

Evidence classifications used throughout this pack:

- **Implemented:** directly supported by current source code.
- **Verified this session:** exercised by a retained command log or browser check.
- **Historical:** recorded in an earlier repository document or saved artifact; not a new experiment.
- **Proposed or pending:** an intended activity or acceptance condition without completed evidence.

The supplied assessment rubric is mapped below. No grade or facilitator endorsement is claimed.

| Criterion | Points | Evidence in this pack |
| --- | --- | --- |
| Honest implementation, testing, deployment and background IP | 5 | Sections 1, 5-7, 10; test report; verification logs |
| Functionalities and essential quality characteristics explained and tested | 4 | Sections 3-6; traceability tables; technical manual |
| Provider-guided cloud security assessment | 3 | Section 8, including residual risks and operator actions |
| Consistency with requirements/design and revision of related documents | 4 | Sections 3-4 and 9; historical-document supersession notices |
| Structure, completeness, language and readability | 4 | This report and separate task-oriented manuals |

## 2. Stakeholders and Boundaries

Teaching teams and researchers are prospective content owners and operators. Learners and public audiences are recipients of the resulting short, not necessarily users of the editing application. The named reviewer is responsible for watching the complete preview and checking source fidelity, captions, suitability and permissions. An operator maintains the local runtime and provider configuration. The facilitator determines any additional assessment-specific conditions.

In scope: local video intake; timestamped transcription; candidate selection; audience-specific planning; measured preview generation; named review; versioned export; technical and structural checks. The React interface has Source, Clips, Studio and Export stages.

Out of scope: automatic publication, social analytics, unrestricted video downloading, a general-purpose nonlinear editor, voice cloning, production multi-tenancy, cloud-hosted storage, and evidence of improved learning or engagement. A Source URL is a credit/link field, not a downloader. Generated B-roll is no longer supported. The Kids preset is a synthetic childlike treatment, not a dedicated child's voice or evidence of testing with children.

## 3. Current Requirements and Traceability

The following requirements restate the delivered behaviour. They are an assessment baseline derived from the implementation, not evidence that a stakeholder formally signed a requirements specification. References are relative to the repository root.

| ID | Requirement and acceptance condition | Owning implementation | Automated evidence / boundary |
| --- | --- | --- | --- |
| FR-01 | Accept a readable local video; reject invalid uploads and excessive upload bytes | api/main.py; api/schemas.py | test_api: invalid upload and cleanup; byte-limit branch inspected, not a dedicated load test |
| FR-02 | Produce timestamped transcript data; reuse a valid content/model-specific cache | pipeline/transcribe.py | test_transcribe: cache hit, invalidation, corrupt cache, CPU compute choice; ASR inference mocked |
| FR-03 | Nominate source-grounded candidates and expose uncertainty | pipeline/nominate.py; editorial.py; rank.py | test_pipeline: finite ranges, source evidence and unavailable reviews; no semantic-accuracy guarantee |
| FR-04 | Support automatic selection or 1-5 manually retained clips, preserving source order | pipeline/autoselect.py; workflow.py | test_autoselect; test_pipeline; mocked browser selection workflow |
| FR-05 | Support Researcher, General public and Kids; 16:9 and 9:16 | pipeline/storyboard.py; cards.py | audience-profile, layout and media tests; no new audience study |
| FR-06 | Fit a complete story into the measured 30-60 second budget or fail explicitly | pipeline/fit.py; media.py | fitting and duration-boundary tests; narration measurement fixtures |
| FR-07 | Produce a read-only blueprint, then a grounded storyboard; show fallback status honestly | pipeline/blueprint.py; storyboard.py | test_blueprint; privacy tests; browser blueprint checks |
| FR-08 | Render source footage, cards, captions, local narration and licensed music into a preview | pipeline/assemble.py; cards.py; media.py; music.py | real FFmpeg tests plus mocked model/narration tests; no new live Kokoro synthesis |
| FR-09 | Require named attestation and current-preview approval before export; invalidate stale approval | app.py; pipeline/project.py; workflow.py | API and storyboard revision/tamper tests; mocked browser approval flow |
| FR-10 | Export a versioned MP4, SRT, post copy, manifest and ZIP using approved assets | pipeline/export.py | export, hash, asset-reuse and naming tests |
| FR-11 | Display technical and structure checks separately from publication approval | pipeline/assess.py; structure.py; api/catalog.py | assessment and structure tests; browser checks panel |
| FR-12 | Bound paid batches, reuse completed requests and avoid blind retry of failed/ambiguous work | pipeline/cloud.py | cloud workflow and SDK-mock tests; actual gateway billing unverified |

| ID | Quality requirement | Realisation and verification | Remaining acceptance gap |
| --- | --- | --- | --- |
| NFR-01 Security | Local network boundary and path controls | Host/origin/token checks; allowlisted files; path containment; API tests | No authentication, penetration test or malware scanning |
| NFR-02 Privacy | Minimise provider payloads and keep keys server-side | Text payload allowlists; private environment configuration; privacy tests | No automatic personal-data redaction; gateway retention approval pending |
| NFR-03 Reliability | Detect stale state, interrupted jobs and changed assets | Revisions, hashes, durable job JSON, one worker; restart/tamper tests | No high availability, transactional database or disaster-recovery exercise |
| NFR-04 Performance | Avoid unnecessary ASR, narration and provider work | Content caches and stable asset reuse; cache tests | No current latency SLA, peak-memory benchmark or load test |
| NFR-05 Usability | Present a coherent four-stage workflow with understandable errors | Responsive React UI; 10 desktop/mobile browser checks | No independent usability study or full assistive-technology audit |
| NFR-06 Maintainability | Separate browser, API and processing responsibilities | Typed frontend, modular Python pipeline, build/lint/regression checks | Workflow still adapts legacy app.py callbacks; Python dependencies incompletely pinned |
| NFR-07 Traceability | Record source, selection, assets, revisions and assessments | Export manifests, hashes and versioned reports | Hashes prove identity/integrity, not truth or authorship |
| NFR-08 Responsible use | Human decision before publication and honest AI labels | Named preview approval, illustration labels, retained failed metrics | Reviewer competence and permission cannot be inferred by software |

## 4. Architecture and Design

### 4.1 Components and Data Flow

```text
Browser: Source -> Clips -> Studio -> Export
                     |
          localhost HTTP / JSON / media
                     |
FastAPI: validation -> revision checks -> single-worker jobs
                     |
workflow.py -> app.py callbacks -> pipeline modules
                     |
Local: WhisperX / Ollama / Kokoro / Pillow / FFmpeg
                     |
Selected text and prompts -> configured HTTPS AI gateway
                     |
Local preview -> named human approval -> versioned export
```

The browser manages interaction and server-state queries. FastAPI validates requests, exposes media and export metadata, and submits long-running jobs. The workflow adapter reuses existing callback validation rather than duplicating generation logic. This reduced migration risk, at the cost of retaining Gradio-shaped dependencies in the backend despite removal of the Gradio interface.

The application uses local files rather than a database: session/job JSON under `work/studio/`, run-specific processing files under `work/`, and immutable-by-convention export directories under `exports/`. Atomic JSON writes and staged export publication reduce partial-file failures. They do not provide distributed locking or a multi-process transaction system; exactly one API worker is required.

### 4.2 Contracts and State

A run contains its source identity/hash, options, transcript, candidates, selection, blueprint, storyboard, preview revision and approval. Transcript segments hold start/end times and text, with word timings where available. Selected clips retain IDs, source ranges and excerpts. The storyboard records opening, transitions, ending and supporting evidence. The measured timeline drives the rendered sequence and captions.

The conceptual progression is source -> analysed -> selected -> generating -> preview-current -> approved -> exported. An edit or regeneration clears approval. A failed generation must not leave the old approval active. Restarting validates saved source/checkpoint data, increments session revisions and clears approvals; queued/running jobs become interrupted rather than silently restarting. API idempotency keys prevent duplicate submission of the same action and reject conflicting reuse.

### 4.3 Design Decisions and Trade-offs

Local ASR and narration reduce raw-media exposure and recurring speech-service dependence, but require substantial model downloads and machine resources. Cloud text provides story-planning capability but introduces access, cost, retention and nondeterminism risks. Rendering constrained shape descriptions locally avoids dependence on an image-generation endpoint, but produces conceptual rather than photographic artwork.

Source-order compilation favours traceability over arbitrary rearrangement. Automatic selection improves convenience, but is not a human editorial decision. Source-evidence checks and bounded fitting reduce unsupported cuts without proving semantic faithfulness. Named final-preview review is therefore a release gate rather than a decorative checkbox.

The short-duration policy is based on measured narration and footage, not character count alone. Existing older duration policies remain interpretable for historical exports. New content is not padded or accepted outside the current budget simply to make generation succeed.

## 5. Implementation

### 5.1 Source, Transcription and Selection

Uploads are assigned UUID filenames, streamed in chunks and checked with media probes. The default upload limit is 2 GiB. Existing MP4s may be selected from the local downloads library. WhisperX provides transcription/alignment; the current model options include small, distil-large-v3 and large-v3, with CPU int8 and a content/model/compute-specific cache. Cache tests do not establish transcription accuracy.

Local Ollama nomination and editorial processing propose candidate moments, using source excerpts and sentence-boundary constraints. Embedding similarity is a ranking signal, not a probability that a clip is correct or engaging. Unavailable model review remains unavailable. Automatic selection aims to retain up to three clips that fit, while manual selection supports one to five; fitting may trim or remove clips and must expose that change.

### 5.2 Planning, Wording and Media

The blueprint sets the opening angle, relationships between clips and ending idea. The storyboard writes the actual hook, bridge labels, takeaway and narration using selected evidence. A failed model blueprint can produce a labelled template, which is not presented as model guidance. Model blueprint guidance is compared with the final wording by local structure checks; word overlap is not semantic entailment.

The configured local-illustration path sends text prompts to the configured provider and validates returned geometric descriptions before Pillow rendering. Narration uses an isolated Kokoro Python 3.12 environment. Current presets are Michael at 0.95x for Researcher, Heart at 1.0x for General public, and Sky at 0.98x with a +2-semitone effect for Kids. These are presets, not cloned voices.

FFmpeg assembles normalised source clips, cards and captions, then mixes ducked instrumental music and narration. The bundled library contains nine 64-second tracks with CC0 declarations and provenance metadata. Generated music and direct Google media are optional configuration paths, not the default library workflow. Generated B-roll/video requests are rejected.

### 5.3 Approval, Export and Assessment

The reviewer must inspect the current rendered preview, supply a name and attest review. Approval is bound to the relevant revision; changed wording, timing, selection, aspect or assets requires a fresh review. The name is not an authenticated identity or digital signature.

Exports contain `compilation.mp4`, `captions.srt`, `post.txt`, `selection.json`, approved media assets and a version-named ZIP. Export reuses frozen narration and media rather than making new generation requests. Versioned assessments remain associated with the exported artifact. Technical checks concern measurable media/caption properties; structure checks concern section and clip order, timing, wording contracts and blueprint comparison. Neither certifies truth, fairness or learning value.

### 5.4 Cost and Failure Handling

A cloud batch permits at most five story-text requests and two illustration requests, with one optional generated-music request. Local illustrations still consume text-model calls under the image allowance. With library music, there is no music-generation call. Completed calls are cached and hash-checked. Failed or ambiguous calls are not blindly resubmitted within the same batch; explicit regeneration starts a new batch and can incur new charges.

The estimate is informational, not a separate approval gate or billing guarantee. Server startup and library browsing do not generate paid content. Stopping local execution cannot guarantee that a provider cancels an already submitted billable request. The configured gateway alias, allowance and price must be checked by the operator before a live run.

## 6. Verification and Evaluation

| Activity | Current result | What it establishes |
| --- | --- | --- |
| Python unittest discovery, RUN_MEDIA_TESTS=1 | 249 tests passed; 125.836 s | Unit/regression contracts and real synthetic-media FFmpeg paths under network denial |
| TypeScript/Vite production build | Passed | Current frontend compiles and bundles |
| Oxlint | Passed | Configured frontend static checks |
| Playwright desktop/mobile | 10 passed; 14.0 s | Defined UI scenarios, overflow checks and real saved-media playback with mocked workflow mutations |
| Fresh end-to-end live AI generation | Not run | No current provider access, generation quality or actual cost conclusion |
| User study, learning study, accessibility audit, penetration test | Not performed | No validated benefit, standards compliance or broad security conclusion |

See the test report for commands, scope, limitations and the evidence register. The current test logs supersede old suite counts for this snapshot; older results remain historical rather than being rewritten as failures or passes today. A passing test run does not mean every existing saved export passes its own caption or publication checks.

The earlier four-week plan records a 26 September baseline on a 919.574-second source with 298.63 seconds of measured processing stages. That figure excluded manual review and export and predates the current workflow. It is historical planning evidence, not a current performance benchmark or a measured editing-time reduction. Reuse permissions for that source were explicitly unverified in the historical record.

Proposed evaluation should use permission-cleared lectures and the same source/brief for assisted and manual workflows. Record preparation time, reviewer corrections, context-loss findings, caption readability, completion rate and perceived workload. Use independent source-fidelity review and predefined acceptance rules. Any study involving children needs institutional approval and appropriate consent before recruitment. No sample size or effect estimate is asserted here without a study design.

## 7. Deployment, Operations and Handover

The delivered topology is a browser and API on the same machine, normally `http://127.0.0.1:8765`. A production React build is served by FastAPI; Vite is a development convenience, not the deployed application server. The macOS launcher selects `/opt/anaconda3/bin/python` unless `STUDIO_PYTHON` is set. Manual installation and alternative port instructions are in the technical manual.

Verification used macOS 26.7.1 on arm64, Python 3.13.5, Node 26.12.0, npm 11.20.0 and FFmpeg 9.0.2. The README's Python 3.11+ and Node 22.12+ are stated prerequisites, not a multi-version certification from this session. The isolated browser server ran successfully on port 8796 and was stopped after testing. No new public deployment or clean-machine installation was performed.

Operations require adequate disk space for sources, model caches, temporary renders and exports; one API worker; controlled provider credentials; and review of failed jobs rather than automatic resubmission. Stop generation before backup or upgrade. Back up approved exports and essential manifests separately from replaceable caches. Source and asset hashes should be checked after restore. A restore drill is still pending.

Handover includes source code, the current README, these manuals, test logs, presentation materials and exported demonstration assets already held locally. Do not include private keys, raw transcripts, licensed source video or the whole work directory in an assessment upload without permission. The documentation archive deliberately excludes those materials.

## 8. Cloud Security and Privacy Assessment

### 8.1 Scope and Provider Guidance

No application component is claimed to be hosted in the cloud. This does not make cloud risk irrelevant: selected excerpts and creative prompts are processed by a configured enterprise Portkey gateway and its upstream model provider, or by Google-direct routes if explicitly configured. The gateway's region, logging, deletion schedule, access policy, fallback configuration and contract were not inspected through an administrator console.

The assessment used Google's official API-key security guidance [R1] and the data-governance guidance reached from the Vertex AI documentation [R2], accessed 29 October 2026. R1 recommends keeping keys out of client code and source control, restricting keys, using secure storage and monitoring usage. R2 distinguishes training restrictions from retention and describes logging, caching and feature-dependent retention. These are reference controls, not proof of this gateway's settings or of zero retention. The public Portkey security URL attempted during preparation returned 404; no gateway-specific policy is invented to fill that gap.

For Google-direct Interactions calls, the adapter specifies `store=False`. This is useful minimisation for that API feature, but does not establish that all provider abuse logs, caches or gateway logs are disabled. The Portkey path uses its own chat-completions route. A gateway alias containing a region-like label is not sufficient evidence of data residency.

### 8.2 Data Classification and Boundaries

| Data | Current location/flow | Control and unresolved issue |
| --- | --- | --- |
| Video, lecture audio and extracted frames | Local source/work/export files | Local processing; OS access and backup controls required; no app-level encryption/purge |
| Transcripts and selected excerpts | Full transcript local; selected text enters planning requests | Payload allowlists reduce exposure, but excerpts can still identify people or contain confidential content |
| Audience/style metadata and generated descriptions | Local prompts and provider responses | Schema validation; returned content remains untrusted and may be incorrect |
| Keys and pricing settings | Server environment/private .env; pricing JSON separately | No VITE_ secrets; .env is plaintext and must be protected; prices are not verified invoices |
| Reviewer names, decisions and hashes | Local sessions/manifests/exports | Necessary provenance; names are personal data and not authenticated identities |
| Gateway/provider logs and caches | Potential external retention | Exact scope, access, region and deletion remain administrator-confirmation items |

The privacy tests check blueprint/storyboard payload fields and sentinel media-path exclusion. They do not constitute packet capture of every provider route, proof of anonymisation or a full data-protection impact assessment.

### 8.3 Risk Register

Ratings are qualitative engineering assessments, not measured incident probabilities. Residual risk assumes the described local-use restrictions are followed.

| ID | Threat and initial risk | Existing mitigation/evidence | Residual risk and required action |
| --- | --- | --- | --- |
| SEC-01 | Key leakage and unauthorised spend: High | Server-side environment loading; SDK error-redaction tests; no client credentials | Medium: operator must use least privilege, protect .env, rotate exposed keys and review provider usage [R1] |
| SEC-02 | Confidential excerpt retained externally: High | Selected-text minimisation; two privacy payload tests | High until gateway owner confirms retention, access, region and permitted data class [R2] |
| SEC-03 | Local API exposed publicly: High | Loopback launcher, Host/Origin checks, mutation token; API tests | High if binding changes: no user authentication or tenant isolation; public hosting prohibited without redesign |
| SEC-04 | File traversal or unintended file download: High | Allowlist and resolved-path containment; symlink escape test | Medium: media parsers and filesystem permissions still require maintenance and adversarial testing |
| SEC-05 | Duplicate requests and cost growth: Medium | Idempotency keys, single job worker, batch caps, cached completion and no blind retry | Medium: account-side retries, multiple fresh batches and real token charges remain outside local cap guarantees |
| SEC-06 | Malicious lecture text or inaccurate model output: High | Constrained JSON, excerpt checks, bounds checking and human review | Medium/High: no formal prompt-injection red team or semantic correctness proof |
| SEC-07 | Lost device, backup leakage or stale local data: High | Local storage and integrity hashes | High for sensitive data until disk encryption, access rules, retention and restore procedures are verified |
| SEC-08 | Dependency/model supply-chain vulnerability: High | Existing regression tests; installed-package inventory | Medium/High: no current vulnerability scan, complete SBOM or fully locked Python environment |

### 8.4 Required Security Decisions

The operator and institution must approve the data classification before any cloud call, obtain gateway-specific retention/residency evidence, verify model-route access and account-side fallback/retry behaviour, restrict credentials, set usage alerts, and agree incident ownership. For a suspected leak, stop new generation, revoke/rotate the affected credential through the provider, audit usage and exposed data, then validate the replacement privately. Do not paste credentials into support or assessment records.

Any future hosted deployment requires authentication, authorisation, isolated storage, HTTPS, managed secrets, quotas/rate limiting, job isolation, audit controls, monitoring, retention/deletion mechanisms and a new provider-aligned security review. Those controls are recommendations, not features of this prototype.

## 9. Requirements and Design Reconciliation

The original Gradio workflow plan and dated improvement notes are valuable development records but are not the current operating specification. Supersession notices now identify them as historical and point to this baseline. Their original milestones and results remain intact.

| Historical statement | Current baseline | Reason/impact |
| --- | --- | --- |
| Gradio interface / app.py launch | React plus FastAPI; app.py retains callbacks only | Separate UI/API with reusable processing contracts |
| Keep 3-5 manually reviewed clips | Automatic selection up to 3; manual 1-5 | Convenience plus measured short-duration fitting |
| Earlier clip/duration targets | New outputs target measured 30-60 s | Preserve legacy interpretation without applying old defaults to new work |
| Human approval before all rendering | Automatic selection/preview allowed; named final-preview approval before export | Reviewer sees the actual audiovisual output |
| Planned/local/retired speech approaches | Kokoro audience presets; no current freeform voice picker | Local deterministic asset reuse and explicit failure |
| Cost-consent step | Informational estimate and explicit generation action | Current design still enforces request caps and human preview review |
| Generated B-roll / inset overlay | Removed; cards and source transitions remain | Narrowed media scope |
| Fixed compilation.zip in older notes | Version-named compilation ZIP | Traceable repeated exports |
| Earlier suite counts | 249 Python and 10 browser checks in this session | Dated evidence; not a claim about all past or future versions |

Remaining documentation debt: the long pipeline walkthrough contains historical details below its current section, and the README contains a mixture of earlier model-access statements and current configuration guidance. These are explicitly treated as historical/configuration caveats, not proof of present provider access. This pack does not silently rewrite old experimental records or claim all legacy text has been modernised.

## 10. Background IP, Attribution and Ethics

The team's contribution is the workflow integration, validation/approval contracts, audience-specific orchestration, UI/API, rendering configuration and documentation. The team does not claim authorship of the underlying ASR, language, embedding or narration models, external libraries, fonts or source lectures. Individual contributions and any prior institutional/employer IP must be declared by the team; no attribution of individual work has been inferred from names or commits.

| Component/material | Provenance and licence evidence | Submission/distribution obligation |
| --- | --- | --- |
| FastAPI, Ollama Python SDK, srt, Portkey SDK | Installed metadata reports MIT-family licences | Retain applicable notices; SDK licence does not license a model or hosted service |
| Uvicorn, python-dotenv | Installed metadata: BSD-3-Clause | Retain licence/disclaimer notices |
| WhisperX | Installed metadata: BSD-2-Clause | Attribute library; separately check downloaded models and alignment assets |
| Gradio, sentence-transformers, google-genai | Installed metadata: Apache-2.0 | Preserve notices and applicable Apache obligations; legacy Gradio dependency remains |
| Pillow | Installed metadata: MIT-CMU | Preserve applicable notices |
| React, Lucide, IBM Plex fonts | Declared frontend dependencies; upstream licence files must accompany redistribution | Verify installed release licences, including font embedding; not a completed full SBOM |
| FFmpeg | Current binary built with --enable-gpl and --enable-version3 | Redistribution obligations depend on exact build/codecs; do not describe the binary as permissively licensed |
| Kokoro weights, voice files, Qwen and embedding weights | External model artifacts, not team-created | Verify exact model cards, weights licences and voice/data restrictions separately from Python package licences |
| Bundled music | Repository generator and manifest declare original synthesis, CC0-1.0 | Preserve provenance; declaration alone is not independent proof of ownership |
| Lecture footage/slides/performances | Third-party or contributor-supplied media | Obtain reuse, editing, voice/likeness and distribution permission; a URL is not permission |
| Swinburne branding in posters | Official-derived mark with a reference-matched composition | Institutional trademark/brand approval is separate from software licensing |
| AI-generated wording and illustrations | Model-assisted output, not factual source evidence | Label appropriately and review fidelity, bias, rights and suitability |

CC0 waives copyright-related restrictions to the extent legally possible; attribution can still aid provenance. It does not erase unrelated rights or prove that the declarant owned the material. Similarly, a passed factual-evidence check does not independently verify every statement in a lecture.

This documentation pack was prepared with AI assistance using repository inspection and executable checks. The team must review it and disclose assistance according to institutional assessment policy. Facilitator approval, participant consent, signatures and individual contributions remain human declarations.

## 11. Project Management and Reflection

The repository includes a four-week improvement plan (21 September-18 October), recorded baseline experiments, incremental workflow changes, a React migration and subsequent presentation work. These support a description of iterative prototype development with regression testing. They do not prove adherence to a particular agile framework, attendance, time sheets or individual task allocation.

Useful lessons are that transcript evidence is necessary but insufficient for context preservation; frozen assets and revisions matter when approval precedes export; model access and billing can fail independently of code; local processing reduces some privacy exposure but does not remove cloud text risk; and historical documentation must be separated from the final baseline. The unresolved legacy callback dependency is a pragmatic migration trade-off rather than a completed architectural cleanup.

## 12. Limitations, Future Work and Conclusion

The application has automated evidence for a substantial local workflow, but lacks fresh live-provider validation, clean-machine certification, multi-user security, full accessibility validation, independent performance measurements and user-benefit evidence. No claim of production readiness or universal model reliability follows from the test count.

Priorities are: obtain source/cloud governance approval; repeat a consented, budgeted live workflow on permission-cleared material; conduct human source-fidelity and caption review; complete dependency/licence/security checks; test installation and recovery on another machine; then evaluate usability and editing-time outcomes. Public hosting is a separate engineering project with its own acceptance and security requirements.

Brevio demonstrates an integrated, source-aware lecture-to-short workflow with a meaningful human approval boundary. Its strongest defensible outcome is the working local prototype and its verifiable engineering controls, not a proven educational or commercial impact.

## References and Related Documents

- [R1] Google, Using Gemini API keys, security and secret management: https://ai.google.dev/gemini-api/docs/api-key (accessed 29 October 2026).
- [R2] Google Cloud, data-governance / zero-data-retention guidance: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/data-governance (accessed 29 October 2026; redirected to the current Gemini Enterprise Agent Platform zero-data-retention documentation). Applicability to the configured gateway must be confirmed.
- [R3] Creative Commons, CC0 1.0 Universal: https://creativecommons.org/publicdomain/zero/1.0/ (licence reference; no legal opinion is provided).
- [R4] FFmpeg legal considerations: https://ffmpeg.org/legal.html (release/build-specific review required).
- [R5] Current repository: README.md, api/, pipeline/, frontend/, tests/ and assets/music/manifest.json, identified by evidence-manifest.json.
- [R6] Historical project records: docs/four-week-plan.md, docs/improvement-implementation.md and gradio_demo_workflow_plan.md.
- Supporting documents: technical-manual.md, user-manual.md and test-report.md in this pack.

## Facilitator and Team Sign-off

Not yet completed. Confirm the assessment template, referencing style, word count, permitted attachments, demonstration requirements, contribution declaration, AI-use declaration and project-specific acceptance criteria with the facilitator. Confirm team names/student identifiers and any confidentiality restrictions before submission. This section intentionally contains no invented approval or signature.