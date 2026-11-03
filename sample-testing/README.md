# KQ3 Phase 6 Testing Scripts

This folder contains an executable QA rules module, a pytest suite, and a small synthetic demo for the KQ3 Phase 6 testing and release-gate work.

## Included files

- `qa_checks.py` - tests supplied blueprint and candidate evidence, returns a structured QA report, and evaluates batch processing reliability.
- `test_qa_checks.py` - pytest cases for pass, revise, blocked, boundary, evidence, identity, learner and reliability scenarios.
- `examples.py` - synthetic valid and intentionally failing records used by tests and demo.
- `demo.py` - runs one synthetic pass example and one critical-failure example, then writes JSON reports to `demo_output/`.
- `phase6_testing_walkthrough.md` - presentation explanation and a demonstration order.
- `run_tests.sh` and `run_tests.bat` - convenience launchers for macOS/Linux and Windows.

## Run on Windows

```powershell
cd phase6-testing
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q
python demo.py
```

## Run on macOS or Linux

```bash
cd phase6-testing
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run_tests.sh
python demo.py
```

## Release statuses

- `blocked` - at least one critical source, rights, identity or context failure.
- `revise` - a non-critical acceptance check failed; correct and rerun the QA gate.
- `pending_human_approval` - all automated checks pass but educator approval is missing.
- `approved` - all checks pass and an educator reviewer is recorded.

The critical-failure rule overrides the numeric score. A high score cannot release a teaser that contains an unsupported claim, unauthorized source, missing context, or a blueprint identity/hash mismatch.

## Important scope boundary

The examples use synthetic JSON inputs. These scripts validate the evidence fields provided to Phase 6; they do not decode video, calculate actual caption accuracy, detect synchronization errors, run a learner pilot, or verify copyright. Those measurements must come from integrated media tools or documented human review. Do not describe synthetic demo outputs as empirical project results.
