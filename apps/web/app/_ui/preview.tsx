"use client";

// The landing page mini-replay: the featured run plays step by step on a loop next to its
// growing money-flow diagram, with a progress rail and a pause button. With reduced motion it
// shows one still frame: the last step and the whole diagram.

import { useEffect, useState } from "react";

import type { FlowGraph, StepActor } from "../../lib/api";
import { ACTOR_LABELS } from "../../lib/replay";
import { FlowDiagram } from "./flow";
import styles from "./preview.module.css";

export interface PreviewStep {
  index: number;
  actor: StepActor;
  title: string;
  explanation: string;
}

const STEP_MS = 1800;
const END_HOLD = 3;
const SHOWN = 3;

export function Preview({
  steps,
  graph,
  summary,
}: {
  steps: PreviewStep[];
  graph: FlowGraph;
  /** One sentence for screen readers, since the moving part is hidden from them. */
  summary: string;
}) {
  const last = steps.length - 1;
  const [index, setIndex] = useState(0);
  const [still, setStill] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (query.matches) {
      // Reduced motion: no loop; show the last step straight away.
      const timer = window.setTimeout(() => {
        setStill(true);
        setIndex(last);
      }, 0);
      return () => window.clearTimeout(timer);
    }
  }, [last]);

  useEffect(() => {
    if (still || paused) return;
    const timer = window.setTimeout(
      () => setIndex((i) => (i >= last ? 0 : i + 1)),
      index >= last ? STEP_MS * END_HOLD : STEP_MS,
    );
    return () => window.clearTimeout(timer);
  }, [index, last, still, paused]);

  const upTo = steps[index]?.index ?? 0;
  const recent = steps.slice(Math.max(0, index - SHOWN + 1), index + 1);

  return (
    <div className={styles.wrap}>
      <p className="sr-only">{summary}</p>
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
      <div className={styles.controls}>
        <ol className={styles.rail} aria-hidden="true">
          {steps.map((s, i) => (
            <li
              key={s.index}
              className={i < index ? styles.done : i === index ? styles.now : undefined}
            />
          ))}
        </ol>
        <span className={styles.counter} aria-hidden="true">
          Step {index + 1} of {steps.length}
        </span>
        {!still && (
          <button
            type="button"
            className={styles.toggle}
            aria-pressed={paused}
            onClick={() => setPaused((p) => !p)}
          >
            {paused ? "Play preview" : "Pause preview"}
          </button>
        )}
      </div>
    </div>
  );
}
