// Small shared pieces for server components: the "agent says" word with its shape, the
// confidence meter with the 0.5 decision line, and pass or fail marks. Colour is never the
// only signal: every coloured word has a shape and a text label.

import { saysKind, saysLabel, type SaysKind } from "../../lib/format";

export const DECISION_LINE = 0.5;

export function Says({
  recommendation,
  status,
}: {
  recommendation: string | null;
  status: string;
}) {
  const kind = saysKind(recommendation, status);
  return (
    <span className={`says word-${kind}`}>
      <span className={`mark ${kind}`} aria-hidden="true" />
      {saysLabel(kind)}
    </span>
  );
}

function clamp(value: number): number {
  return Math.min(1, Math.max(0, value));
}

/** A track from 0 to 1 with the decision line at 0.5, a marker at the confidence and a shaded
 * span between the two, so the distance from the cut line is visible at a glance. */
export function Meter({
  value,
  kind,
  mini = false,
}: {
  value: number;
  kind: SaysKind;
  mini?: boolean;
}) {
  const at = clamp(value);
  const from = Math.min(at, DECISION_LINE) * 100;
  const width = Math.abs(at - DECISION_LINE) * 100;
  const side = at >= DECISION_LINE ? "at or above" : "below";
  return (
    <span
      className={`meter ${kind}${mini ? " mini" : ""}`}
      role="img"
      aria-label={`Confidence ${at.toFixed(2)}, ${side} the 0.5 decision line`}
    >
      <span className="span" style={{ left: `${from}%`, width: `${width}%` }} />
      <span className="cut" />
      <span className="at" style={{ left: `${at * 100}%` }} />
    </span>
  );
}

export function CheckResult({ passed }: { passed: boolean }) {
  const kind = passed ? "passed" : "failed";
  return (
    <span className={`says word-${kind}`}>
      <span className={`mark ${kind}`} aria-hidden="true" />
      {kind}
    </span>
  );
}

/** The checker's verdict in user words, with a shape. */
export function ReviewerWord({ verdict }: { verdict: string | null }) {
  if (verdict === "approve") {
    return (
      <span className="says word-passed">
        <span className="mark passed" aria-hidden="true" />
        approved
      </span>
    );
  }
  if (verdict === "reject" || verdict === "failed") {
    return (
      <span className="says word-failed">
        <span className="mark failed" aria-hidden="true" />
        {verdict === "reject" ? "rejected" : "did not finish"}
      </span>
    );
  }
  return <span className="muted">not run</span>;
}
