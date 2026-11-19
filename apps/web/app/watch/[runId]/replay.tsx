"use client";

// The replay: a timeline of steps on the left with play, pause, back, next and a speed
// control, and the evidence board on the right, where the money-flow diagram grows as the
// tools return data. Keyboard: left and right arrows step, space plays or pauses, Home and
// End jump. Every explanation comes from the API, written by deterministic code.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import type { ClaimOut, ReplayStep, RunSteps, StepKind } from "../../../lib/api";
import { checkLabel, reasonLabel, saysKind, saysLabel } from "../../../lib/format";
import { ACTOR_LABELS, argText, countVisible } from "../../../lib/replay";
import { FlowDiagram } from "../../_ui/flow";
import { CheckResult, Meter, ReviewerWord } from "../../_ui/marks";

type SpeedKey = "slow" | "normal" | "fast";

const SPEEDS: { key: SpeedKey; label: string; factor: number }[] = [
  { key: "slow", label: "Slow", factor: 1.7 },
  { key: "normal", label: "Normal", factor: 1 },
  { key: "fast", label: "Fast", factor: 0.45 },
];

const BASE_DELAY: Record<StepKind, number> = {
  alert: 3400,
  thinking: 3600,
  tool_call: 2200,
  tool_result: 2900,
  retry: 3400,
  checks: 3000,
  review: 3400,
  outcome: 0,
};

/** Steps where the cited or looked-up transfers are worth marking on the board. */
const HIGHLIGHT_KINDS: StepKind[] = ["tool_call", "checks", "outcome"];

function Icon({ name }: { name: "play" | "pause" | "back" | "next" | "again" }) {
  const paths: Record<typeof name, string> = {
    play: "M4 2.5v11l9-5.5z",
    pause: "M4 2.5h3v11H4zM9 2.5h3v11H9z",
    back: "M12 2.5v11L5 8zM3 2.5h2v11H3z",
    next: "M4 2.5v11L11 8zM11 2.5h2v11h-2z",
    again: "M8 3a5 5 0 1 1-4.6 3H1.5L4.5 2l3 4H5.6A3 3 0 1 0 8 5z",
  };
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" className="icon">
      <path d={paths[name]} />
    </svg>
  );
}

function Args({ args }: { args: Record<string, unknown> }) {
  const entries = Object.entries(args);
  if (entries.length === 0) return null;
  return (
    <ul className="args" aria-label="What it asked for">
      {entries.map(([key, value]) => (
        <li key={key}>
          <span className="arg-key">{key.replaceAll("_", " ")}</span> {argText(value)}
        </li>
      ))}
    </ul>
  );
}

function Raw({ label, raw }: { label: string; raw: string | null }) {
  if (!raw) return null;
  return (
    <details className="raw">
      <summary>{label}</summary>
      <pre>{raw}</pre>
    </details>
  );
}

