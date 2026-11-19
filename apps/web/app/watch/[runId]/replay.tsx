"use client";

// The replay, in two modes. Guided plays the whole run in about a minute with a caption per
// step; Explore is free stepping with back, next, a scrubber and a speed control. Steps sit on
// the left, the evidence board on the right, where the money-flow diagram grows as the tools
// return data. A second tab shows the case file, rendered on the server. Keyboard: left and
// right arrows step, space plays or pauses, Home and End jump. Every step summary is written by
// deterministic code from the recorded run, never by the model.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

import type { ClaimOut, FlowEdge, ReplayStep, RunSteps } from "../../../lib/api";
import type { RunEffort } from "../../../lib/bundle-types";
import { checkLabel, reasonLabel, saysKind, saysLabel } from "../../../lib/format";
import {
  ACTOR_LABELS,
  GUIDED_TOTAL_MS,
  STEP_WEIGHT,
  countVisible,
  effortFor,
  guidedDelays,
  reviewFailureText,
} from "../../../lib/replay";
import { FlowDiagram, edgeAmountsText } from "../../_ui/flow";
import { CheckResult, Meter, ReviewerWord } from "../../_ui/marks";
import styles from "./replay.module.css";

type SpeedKey = "slow" | "normal" | "fast";
export type ReplayMode = "guided" | "explore";
type Tab = "replay" | "case";

/** The stored reviewer agent row, when one exists. */
export interface ReviewSummary {
  verdict: string;
  failure_reason: string | null;
}

const SPEEDS: { key: SpeedKey; label: string; factor: number }[] = [
  { key: "slow", label: "Slow", factor: 1.7 },
  { key: "normal", label: "Normal", factor: 1 },
  { key: "fast", label: "Fast", factor: 0.45 },
];

/** Steps where the cited or looked-up transfers are worth marking on the board. */
const HIGHLIGHT_KINDS: ReplayStep["kind"][] = ["tool_call", "checks", "outcome"];

const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

function subscribeReducedMotion(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED_MOTION);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/** True when the visitor asks for less motion. False on the server and in the first render. */
function useReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribeReducedMotion,
    () => window.matchMedia(REDUCED_MOTION).matches,
    () => false,
  );
}

/** At most this many transfer chips per step; the rest are counted. */
const MAX_CHIPS = 12;

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

// ------------------------------------------------------------------ evidence chips

/** Shared state for cross-highlighting chips and the money-flow diagram. */
interface Evidence {
  /** Transfers lit on the board by a hovered or focused chip. */
  hover: number[] | null;
  setHover: (ids: number[] | null) => void;
  pinned: number | null;
  setPinned: (id: number | null) => void;
  /** Transfers behind the money link in focus on the board. */
  fromBoard: ReadonlySet<number>;
}

function TxnChips({ ids, evidence, label }: { ids: number[]; evidence: Evidence; label: string }) {
  if (ids.length === 0) return null;
  const shown = ids.slice(0, MAX_CHIPS);
  return (
    <ul className={styles.chips} aria-label={label}>
      {shown.map((id) => (
        <li key={id}>
          <button
            type="button"
            className={`${styles.chip}${evidence.fromBoard.has(id) ? ` ${styles.chipLit}` : ""}`}
            aria-pressed={evidence.pinned === id}
            onMouseEnter={() => evidence.setHover([id])}
            onMouseLeave={() => evidence.setHover(null)}
            onFocus={() => evidence.setHover([id])}
            onBlur={() => evidence.setHover(null)}
            onClick={() => evidence.setPinned(evidence.pinned === id ? null : id)}
          >
            <span className={styles.chipKey}>transfer</span> {id}
          </button>
        </li>
      ))}
      {ids.length > shown.length && (
        <li className={styles.chipMore}>and {ids.length - shown.length} more</li>
      )}
    </ul>
  );
}

