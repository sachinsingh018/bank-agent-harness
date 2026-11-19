"use client";

// The landing page preview: the first steps of a featured run playing on a loop next to its
// growing money-flow diagram. With reduced motion it stays still on the last preview step.

import { useEffect, useState } from "react";

import type { FlowGraph, StepActor } from "../../lib/api";
import { ACTOR_LABELS } from "../../lib/replay";
import { FlowDiagram } from "./flow";

export interface PreviewStep {
  index: number;
  actor: StepActor;
  title: string;
  explanation: string;
}

const STEP_MS = 2400;
const SHOWN = 3;

export function Preview({ steps, graph }: { steps: PreviewStep[]; graph: FlowGraph }) {
  const last = steps.length - 1;
  const [index, setIndex] = useState(0);
  const [still, setStill] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (query.matches) {
      // Reduced motion: no loop; show the furthest step straight away.
      const timer = window.setTimeout(() => {
        setStill(true);
        setIndex(last);
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [last]);

  useEffect(() => {
    if (still) return;
    const timer = window.setTimeout(
      () => setIndex((i) => (i >= last ? 0 : i + 1)),
      index >= last ? STEP_MS * 2 : STEP_MS,
    );
    return () => window.clearTimeout(timer);
  }, [index, last, still]);

  const upTo = steps[index]?.index ?? 0;
  const recent = steps.slice(Math.max(0, index - SHOWN + 1), index + 1);

  return (
    <div className="preview" aria-hidden="true">
      <div className="preview-flow">
        <FlowDiagram
          graph={graph}
          variant="board"
          upTo={upTo}
          freshStep={upTo}
          label="Preview of a money-flow diagram growing"
        />
      </div>
      <ol className="preview-steps">
        {recent.map((s) => (
          <li key={s.index} className={s.index === upTo ? "current" : ""}>
            <span className="tl-actor">{ACTOR_LABELS[s.actor]}</span>
            <span className="preview-title condensed">{s.title}</span>
            <span className="preview-explain">{s.explanation}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
