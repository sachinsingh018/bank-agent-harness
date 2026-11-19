// Small pure helpers for the watch pages. Safe to import from client components: no server
// env, no fetch. Types come from api.ts as type-only imports.

import type { FlowGraph, ShowcaseItem, StepActor, StepKind } from "./api";
import type { RunEffort, StepEffort, ToolEffort } from "./bundle-types";

export const PATTERNS = [
  { key: "fan-out", label: "Fan-out" },
  { key: "fan-in", label: "Fan-in" },
  { key: "cycle", label: "Cycle" },
  { key: "scatter-gather", label: "Scatter-gather" },
  { key: "gather-scatter", label: "Gather-scatter" },
] as const;

export type OutcomeKey = "escalate" | "close" | "person";

export const OUTCOMES: { key: OutcomeKey; label: string }[] = [
  { key: "escalate", label: "Escalate" },
  { key: "close", label: "Close" },
  { key: "person", label: "Analyst review" },
];

export function patternLabel(detector: string | null): string {
  const found = PATTERNS.find((p) => p.key === detector);
  return found ? found.label : "Alert";
}

/** Where a case ended: a proposal to escalate or close, or analyst review. */
export function outcomeOf(item: { status: string; recommendation: string | null }): OutcomeKey {
  if (item.status !== "proposed") return "person";
  return item.recommendation === "escalate" ? "escalate" : "close";
}

export function outcomeLabel(key: OutcomeKey): string {
  if (key === "escalate") return "Recommended: escalate";
  if (key === "close") return "Recommended: close";
  return "Routed to analyst review";
}

/** The mark class from globals.css for an outcome. */
export function outcomeMark(key: OutcomeKey): "escalate" | "close" | "review" {
  return key === "person" ? "review" : key;
}

export const ACTOR_LABELS: Record<StepActor, string> = {
  rule: "Monitoring rule",
  agent: "Agent",
  tools: "Data tools",
  harness: "Harness",
  reviewer: "Reviewer agent",
};

export function modelLabel(model: { provider: string; model: string }): string {
  if (model.provider === "scripted") return "scripted test run";
  const name = model.model.split("/").pop() ?? model.model;
  return name.replace(/:free$/, "");
}

/** A featured case for the landing page: a real model run that got through the checks, with
 * a few tool calls, else the newest run. */
export function pickFeatured(items: ShowcaseItem[]): ShowcaseItem | null {
  const real = items.filter((i) => i.model.provider !== "scripted" && i.graph.nodes.length > 2);
  const good = real.filter((i) => i.status === "proposed" && i.tool_steps >= 4);
  return good[0] ?? real[0] ?? items[0] ?? null;
}

export function countVisible(graph: FlowGraph, upTo: number): { nodes: number; edges: number } {
  return {
    nodes: graph.nodes.filter((n) => n.step <= upTo).length,
    edges: graph.edges.filter((e) => e.step <= upTo).length,
  };
}

/** Argument values as short text for the step chips. */
export function argText(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text === undefined) return "";
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}

// ------------------------------------------------------------------ replay pacing and effort

/** Guided mode plays the whole run in about this long. */
export const GUIDED_TOTAL_MS = 60_000;

/** Relative reading time per step kind; guided mode scales these to GUIDED_TOTAL_MS. */
export const STEP_WEIGHT: Record<StepKind, number> = {
  alert: 3400,
  thinking: 3600,
  tool_call: 2200,
  tool_result: 2900,
  retry: 3400,
  checks: 3000,
  review: 3400,
  outcome: 0,
};

/** Milliseconds to stay on each step in guided mode, so the run lasts about total ms. The
 * last step has no delay: the replay stops there. */
export function guidedDelays(kinds: readonly StepKind[], total = GUIDED_TOTAL_MS): number[] {
  const weights = kinds.map((k, i) => (i === kinds.length - 1 ? 0 : STEP_WEIGHT[k] || 1));
  const sum = weights.reduce((a, b) => a + b, 0) || 1;
  return weights.map((w) => Math.round((w / sum) * total));
}

/** Model effort and tool timing for one step, from the bundle effort block. */
export function effortFor(
  effort: RunEffort | null | undefined,
  stepIndex: number,
): { model: StepEffort | null; tool: ToolEffort | null } {
  return {
    model: effort?.per_step?.find((e) => e.step === stepIndex) ?? null,
    tool: effort?.tools?.find((t) => t.step === stepIndex) ?? null,
  };
}

const REVIEW_FAILURES: Record<string, string> = {
  limit_time: "time limit",
  limit_steps: "step limit",
  limit_tokens: "token limit",
  model_error: "model error",
};

/** Plain words for why the reviewer agent stopped, from its stored failure_reason. */
export function reviewFailureText(reason: string | null | undefined): string {
  const why = reason ? (REVIEW_FAILURES[reason] ?? reason.replaceAll("_", " ")) : null;
  return why
    ? `The reviewer agent did not finish (${why}).`
    : "The reviewer agent did not finish.";
}

/** An amount with its currency, grouped for reading: "10,740.09 US Dollar". */
export function amountText(amount: string, currency: string): string {
  const n = Number(amount);
  const value = Number.isFinite(n)
    ? n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : amount;
  return `${value} ${currency}`;
}

/** Recorded timestamps as "2022-09-06 17:24" (data time, no zone conversion). */
export function shortTs(ts: string): string {
  return ts.replace("T", " ").slice(0, 16);
}

/** Total tokens, in and out, as a grouped number. */
export function tokenText(tokensIn: number, tokensOut: number): string {
  return `${(tokensIn + tokensOut).toLocaleString("en-US")} tokens`;
}