function ClaimList({
  claims,
  selected,
  onSelect,
  evidence,
}: {
  claims: ClaimOut[];
  selected: number | null;
  onSelect: (i: number | null) => void;
  evidence: Evidence;
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
          <TxnChips
            ids={c.txn_ids}
            evidence={evidence}
            label={`Transfers cited by claim ${i + 1}`}
          />
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

// ------------------------------------------------------------------ technical details

const RAW_LABELS: Partial<Record<ReplayStep["kind"], string>> = {
  tool_call: "Exact tool call",
  tool_result: "Raw answer",
  retry: "What the harness sent back",
};

function ms(value: number): string {
  return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`;
}

/** Tool name, arguments, timings and the raw text of one step, collapsed by default. */
function TechDetails({ step, effort }: { step: ReplayStep; effort: RunEffort | null }) {
  const { model } = effortFor(effort, step.index);
  const tool =
    step.kind === "tool_result"
      ? effortFor(effort, step.index - 1).tool
      : effortFor(effort, step.index).tool;
  const args = step.arguments && Object.keys(step.arguments).length > 0 ? step.arguments : null;
  const rawLabel =
    step.tool === "submit_findings" ? "Exact answer it handed in" : RAW_LABELS[step.kind];
  const raw = step.raw && rawLabel ? step.raw : null;
  if (!step.tool && !raw && !model) return null;
  return (
    <details className={styles.tech}>
      <summary>Technical details</summary>
      <dl className={styles.techList}>
        {step.tool && (
          <>
            <dt>Tool</dt>
            <dd>{step.tool}</dd>
          </>
        )}
        {args && (
          <>
            <dt>Arguments</dt>
            <dd>
              <code className={styles.code}>{JSON.stringify(args)}</code>
            </dd>
          </>
        )}
        {tool && (
          <>
            <dt>Tool time</dt>
            <dd>
              {ms(tool.duration_ms)}
              {tool.ok ? "" : ", returned an error"}
            </dd>
          </>
        )}
        {model && (
          <>
            <dt>Model tokens</dt>
            <dd>
              {model.tokens_in.toLocaleString("en-US")} in,{" "}
              {model.tokens_out.toLocaleString("en-US")} out
            </dd>
            <dt>Model latency</dt>
            <dd>{ms(model.latency_ms)}</dd>
          </>
        )}
      </dl>
      {raw && (
        <>
          <p className={styles.techLabel}>
            {rawLabel}
            {step.raw_truncated ? " (cut at 2,000 characters; the full text is in the raw run)" : ""}
          </p>
          <pre className={styles.pre}>{raw}</pre>
        </>
      )}
    </details>
  );
}

function StepBody({
  step,
  claim,
  onClaim,
  evidence,
  effort,
}: {
  step: ReplayStep;
  claim: number | null;
  onClaim: (i: number | null) => void;
  evidence: Evidence;
  effort: RunEffort | null;
}) {
  const submit = step.tool === "submit_findings";
  let detail: ReactNode = null;
  if (step.kind === "thinking" && step.text) {
    detail = (
      <div className={styles.notes}>
        <h4>Agent notes</h4>
        <p className="meta">Text the model wrote while it worked, shown as recorded.</p>
        <blockquote className="thinking">{step.text}</blockquote>
      </div>
    );
  } else if (step.kind === "tool_call" && submit) {
    detail = <ClaimList claims={step.claims} selected={claim} onSelect={onClaim} evidence={evidence} />;
  } else if (step.kind === "retry" && step.errors.length > 0) {
    detail = (
      <ul className="problems">
        {step.errors.map((e, i) => (
          <li key={i}>{e}</li>
        ))}
      </ul>
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
  const chips = !submit && step.kind !== "outcome" ? step.txn_ids : [];
  return (
    <div className="tl-body">
      <p className={`tl-explain${step.ok === false ? " word-failed" : ""}`}>{step.explanation}</p>
      {detail}
      <TxnChips ids={chips} evidence={evidence} label="Transfers in this step" />
      <TechDetails step={step} effort={effort} />
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

function Legend({ motion }: { motion: boolean }) {
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
      {motion && (
        <li className={styles.legendMotion}>
          <svg viewBox="-6 -6 12 12" aria-hidden="true">
            <circle r="3" className={styles.lgMoney} />
          </svg>
          a transfer, moving in time order
        </li>
      )}
    </ul>
  );
}

function ReviewerBlock({ review, step }: { review: ReviewSummary; step: ReplayStep | undefined }) {
  if (review.verdict === "failed") {
    return <p className="word-failed">{reviewFailureText(review.failure_reason)}</p>;
  }
  return (
    <>
      <p>
        <ReviewerWord verdict={review.verdict} />
      </p>
      {step && <p className="meta">{step.explanation}</p>}
    </>
  );
}

function EndPanel({
  run,
  claim,
  onClaim,
  caseLink,
  review,
  evidence,
}: {
  run: RunSteps;
  claim: number | null;
  onClaim: (i: number | null) => void;
  caseLink: boolean;
  review: ReviewSummary | null;
  evidence: Evidence;
}) {
  const outcome = run.steps[run.steps.length - 1];
  const checks = run.steps.find((s) => s.kind === "checks");
  const reviewStep = run.steps.find((s) => s.kind === "review");
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
          <ClaimList
            claims={outcome.claims}
            selected={claim}
            onSelect={onClaim}
            evidence={evidence}
          />
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
        {review && (
          <div>
            <h3>Reviewer agent</h3>
            <ReviewerBlock review={review} step={reviewStep} />
          </div>
        )}
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
  if (!(target instanceof Element)) return false;
  return (
    ["BUTTON", "A", "SUMMARY"].includes(target.tagName) || target.getAttribute("role") === "button"
  );
}

function txnsOf(graph: RunSteps["graph"], edgeId: string | null): number[] {
  if (!edgeId) return [];
  return graph.edges.find((e: FlowEdge) => e.id === edgeId)?.txn_ids ?? [];
}

export interface ReplayProps {
  run: RunSteps;
  /** 0-based; a link like /watch/<run>?step=5 opens at step 5. */
  initialStep?: number;
  /** Guided auto-plays with captions; Explore waits for the visitor. */
  initialMode?: ReplayMode;
  /** Show the link to the queue case page, which exists only when the site reads the API. */
  caseLink?: boolean;
  /** The case file sections, rendered on the server. No tab when absent. */
  caseFile?: ReactNode;
  /** Per step model effort and tool timings, from the bundle. Hidden when absent. */
  effort?: RunEffort | null;
  /** The stored reviewer agent row. The reviewer is shown only when it exists. */
  review?: ReviewSummary | null;
}

export function Replay({
  run,
  initialStep = 0,
  initialMode = "guided",
  caseLink = true,
  caseFile,
  effort = null,
  review = null,
}: ReplayProps) {
  const steps = run.steps;
  const last = steps.length - 1;
  const [mode, setMode] = useState<ReplayMode>(initialMode);
  const [tab, setTab] = useState<Tab>("replay");
  const [index, setIndex] = useState(Math.max(0, Math.min(last, initialStep)));
  const [playing, setPlaying] = useState(initialMode === "guided");
  // With reduced motion the guided tour waits until the visitor presses play.
  const reduced = useReducedMotion();
  const [started, setStarted] = useState(false);
  const [speed, setSpeed] = useState<SpeedKey>("normal");
  const [claim, setClaim] = useState<number | null>(null);
  const [hoverTxns, setHoverTxns] = useState<number[] | null>(null);
  const [pinnedTxn, setPinnedTxn] = useState<number | null>(null);
  const [focusEdge, setFocusEdge] = useState<string | null>(null);
  const [pinnedEdge, setPinnedEdge] = useState<string | null>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const moved = useRef(false);

  const atEnd = index >= last;
  const held = reduced && !started;
  const running = playing && !held && !atEnd && tab === "replay";
  const step = steps[index];
  const factor = SPEEDS.find((s) => s.key === speed)?.factor ?? 1;
  const delays = guidedDelays(steps.map((s) => s.kind));
  const delay = mode === "guided" ? delays[index] : STEP_WEIGHT[step.kind] * factor;

  const go = useCallback(
    (to: number) => {
      moved.current = true;
      setClaim(null);
      setPinnedTxn(null);
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
    setStarted(true);
    if (atEnd) {
      go(0);
      setPlaying(true);
      return;
    }
    setPlaying(!running);
  }, [atEnd, go, running]);

  const restart = useCallback(() => {
    setStarted(true);
    go(0);
    setPlaying(true);
  }, [go]);

  function pickMode(next: ReplayMode) {
    setMode(next);
    if (next === "explore") setPlaying(false);
  }

  // Auto play: one timer per step, cleared on any change.
  useEffect(() => {
    if (!running) return;
    const timer = window.setTimeout(() => {
      moved.current = true;
      setClaim(null);
      setPinnedTxn(null);
      setIndex((i) => Math.min(last, i + 1));
    }, delay);
    return () => window.clearTimeout(timer);
  }, [running, index, delay, last]);

  // Keyboard: arrows step, space plays or pauses, Home and End jump. Replay tab only.
  useEffect(() => {
    if (tab !== "replay") return;
    function onKey(event: KeyboardEvent) {
      if (event.altKey || event.ctrlKey || event.metaKey || isTyping(event.target)) return;
      if (event.key === "ArrowRight") {
        event.preventDefault();
        jump(index + 1);
      } else if (event.key === "ArrowLeft") {
        event.preventDefault();
        jump(index - 1);
      } else if (event.key === "Home") {
        event.preventDefault();
        jump(0);
      } else if (event.key === "End") {
        event.preventDefault();
        jump(last);
      } else if (event.key === " " && !activates(event.target)) {
        event.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [jump, index, last, toggle, tab]);

  // Keep the current step in view once the visitor has moved.
  useEffect(() => {
    if (!moved.current) return;
    const item = listRef.current?.querySelector(`[data-step="${index}"]`);
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    item?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [index]);

  const tabs: { key: Tab; label: string }[] = [
    { key: "replay", label: "Replay" },
    { key: "case", label: "Case file" },
  ];

  function onTabKey(event: ReactKeyboardEvent<HTMLButtonElement>, i: number) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    event.stopPropagation();
    const next = (i + (event.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
    setTab(tabs[next].key);
    tabRefs.current[next]?.focus();
  }

  const outcome = steps.find((s) => s.kind === "outcome");
  const reached = outcome !== undefined && index >= outcome.index;
  const claims = step.claims.length > 0 ? step.claims : atEnd ? (outcome?.claims ?? []) : [];
  const highlight =
    hoverTxns ??
    (pinnedTxn !== null
      ? [pinnedTxn]
      : claim !== null && claims[claim]
        ? claims[claim].txn_ids
        : HIGHLIGHT_KINDS.includes(step.kind)
          ? step.txn_ids
          : []);
  const visible = countVisible(run.graph, index);
  const boardEdge = focusEdge ?? pinnedEdge;
  const evidence: Evidence = {
    hover: hoverTxns,
    setHover: setHoverTxns,
    pinned: pinnedTxn,
    setPinned: setPinnedTxn,
    fromBoard: new Set(txnsOf(run.graph, boardEdge)),
  };
  const shownEdge = boardEdge ? run.graph.edges.find((e) => e.id === boardEdge) : undefined;
  const hasMoney = run.graph.edges.some((e) => (e.transfers?.length ?? 0) > 0);
  const elapsed = delays.slice(0, index).reduce((a, b) => a + b, 0);

  const replayPanel = (
    <>
      <div className="replay-bar" role="toolbar" aria-label="Replay controls">
        {mode === "explore" && (
          <button type="button" className="ctl" onClick={() => jump(index - 1)} disabled={index === 0}>
            <Icon name="back" /> Back
          </button>
        )}
        <button type="button" className="ctl primary" onClick={toggle} aria-pressed={running}>
          <Icon name={atEnd ? "again" : running ? "pause" : "play"} />
          {atEnd ? "Play again" : running ? "Pause" : "Play"}
        </button>
        {mode === "guided" ? (
          <button type="button" className="ctl" onClick={restart} disabled={index === 0 && running}>
            <Icon name="again" /> Restart
          </button>
        ) : (
          <button type="button" className="ctl" onClick={() => jump(index + 1)} disabled={atEnd}>
            Next <Icon name="next" />
          </button>
        )}
        {mode === "explore" ? (
          <label className="scrub">
            <span className="sr-only">Jump to step</span>
            <input
              type="range"
              min={0}
              max={last}
              value={index}
              onChange={(e) => jump(Number(e.target.value))}
            />
          </label>
        ) : (
          <span className={styles.progress} aria-hidden="true">
            <span
              className={styles.progressDone}
              style={{ width: `${(elapsed / GUIDED_TOTAL_MS) * 100}%` }}
            />
            {!atEnd && (
              <span
                key={index}
                className={styles.progressStep}
                style={{
                  left: `${(elapsed / GUIDED_TOTAL_MS) * 100}%`,
                  width: `${(delays[index] / GUIDED_TOTAL_MS) * 100}%`,
                  animationDuration: `${delays[index]}ms`,
                  animationPlayState: running ? "running" : "paused",
                }}
              />
            )}
          </span>
        )}
        <span className="counter">
          Step {index + 1} of {steps.length}
        </span>
        {mode === "explore" && (
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
        )}
      </div>

      <p className="sr-only" aria-live="polite">
        {`Step ${index + 1} of ${steps.length}: ${step.title}. ${step.explanation}`}
      </p>

      {mode === "guided" && (
        <div className={styles.caption} aria-hidden="true">
          <p className={styles.captionMeta}>
            What the agent did, step {index + 1} of {steps.length}: {ACTOR_LABELS[step.actor]}
          </p>
          <p className={styles.captionTitle}>{step.title}</p>
          <p className={styles.captionText}>{step.explanation}</p>
        </div>
      )}

      <div className="replay-grid">
        <section className="timeline-col" aria-labelledby="narration-title">
          <h2 id="narration-title" className={styles.narrationTitle}>
            What the agent did
          </h2>
          <p className={`meta ${styles.narrationNote}`}>
            Plain summaries written by code from the recorded tool calls. They are not the
            model&apos;s own words.
          </p>
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
                    <h3 className="tl-title">{s.title}</h3>
                  ) : (
                    <button type="button" className="tl-title tl-jump" onClick={() => jump(s.index)}>
                      {s.title}
                    </button>
                  )}
                  {current ? (
                    <StepBody
                      step={s}
                      claim={claim}
                      onClaim={setClaim}
                      evidence={evidence}
                      effort={effort}
                    />
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
          {atEnd && (
            <EndPanel
              run={run}
              claim={claim}
              onClaim={setClaim}
              caseLink={caseLink}
              review={review}
              evidence={evidence}
            />
          )}
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
              motion
              label={`Money flow seen so far: ${visible.nodes} accounts and ${visible.edges} links`}
              interactive={{
                idBase: `flow-${run.run_id}`,
                description:
                  "Dots are accounts, arrows are money links between them. Tab to a link to " +
                  "hear its transfers and amounts. The step list on the left tells the same " +
                  "story in words.",
                focusEdge,
                pinnedEdge,
                onEdgeFocus: setFocusEdge,
                onEdgePin: (id) => setPinnedEdge((p) => (p === id ? null : id)),
              }}
            />
          </div>
          <p className={styles.readout}>
            {shownEdge
              ? edgeAmountsText(shownEdge)
              : "Hover or focus a money link to see its transfers and amounts."}
          </p>
          <Legend motion={hasMoney} />
          <Verdict outcome={outcome} reached={reached} />
        </aside>
      </div>
    </>
  );

  return (
    <div className="replay">
      <div className={styles.top}>
        {caseFile ? (
          <div role="tablist" aria-label="Views" className={styles.tabs}>
            {tabs.map((t, i) => (
              <button
                key={t.key}
                ref={(el) => {
                  tabRefs.current[i] = el;
                }}
                type="button"
                role="tab"
                id={`tab-${t.key}`}
                aria-selected={tab === t.key}
                aria-controls={`panel-${t.key}`}
                tabIndex={tab === t.key ? 0 : -1}
                className={styles.tab}
                onClick={() => setTab(t.key)}
                onKeyDown={(e) => onTabKey(e, i)}
              >
                {t.label}
              </button>
            ))}
          </div>
        ) : (
          <span />
        )}
        {tab === "replay" && (
          <div className={styles.modes} role="group" aria-label="Replay mode">
            <button
              type="button"
              className="ctl small"
              aria-pressed={mode === "guided"}
              onClick={() => pickMode("guided")}
            >
              Guided
            </button>
            <button
              type="button"
              className="ctl small"
              aria-pressed={mode === "explore"}
              onClick={() => pickMode("explore")}
            >
              Explore
            </button>
          </div>
        )}
      </div>
      {mode === "guided" && tab === "replay" && (
        <p className={`meta ${styles.modeNote}`}>
          Guided tour: the whole run in about a minute.{" "}
          {held
            ? "It waits for you because your device asks for less motion: press Play to start."
            : "Pause any time, or switch to Explore to step through it yourself."}
        </p>
      )}
      <div
        id="panel-replay"
        role={caseFile ? "tabpanel" : undefined}
        aria-labelledby={caseFile ? "tab-replay" : undefined}
        hidden={tab !== "replay"}
      >
        {replayPanel}
      </div>
      {caseFile && (
        <div
          id="panel-case"
          role="tabpanel"
          aria-labelledby="tab-case"
          hidden={tab !== "case"}
          className={styles.casePanel}
        >
          {caseFile}
        </div>
      )}
    </div>
  );
}

/** The 0-based step to open at from a 1-based ?step= value; the first step otherwise. */
function startAt(asked: number, count: number): number {
  return Number.isInteger(asked) ? Math.min(Math.max(asked, 1), count) - 1 : 0;
}

/** The replay opened at the step in the URL, so /watch/<run>?step=5 opens at step 5 in Explore
 * mode; without ?step= it starts the guided tour. Render it inside a Suspense boundary: reading
 * the query on the client keeps the page static. */
export function StepFromUrl(props: Omit<ReplayProps, "initialStep" | "initialMode">) {
  const raw = useSearchParams().get("step");
  const asked = raw === null ? Number.NaN : Number(raw);
  const deepLink = raw !== null && Number.isInteger(asked);
  return (
    <Replay
      {...props}
      initialStep={startAt(asked, props.run.steps.length)}
      initialMode={deepLink ? "explore" : "guided"}
    />
  );
}
