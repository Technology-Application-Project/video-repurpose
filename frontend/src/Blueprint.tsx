import { ClipboardList, TriangleAlert } from "lucide-react";
import type { Blueprint } from "./types";

const seconds = (value: number) => `${Math.round(value)}s`;

export function BlueprintPanel({ plan, planning }: { plan?: Blueprint | null; planning: boolean }) {
  if (!plan) {
    return planning ? (
      <section className="blueprint" aria-live="polite">
        <div className="blueprint-head">
          <div>
            <span className="eyebrow">BLUEPRINT</span>
            <h2>Planning the video…</h2>
            <p>The plan for the opening, bridges and ending appears here before the storyboard is generated.</p>
          </div>
        </div>
      </section>
    ) : null;
  }
  const { timing, clips, bridges, final } = plan;
  const title = (id: string) => clips.find((item) => item.id === id)?.title || id;
  const bridgeSeconds = clips.length > 1 ? timing.bridge_seconds / (clips.length - 1) : 0;
  const parts = [
    { label: "Opening", value: timing.opening_seconds, kind: "card" },
    ...clips.flatMap((item, index) => [
      ...(index ? [{ label: "Bridge", value: bridgeSeconds, kind: "card" }] : []),
      { label: item.role, value: item.seconds, kind: "clip" },
    ]),
    { label: "Ending", value: timing.ending_seconds, kind: "card" },
  ];
  const template = plan.generated.status === "template_plan";
  return (
    <section className="blueprint" aria-label="Video blueprint">
      <div className="blueprint-head">
        <div>
          <span className="eyebrow">BLUEPRINT</span>
          <h2>
            {plan.clip_count} {plan.clip_count === 1 ? "clip" : "clips"} · about {seconds(timing.estimated_total)}
          </h2>
          <p>{plan.clip_count_reason}</p>
        </div>
        <div className="blueprint-badges">
          <span className={`status ${timing.fits ? "status-success" : ""}`}>
            {timing.fits ? "Within" : "Outside"} {timing.budget[0]}-{timing.budget[1]}s
          </span>
          <span className="status">{template ? "Template plan" : "Model plan"}</span>
        </div>
      </div>
      <div className="blueprint-bar" role="img" aria-label="Planned timeline">
        {parts.map((part, index) => (
          <span
            key={index}
            className={`blueprint-part ${part.kind}`}
            style={{ flexGrow: Math.max(part.value, 0.5) }}
            title={`${part.label} · ${seconds(part.value)}`}
          >
            {part.label}
          </span>
        ))}
      </div>
      <div className="blueprint-ideas">
        <article>
          <h3>
            Opening <small>{plan.opening.approach}</small>
          </h3>
          <p>{plan.opening.idea}</p>
        </article>
        <article>
          <h3>Bridges</h3>
          {bridges.length ? (
            <ol>
              {bridges.map((bridge) => (
                <li key={bridge.to_clip_id}>
                  <small>
                    {title(bridge.from_clip_id)} → {title(bridge.to_clip_id)} · {bridge.relationship}
                  </small>
                  {bridge.idea}
                </li>
              ))}
            </ol>
          ) : (
            <p className="blueprint-muted">One clip, so no bridges.</p>
          )}
        </article>
        <article>
          <h3>Ending</h3>
          <p>{plan.ending.synthesis}</p>
          <p className="blueprint-muted">Action: {plan.ending.action}</p>
        </article>
      </div>
      {plan.risks.length > 0 && (
        <ul className="blueprint-risks">
          {plan.risks.map((risk) => (
            <li key={risk}>
              <TriangleAlert size={14} />
              {risk}
            </li>
          ))}
        </ul>
      )}
      {plan.generated.note && <p className="blueprint-muted">{plan.generated.note}</p>}
      {final && (
        <p className="blueprint-final">
          <ClipboardList size={14} />
          Result: {final.clip_count} {final.clip_count === 1 ? "clip" : "clips"}
          {final.measured_total != null &&
            `, ${seconds(final.measured_total)} measured (${(final.difference ?? 0) >= 0 ? "+" : ""}${Math.round(final.difference ?? 0)}s vs plan)`}
          {final.same_clips ? "" : ". The clip set changed after fitting."}
        </p>
      )}
    </section>
  );
}
