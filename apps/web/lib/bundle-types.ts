// Types for the committed showcase bundle in content/showcase/v1, written by
// evals/export_showcase.py. Fields the pages do not rely on yet are optional, so a slightly
// different export still type checks; pages must handle them being absent.

import type { RunDetail, RunSteps } from "./api";

/** One transfer drawn on a money-flow edge; amount and currency are the paid side. */
export interface EdgeTransfer {
  txn_id: number;
  ts: string;
  amount: string;
  currency: string;
}

export interface ManifestStory {
  id: string;
  title: string;
  run_ids: string[];
}

export interface ManifestCase {
  run_id: string;
  label: string;
  caption: string;
  story?: string | null;
}

export interface Manifest {
  format: number;
  export_date: string;
  git_commit: string;
  eval_run_id?: string | null;
  ruleset_version?: string | null;
  models?: string[];
  prompt_versions?: string[];
  featured: string | null;
  order: string[];
  stories: ManifestStory[];
  cases: ManifestCase[];
}

export interface Provenance {
  recorded_at?: string | null;
  model?: string | null;
  provider?: string | null;
  prompt_version?: string | null;
  ruleset_version?: string | null;
}

/** Model effort for one tool call step (1-based step number of the run). */
export interface StepEffort {
  step: number;
  tokens_in: number;
  tokens_out: number;
  latency_ms: number;
}

export interface ToolEffort {
  index: number;
  step: number | null;
  name: string;
  ok: boolean;
  duration_ms: number;
}

export interface EffortTotals {
  model_calls?: number;
  tokens_in?: number;
  tokens_out?: number;
  latency_ms?: number;
  wall_ms?: number;
  cost_usd?: string;
}

export interface RunEffort {
  /** null when model calls cannot be matched to tool call steps by order. */
  per_step?: StepEffort[] | null;
  tools?: ToolEffort[] | null;
  totals?: EffortTotals | null;
}

/** One file in runs/<runId>.json. */
export interface BundleRun {
  steps: RunSteps;
  case: RunDetail;
  provenance?: Provenance | null;
  effort?: RunEffort | null;
}

/** False and real alerts in one split, and how many of each the agent would close. */
export interface SplitCounts {
  false_closed?: number;
  false_total?: number;
  real_closed?: number;
  real_total?: number;
}

/** One population of alerts. Nested scores stay loose until the results page is designed. */
export interface ResultsSplit {
  n?: number | null;
  counts?: SplitCounts | null;
  baseline?: Record<string, unknown> | null;
  agent_with_human_review?: { strict?: Record<string, unknown> | null } | null;
}

/** results.json: counts from one scored eval run. */
export interface Results {
  eval_run_id?: string | null;
  dataset?: string | null;
  model?: string | null;
  prompt_version?: string | null;
  late_cutoff?: string | null;
  headline?: unknown;
  splits?: { early?: ResultsSplit | null; all?: ResultsSplit | null } | null;
  human_review?: unknown;
  rules_171?: unknown;
  notes?: string[] | null;
}