function ClaimList({
  claims,
  selected,
  onSelect,
}: {
  claims: ClaimOut[];
  selected: number | null;
  onSelect: (i: number | null) => void;
}) {
  if (claims.length === 0) return null;
  return (
    <ol className="replay-claims">
      {claims.map((c, i) => (
        <li key={i}>
          <button
            type="button"
            className="claim-button"
            aria-pressed={selected === i}
            onClick={() => onSelect(selected === i ? null : i)}
          >
            <span className="statement">{c.statement}</span>
            <span className="meta">
              {selected === i ? "Shown on the board" : "Show on the board"}: {c.txn_ids.length}{" "}
              transfer{c.txn_ids.length === 1 ? "" : "s"} cited
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function Checks({ step }: { step: ReplayStep }) {
  return (
    <ul className="checks">
      {step.checks.map((c) => (
        <li key={c.name}>
          <span>{checkLabel(c.name)}</span>
          <CheckResult passed={c.passed} />
          {c.errors.length > 0 && (
            <span className="errors">
              <ul>
                {c.errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function StepBody({
  step,
  claim,
  onClaim,
}: {
  step: ReplayStep;
  claim: number | null;
  onClaim: (i: number | null) => void;
}) {
  const submit = step.tool === "submit_findings";
  let detail: ReactNode = null;
  if (step.kind === "thinking" && step.text) {
    detail = <blockquote className="thinking">{step.text}</blockquote>;
  } else if (step.kind === "tool_call" && submit) {
    detail = (
      <>
        <ClaimList claims={step.claims} selected={claim} onSelect={onClaim} />
        <Raw label="Show the exact answer it handed in" raw={step.raw} />
      </>
    );
  } else if (step.kind === "tool_call") {
    detail = (
      <>
        {step.arguments && <Args args={step.arguments} />}
        <Raw label="Show the exact tool call" raw={step.raw} />
      </>
    );
  } else if (step.kind === "tool_result") {
    detail = <Raw label="Show the raw answer" raw={step.raw} />;
  } else if (step.kind === "retry" && step.errors.length > 0) {
    detail = (
      <>
        <ul className="problems">
          {step.errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
        <Raw label="Show what the harness sent back" raw={step.raw} />
      </>
    );
  } else if (step.kind === "checks") {
    detail = <Checks step={step} />;
  } else if (step.kind === "review") {
    detail = (
      <>
        {step.reasons.length > 0 && (
          <ul className="plain">
            {step.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        )}
        {step.errors.length > 0 && (
          <>
            <p className="meta">Concerns it raised</p>
            <ul className="plain problems">
              {step.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </>
        )}
      </>
    );
  }
  return (
    <div className="tl-body">
      <p className={`tl-explain${step.ok === false ? " word-failed" : ""}`}>{step.explanation}</p>
      {detail}
    </div>
  );
}

function Verdict({ outcome, reached }: { outcome: ReplayStep | undefined; reached: boolean }) {
  if (!outcome || !reached) {
    return (
      <div className="board-verdict pending">
        <p className="meta">Verdict</p>
        <p className="pending-word condensed">Still investigating</p>
        <span className="meter" aria-hidden="true" />
      </div>
    );
  }
  const kind = saysKind(outcome.recommendation, outcome.status ?? "human_review");
  return (
    <div className="board-verdict reveal">
      <p className="meta">Agent says</p>
      <div className="line">
        <p className={`word condensed word-${kind}`}>
          <span className={`mark ${kind}`} aria-hidden="true" />
          {saysLabel(kind)}
        </p>
        {outcome.confidence !== null && (
          <span className="number condensed">{outcome.confidence.toFixed(2)}</span>
        )}
      </div>
      {outcome.confidence !== null && <Meter value={outcome.confidence} kind={kind} />}
      {outcome.status === "human_review" ? (
        <p className="board-route word-review">
          <span className="mark review" aria-hidden="true" />
          Sent to a person: {reasonLabel(outcome.reason)}
        </p>
      ) : (
        <p className="board-route">Proposed. A person approves or rejects it.</p>
      )}
    </div>
  );
}

function Legend() {
  return (
    <ul className="legend" aria-label="Legend">
      <li>
        <svg viewBox="-6 -6 12 12" aria-hidden="true">
          <circle r="4.5" className="lg-anchor" />
        </svg>
        account the rule flagged
      </li>
      <li>
        <svg viewBox="-6 -6 12 12" aria-hidden="true">
          <circle r="3.5" className="lg-alert" />
        </svg>
        in the alert
      </li>
      <li>
        <svg viewBox="-6 -6 12 12" aria-hidden="true">
          <circle r="3.5" className="lg-found" />
        </svg>
        found by the agent
      </li>
      <li>
        <svg viewBox="0 0 18 8" aria-hidden="true">
          <path d="M1 4h16" className="lg-hot" />
        </svg>
        cited or looked up
      </li>
      <li>
        <svg viewBox="0 0 18 8" aria-hidden="true">
          <path d="M1 4h16" className="lg-fresh" />
        </svg>
        new at this step
      </li>
    </ul>
  );
}

function EndPanel({
  run,
  claim,
  onClaim,
  caseLink,
}: {
  run: RunSteps;
  claim: number | null;
  onClaim: (i: number | null) => void;
  caseLink: boolean;
}) {
  const outcome = run.steps[run.steps.length - 1];
  const checks = run.steps.find((s) => s.kind === "checks");
  const review = run.steps.find((s) => s.kind === "review");
  return (
    <section className="end-panel reveal" aria-labelledby="end-title">
      <h2 id="end-title">How it ended</h2>
      <p>{outcome.explanation}</p>
      {outcome.narrative && (
        <>
          <h3>The agent&apos;s findings</h3>
          <p className="prose">{outcome.narrative}</p>
        </>
      )}
      {outcome.claims.length > 0 && (
        <>
          <h3>Claims, each backed by cited transfers</h3>
          <ClaimList claims={outcome.claims} selected={claim} onSelect={onClaim} />
        </>
      )}
      <div className="end-grid">
        <div>
          <h3>Deterministic checks</h3>
          {checks ? (
            <Checks step={checks} />
          ) : (
            <p className="muted">No answer reached the checks.</p>
          )}
        </div>
        <div>
          <h3>Reviewer agent</h3>
          <p>
            <ReviewerWord verdict={review ? review.verdict : null} />
          </p>
          {review && <p className="meta">{review.explanation}</p>}
        </div>
      </div>
      <p className="end-links">
        {caseLink && (
          <Link href={`/queue/${encodeURIComponent(run.run_id)}`}>Open the full case</Link>
        )}
        <Link href="/about">How it works</Link>
        <Link href="/watch">Watch another case</Link>
      </p>
    </section>
  );
}

function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return (
    target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)
  );
}

function activates(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && ["BUTTON", "A", "SUMMARY"].includes(target.tagName);
}

/** initialStep is 0-based; a link like /watch/<run>?step=5 opens at step 5. caseLink shows
 * the link to the queue case page, which exists only when the site reads the live API. */
export function Replay({
  run,
  initialStep = 0,
  caseLink = true,
}: {
  run: RunSteps;
  initialStep?: number;
  caseLink?: boolean;
}) {
  const steps = run.steps;
  const last = steps.length - 1;
  const [index, setIndex] = useState(Math.max(0, Math.min(last, initialStep)));
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<SpeedKey>("normal");
  const [claim, setClaim] = useState<number | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const moved = useRef(false);

  const atEnd = index >= last;
  const running = playing && !atEnd;
  const step = steps[index];
  const factor = SPEEDS.find((s) => s.key === speed)?.factor ?? 1;

  const go = useCallback(
    (to: number) => {
      moved.current = true;
      setClaim(null);
      setIndex(Math.max(0, Math.min(last, to)));
    },
    [last],
  );

  /** A visitor's own move: pause and go. */
  const jump = useCallback(
    (to: number) => {
      setPlaying(false);
      go(to);
    },
    [go],
  );

  const toggle = useCallback(() => {
    if (atEnd) {
      go(0);
      setPlaying(true);
      return;
    }
    setPlaying((p) => !p);
  }, [atEnd, go]);

  // Auto play: one timer per step, cleared on any change.
  useEffect(() => {
    if (!running) return;
    const delay = BASE_DELAY[steps[index].kind] * factor;
    const timer = window.setTimeout(() => {
      moved.current = true;
      setClaim(null);
      setIndex((i) => Math.min(last, i + 1));
    }, delay);
    return () => window.clearTimeout(timer);
  }, [running, index, factor, last, steps]);

  // Keyboard: arrows step, space plays or pauses, Home and End jump.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || isTyping(event.target)) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setPlaying(false);
        go(index + 1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        setPlaying(false);
        go(index - 1);
      } else if (event.key === "Home") {
        event.preventDefault();
        setPlaying(false);
        go(0);
      } else if (event.key === "End") {
        event.preventDefault();
        setPlaying(false);
        go(last);
      } else if (event.key === " " && !activates(event.target)) {
        event.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go, index, last, toggle]);

  // Keep the current step in view once the visitor has moved.
  useEffect(() => {
    if (!moved.current) return;
    const item = listRef.current?.querySelector(`[data-step="${index}"]`);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    item?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [index]);

  const outcome = steps.find((s) => s.kind === "outcome");
  const reached = outcome !== undefined && index >= outcome.index;
  const claims = step.claims.length > 0 ? step.claims : atEnd ? (outcome?.claims ?? []) : [];
  const highlight =
    claim !== null && claims[claim]
      ? claims[claim].txn_ids
      : HIGHLIGHT_KINDS.includes(step.kind)
        ? step.txn_ids
        : [];
  const visible = countVisible(run.graph, index);

  return (
    <div className="replay">
      <div className="replay-bar" role="toolbar" aria-label="Replay controls">
        <button type="button" className="ctl" onClick={() => jump(index - 1)} disabled={index === 0}>
          <Icon name="back" /> Back
        </button>
        <button type="button" className="ctl primary" onClick={toggle} aria-pressed={running}>
          <Icon name={atEnd ? "again" : running ? "pause" : "play"} />
          {atEnd ? "Play again" : running ? "Pause" : "Play"}
        </button>
        <button type="button" className="ctl" onClick={() => jump(index + 1)} disabled={atEnd}>
          Next <Icon name="next" />
        </button>
        <label className="scrub">
          <span className="sr-only">Jump to step</span>
          <input
            type="range"
            min={0}
            max={last}
            value={index}
            onChange={(e) => {
              setPlaying(false);
              go(Number(e.target.value));
            }}
          />
        </label>
        <span className="counter">
          Step {index + 1} of {steps.length}
        </span>
        <span className="speed" role="group" aria-label="Speed">
          {SPEEDS.map((s) => (
            <button
              key={s.key}
              type="button"
              className="ctl small"
              aria-pressed={speed === s.key}
              onClick={() => setSpeed(s.key)}
            >
              {s.label}
            </button>
          ))}
        </span>
      </div>

      <p className="sr-only" aria-live="polite">
        {`Step ${index + 1} of ${steps.length}: ${step.title}. ${step.explanation}`}
      </p>

      <div className="replay-grid">
        <section className="timeline-col" aria-label="Steps of the investigation">
          <ol className="timeline" ref={listRef}>
            {steps.slice(0, index + 1).map((s) => {
              const current = s.index === index;
              return (
                <li
                  key={s.index}
                  data-step={s.index}
                  className={`tl-step kind-${s.kind} actor-${s.actor}${current ? " current" : ""}`}
                  aria-current={current ? "step" : undefined}
                >
                  <span className="tl-dot" aria-hidden="true" />
                  <p className="tl-actor">{ACTOR_LABELS[s.actor]}</p>
                  {current ? (
                    <h2 className="tl-title">{s.title}</h2>
                  ) : (
                    <button
                      type="button"
                      className="tl-title tl-jump"
                      onClick={() => {
                        setPlaying(false);
                        go(s.index);
                      }}
                    >
                      {s.title}
                    </button>
                  )}
                  {current ? (
                    <StepBody step={s} claim={claim} onClaim={setClaim} />
                  ) : (
                    <p className="tl-past">{s.explanation}</p>
                  )}
                </li>
              );
            })}
          </ol>
          {index === 0 && !running && (
            <p className="hint meta">
              Press Play to watch the whole investigation, or Next to go one step at a time.
            </p>
          )}
          {atEnd && <EndPanel run={run} claim={claim} onClaim={setClaim} caseLink={caseLink} />}
        </section>

        <aside className="board" aria-label="Evidence board">
          <div className="board-head">
            <h2>Evidence board</h2>
            <p className="meta">
              {visible.nodes} account{visible.nodes === 1 ? "" : "s"}, {visible.edges} money
              link{visible.edges === 1 ? "" : "s"}
              {atEnd && run.graph.hidden_accounts > 0
                ? `, ${run.graph.hidden_accounts} more not drawn`
                : ""}
            </p>
          </div>
          <div className="board-flow">
            <FlowDiagram
              graph={run.graph}
              variant="board"
              upTo={index}
              freshStep={index}
              highlight={highlight}
              label={`Money flow seen so far: ${visible.nodes} accounts and ${visible.edges} links`}
            />
          </div>
          <Legend />
          <Verdict outcome={outcome} reached={reached} />
        </aside>
      </div>
    </div>
  );
}

/** The 0-based step to open at from a 1-based ?step= value; the first step otherwise. */
function startAt(asked: number, count: number): number {
  return Number.isInteger(asked) ? Math.min(Math.max(asked, 1), count) - 1 : 0;
}

/** The replay opened at the step in the URL, so /watch/<run>?step=5 opens at step 5. Render it
 * inside a Suspense boundary: reading the query on the client keeps the page static. */
export function StepFromUrl({ run, caseLink }: { run: RunSteps; caseLink: boolean }) {
  const asked = Number(useSearchParams().get("step"));
  return (
    <Replay run={run} initialStep={startAt(asked, run.steps.length)} caseLink={caseLink} />
  );
}
