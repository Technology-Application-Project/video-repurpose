import { CheckCircle2, ChevronDown, Info, TriangleAlert } from "lucide-react";
import type { Metric, StructureCheck } from "./types";

const statusText: Record<string, string> = {
  pass: "Passed",
  fail: "Failed",
  review: "To review",
  not_applicable: "Not needed",
  unavailable: "Could not run",
};
const neutral = (status: string) => status === "not_applicable" || status === "unavailable";
const needsAttention = (status: string) => status === "fail" || status === "review" || status === "unavailable";
const plainValue = (value: unknown) =>
  value == null ? "Nothing recorded" : typeof value === "object" ? JSON.stringify(value) : String(value);

export function MetricList({ metrics }: { metrics: Metric[] }) {
  return (
    <div className="metrics">
      {[...metrics]
        .sort((first, second) => Number(first.status === "pass") - Number(second.status === "pass"))
        .map((metric) => (
          <details className={`metric metric-${metric.status}`} key={metric.id} open={needsAttention(metric.status)}>
            <summary>
              {metric.status === "pass" ? (
                <CheckCircle2 size={15} />
              ) : neutral(metric.status) ? (
                <Info size={15} />
              ) : (
                <TriangleAlert size={15} />
              )}
              <span>{metric.label}</span>
              <em className="metric-chip">{statusText[metric.status] || metric.status}</em>
              <ChevronDown size={14} />
            </summary>
            <dl className="metric-body">
              <div>
                <dt>What we check</dt>
                <dd>{metric.checks || metric.threshold}</dd>
              </div>
              <div>
                <dt>Your export</dt>
                <dd className="metric-exported">{metric.exported || plainValue(metric.value)}</dd>
              </div>
              {metric.fix && (
                <div className="metric-fix">
                  <dt>What to do</dt>
                  <dd>{metric.fix}</dd>
                </div>
              )}
            </dl>
            <details className="metric-raw">
              <summary>Technical details</summary>
              <p>Limit: {metric.threshold}</p>
              <pre>{JSON.stringify(metric.value, null, 2)}</pre>
            </details>
          </details>
        ))}
    </div>
  );
}

export function StructurePanel({ structure }: { structure?: StructureCheck | null }) {
  if (!structure || structure.status === "unavailable") {
    return <p className="muted">{structure?.note || "No structure report yet."}</p>;
  }
  const passed = structure.status === "passed";
  const { counts } = structure;
  return (
    <>
      <div className={`assessment-summary ${passed ? "passed" : ""}`}>
        {passed ? <CheckCircle2 size={23} /> : <TriangleAlert size={23} />}
        <div>
          <strong>{passed ? "Structure matches" : "Needs attention"}</strong>
          <span>
            {counts.pass} passed · {counts.fail} failed · {counts.review} to review
          </span>
        </div>
      </div>
      <MetricList metrics={structure.metrics} />
      {structure.blueprint && (
        <section className="plan-follow" aria-label="Does the story follow the plan">
          <h3>Does the story follow the plan?</h3>
          {structure.blueprint.rows.some((row) => row.match) ? (
            <ul>
              {structure.blueprint.rows
                .filter((row) => row.match)
                .map((row) => (
                  <li key={row.section} className={`follow-${row.match}`}>
                    {row.match === "follows" ? <CheckCircle2 size={14} /> : <TriangleAlert size={14} />}
                    <span>{row.section}</span>
                    <em>{row.match === "follows" ? "Follows the plan" : "Differs"}</em>
                  </li>
                ))}
            </ul>
          ) : (
            <p className="muted">Wording is only compared for a model plan.</p>
          )}
          <details className="plan-compare">
            <summary>
              Compare the wording
              <ChevronDown size={14} />
            </summary>
            <p className="muted">
              Key words are compared automatically. The video wording is shortened on purpose, so a different
              sentence can still follow the plan.
            </p>
            <dl>
              {structure.blueprint.rows.map((row) => (
                <div key={row.section}>
                  <dt>{row.section}</dt>
                  <dd>
                    <small>Planned</small>
                    {row.planned}
                  </dd>
                  <dd>
                    <small>Exported</small>
                    {row.actual}
                  </dd>
                </div>
              ))}
            </dl>
          </details>
        </section>
      )}
    </>
  );
}
