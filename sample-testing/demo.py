"""Run one passing and one release-blocking synthetic Phase 6 QA example."""
import json
from pathlib import Path

from examples import blocked_candidate, example_inputs
from qa_checks import evaluate_processing_reliability, evaluate_release


def save(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    out = Path("demo_output")
    out.mkdir(exist_ok=True)

    bp, candidate, learner, educator = example_inputs()
    pass_report = evaluate_release(bp, candidate, learner, educator)
    save(out / "qa_report_pass.json", pass_report)

    bp2, candidate2, learner2, educator2 = blocked_candidate()
    blocked_report = evaluate_release(bp2, candidate2, learner2, educator2)
    save(out / "qa_report_blocked.json", blocked_report)

    reliability = evaluate_processing_reliability(successful_jobs=9, total_jobs=10)
    save(out / "reliability_demo.json", reliability)

    for label, report in [("VALID SYNTHETIC EXAMPLE", pass_report),
                          ("CRITICAL-FAILURE EXAMPLE", blocked_report)]:
        print(f"\n{label}")
        print(f"Status: {report['status']}")
        print(f"Checks passed: {report['checks_passed']}/{report['checks_total']}")
        print("Failures:", ", ".join(item["code"] for item in report["failures"]) or "none")
    print(f"\nProcessing reliability example: {reliability['success_percent']}% ({reliability['status']})")
    print(f"Reports saved in: {out.resolve()}")
    print("Note: these are synthetic records, not results from real video or learner testing.")


if __name__ == "__main__":
    main()
