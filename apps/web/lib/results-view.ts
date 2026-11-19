// Plain counts read from results.json for the landing and results pages. Every number comes
// from the bundle; when a field is missing the reader returns null and the page hides that
// part instead of showing a made up value.

import type { Results, ResultsSplit } from "./bundle-types";

function num(record: unknown, key: string): number | null {
  if (record === null || typeof record !== "object") return null;
  const value = (record as Record<string, unknown>)[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function rec(record: unknown, key: string): Record<string, unknown> | null {
  if (record === null || typeof record !== "object") return null;
  const value = (record as Record<string, unknown>)[key];
  return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

/** How one approach handled one population of alerts, in counts. */
export interface Outcome {
  falseClosed: number;
  falseTotal: number;
  realClosed: number;
  realTotal: number;
}

export interface SplitView {
  n: number;
  agent: Outcome;
  /** Rules alone on the same alerts, or null when the baseline is missing. */
  rules: (Outcome & { raised: number }) | null;
}

/** The agent with human review and the same-population rules baseline for one split. */
export function splitView(split: ResultsSplit | null | undefined): SplitView | null {
  const c = split?.counts;
  const n = split?.n;
  if (
    typeof n !== "number" ||
    typeof c?.false_closed !== "number" ||
    typeof c.false_total !== "number" ||
    typeof c.real_closed !== "number" ||
    typeof c.real_total !== "number"
  ) {
    return null;
  }
  const agent: Outcome = {
    falseClosed: c.false_closed,
    falseTotal: c.false_total,
    realClosed: c.real_closed,
    realTotal: c.real_total,
  };

  // Rules escalate every alert they raise, so they close only what their recall leaves out.
  const b = split?.baseline;
  const raised = num(b, "raised");
  const realTotal = num(b, "true_strict");
  const falseTotal = num(b, "false_strict");
  const recall = num(b, "recall_strict");
  let rules: SplitView["rules"] = null;
  if (raised !== null && realTotal !== null && falseTotal !== null && recall !== null) {
    const realEscalated = Math.round(recall * realTotal);
    const falseEscalated = raised - realEscalated;
    rules = {
      raised,
      falseClosed: falseTotal - falseEscalated,
      falseTotal,
      realClosed: realTotal - realEscalated,
      realTotal,
    };
  }
  return { n, agent, rules };
}

export interface HumanReviewView {
  count: number;
  reasons: { key: string; count: number }[];
}

export function humanReviewView(results: Results): HumanReviewView | null {
  const count = num(results.human_review, "count");
  if (count === null) return null;
  const raw = rec(results.human_review, "reasons") ?? {};
  const reasons = Object.entries(raw)
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .map(([key, value]) => ({ key, count: value }))
    .sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
  return { count, reasons };
}

export interface RulesContext {
  alerts: number;
  real: number;
  false: number;
  population: string | null;
}

export function rulesContext(results: Results): RulesContext | null {
  const r = results.rules_171;
  const alerts = num(r, "alerts");
  const real = num(r, "true_strict");
  const falseCount = num(r, "false_strict");
  if (alerts === null || real === null || falseCount === null) return null;
  const population =
    r !== null && typeof r === "object" ? (r as Record<string, unknown>).population : null;
  return {
    alerts,
    real,
    false: falseCount,
    population: typeof population === "string" ? population : null,
  };
}

/** Plain words for the reasons a case was routed to analyst review. Unknown keys show as recorded. */
export const REVIEW_REASONS: Record<string, string> = {
  verifier_failed: "failed the deterministic checks after one retry",
  limit_steps: "ran out of steps before answering",
  limit_tokens: "ran out of tokens before answering",
  limit_time: "ran out of time before answering",
  schema_failed: "gave an answer in the wrong shape after one retry",
  reviewer_failed: "the reviewer agent did not finish",
  reviewer_rejected: "the reviewer agent rejected the answer",
};
