// The harness in one picture, drawn by hand: the main path runs top to bottom, read-only
// tools sit beside the agent loop, and tracing and evals watch from the side. The numbered
// list on the about page is its text alternative.

import styles from "./harness-diagram.module.css";

interface Box {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  title: string;
  caption: string;
  kind: "alert" | "agent" | "tool" | "check" | "reviewer" | "human" | "audit" | "meta";
}

const MAIN_X = 30;
const MAIN_W = 196;
const SIDE_X = 250;
const SIDE_W = 104;
const H = 56;

const BOXES: Box[] = [
  { id: "alert", x: MAIN_X, y: 8, w: MAIN_W, h: H, title: "Alert", caption: "a rule flags transfers", kind: "alert" },
  { id: "loop", x: MAIN_X, y: 104, w: MAIN_W, h: H, title: "Agent loop", caption: "plans, asks, answers", kind: "agent" },
  { id: "tools", x: SIDE_X, y: 104, w: SIDE_W, h: H, title: "Tools", caption: "read only", kind: "tool" },
  { id: "checks", x: MAIN_X, y: 200, w: MAIN_W, h: H, title: "Verifiers", caption: "shape, citations, sums", kind: "check" },
  { id: "reviewer", x: MAIN_X, y: 296, w: MAIN_W, h: H, title: "Reviewer agent", caption: "second look, own checklist", kind: "reviewer" },
  { id: "human", x: MAIN_X, y: 392, w: MAIN_W, h: H, title: "Human approval", caption: "an analyst decides", kind: "human" },
  { id: "audit", x: MAIN_X, y: 488, w: MAIN_W, h: H, title: "Audit trail", caption: "append-only log", kind: "audit" },
  { id: "trace", x: SIDE_X, y: 200, w: SIDE_W, h: 152, title: "Tracing", caption: "every call", kind: "meta" },
  { id: "evals", x: SIDE_X, y: 392, w: SIDE_W, h: 152, title: "Evals", caption: "fixed alert sets", kind: "meta" },
];

const KIND_CLASS: Record<Box["kind"], string> = {
  alert: styles.alert,
  agent: styles.agent,
  tool: styles.tool,
  check: styles.check,
  reviewer: styles.reviewer,
  human: styles.human,
  audit: styles.audit,
  meta: styles.meta,
};

const CX = MAIN_X + MAIN_W / 2;

export function HarnessDiagram({ describedBy }: { describedBy: string }) {
  return (
    <svg
      className={styles.diagram}
      viewBox="0 0 360 552"
      role="img"
      aria-labelledby="harness-diagram-title"
      aria-describedby={describedBy}
    >
      <title id="harness-diagram-title">
        How a case moves through the harness, from alert to audit trail
      </title>
      <defs>
        <marker
          id="harness-arrow"
          viewBox="0 0 8 8"
          refX="7"
          refY="4"
          markerWidth="7"
          markerHeight="7"
          orient="auto-start-reverse"
        >
          <path d="M0 0 L8 4 L0 8 z" className={styles.arrowHead} />
        </marker>
      </defs>

      {/* Main path, top to bottom. */}
      {[8, 104, 200, 296, 392].map((y) => (
        <line
          key={y}
          x1={CX}
          y1={y + H}
          x2={CX}
          y2={y + 96 - 2}
          className={styles.flow}
          markerEnd="url(#harness-arrow)"
        />
      ))}

      {/* Agent loop asks the tools and reads their answers. */}
      <line x1={MAIN_X + MAIN_W} y1={124} x2={SIDE_X - 2} y2={124} className={styles.flow} markerEnd="url(#harness-arrow)" />
      <line x1={SIDE_X} y1={142} x2={MAIN_X + MAIN_W + 2} y2={142} className={styles.flow} markerEnd="url(#harness-arrow)" />

      {/* One retry: failed checks go back to the agent once. */}
      <path
        d={`M${MAIN_X} ${228} H${MAIN_X - 18} V${132} H${MAIN_X - 2}`}
        className={styles.retry}
        markerEnd="url(#harness-arrow)"
      />
      <text x={MAIN_X - 22} y={186} className={styles.side} transform={`rotate(-90 ${MAIN_X - 22} 186)`}>
        one retry
      </text>

      {/* Tracing records the loop, tools, checks and reviewer; evals score whole runs. */}
      <line x1={SIDE_X + SIDE_W / 2} y1={160} x2={SIDE_X + SIDE_W / 2} y2={200} className={styles.watch} />
      <line x1={MAIN_X + MAIN_W} y1={228} x2={SIDE_X} y2={228} className={styles.watch} />
      <line x1={MAIN_X + MAIN_W} y1={324} x2={SIDE_X} y2={324} className={styles.watch} />
      <line x1={MAIN_X + MAIN_W} y1={420} x2={SIDE_X} y2={420} className={styles.watch} />
      <line x1={MAIN_X + MAIN_W} y1={516} x2={SIDE_X} y2={516} className={styles.watch} />

      {BOXES.map((b) => (
        <g key={b.id}>
          <rect
            x={b.x}
            y={b.y}
            width={b.w}
            height={b.h}
            rx={3}
            className={`${styles.box} ${KIND_CLASS[b.kind]}`}
          />
          {b.kind === "audit" && (
            <rect x={b.x + 4} y={b.y + 4} width={b.w - 8} height={b.h - 8} rx={2} className={styles.inner} />
          )}
          <text x={b.x + 12} y={b.y + 24} className={styles.title}>
            {b.title}
          </text>
          <text x={b.x + 12} y={b.y + 42} className={styles.caption}>
            {b.caption}
          </text>
        </g>
      ))}

      {/* Second captions for the tall side boxes. */}
      <text x={SIDE_X + 12} y={260} className={styles.caption}>
        model, tool,
      </text>
      <text x={SIDE_X + 12} y={276} className={styles.caption}>
        check, latency
      </text>
      <text x={SIDE_X + 12} y={452} className={styles.caption}>
        score every
      </text>
      <text x={SIDE_X + 12} y={468} className={styles.caption}>
        change to
      </text>
      <text x={SIDE_X + 12} y={484} className={styles.caption}>
        prompts, tools
      </text>
      <text x={SIDE_X + 12} y={500} className={styles.caption}>
        and models
      </text>
    </svg>
  );
}
