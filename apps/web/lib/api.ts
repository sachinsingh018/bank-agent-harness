// Typed helpers for the approval queue API. Server side only: import this module from
// server components and server actions, never from a client component. The reviewer token
// is read from the server environment here and never reaches the browser, and is only sent
// when WEB_DECISIONS_ENABLED is "1".

import type { EdgeTransfer } from "./bundle-types";
import { webDecisionsEnabled } from "./flags";

export type QueueState = "waiting" | "decided" | "all";
export type Decision = "approve" | "reject" | "approve_with_edits";
export type DecisionEvent = "approved" | "rejected" | "edited";
export type CorrectionKind = "recommendation" | "typology" | "confidence" | "evidence" | "other";
/** The checker's verdict in maker-checker; "failed" when the checker did not finish. */
export type ReviewState = "approve" | "reject" | "failed";

export interface DecisionInfo {
  decision: Decision;
  actor: string;
  at: string;
  note: string | null;
}

export interface QueueItem {
  run_id: string;
  case_id: string;
  module: string;
  status: string;
  reason: string | null;
  recommendation: string | null;
  confidence: number | null;
  typology: string | null;
  created_at: string;
  decision: DecisionInfo | null;
  review_verdict: ReviewState | null;
}

export interface QueuePage {
  state: QueueState;
  items: QueueItem[];
  next_cursor: string | null;
}

export interface VerifierOut {
  name: string;
  passed: boolean;
  errors: string[];
}

export interface TotalsOut {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
}

export interface ModelInfo {
  profile: string;
  provider: string;
  model: string;
}

export interface PromptInfo {
  version: string;
  sha256: string;
}

export interface ModelCallOut {
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: string;
  latency_ms: number;
  stop_reason: string;
}

export interface ToolCallOut {
  index: number;
  step: number | null;
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  error_code: string | null;
  duration_ms: number;
  size: number;
  truncated: boolean;
}

export interface EventOut {
  event: string;
  actor: string;
  at: string;
  payload: Record<string, unknown>;
}

export interface AccountRef {
  bank: number;
  acct: string;
}

export interface Money {
  value: string;
  currency: string;
}

export interface TransferRecord {
  txn_id: number;
  ts: string;
  from_account: AccountRef;
  to_account: AccountRef;
  amount_paid: Money;
  amount_received: Money;
  format: string;
}

export interface ReviewOut {
  verdict: ReviewState;
  failure_reason: string | null;
  review: Record<string, unknown> | null;
  verifier_results: VerifierOut[];
  usage: TotalsOut;
  cost_usd: string;
  tool_steps: number;
  wall_ms: number;
  model: ModelInfo;
  prompt: PromptInfo;
  created_at: string;
}

export interface LessonOut {
  id: number;
  text: string;
}

export interface RunDetail {
  run_id: string;
  case_id: string;
  module: string;
  status: string;
  reason: string | null;
  findings: Record<string, unknown> | null;
  verifier_results: VerifierOut[];
  totals: TotalsOut;
  cost_usd: string;
  tool_steps: number;
  wall_ms: number;
  model: ModelInfo;
  prompt: PromptInfo;
  eval_run_id: string | null;
  created_at: string;
  model_calls: ModelCallOut[];
  tool_calls: ToolCallOut[] | null;
  events: EventOut[];
  cited_transfers: TransferRecord[];
  missing_txn_ids: number[];
  not_fetched_txn_ids: number[];
  decision: DecisionInfo | null;
  review: ReviewOut | null;
  // Saved reviewer lessons shown to the agent at the start of the run. Optional so the page
  // still works against an API from before corrections memory.
  lessons?: LessonOut[];
  // Langfuse trace id when the run was traced. Optional for APIs from before tracing.
  trace_id?: string | null;
}

export interface FindingsEdits {
  recommendation: string;
  confidence: number;
  typology: string;
  narrative: string;
}

export interface CorrectionIn {
  kind: CorrectionKind;
  rule: string;
}

export interface DecisionRequest {
  decision: Decision;
  note: string | null;
  edits?: FindingsEdits;
  correction?: CorrectionIn;
}

export interface DecisionResponse {
  run_id: string;
  case_id: string;
  event: DecisionEvent;
  actor: string;
  hash: string;
  correction_id: number | null;
}

export interface CorrectionOut {
  id: number;
  created_at: string;
  module: string;
  case_id: string;
  run_id: string;
  decision_event_id: number;
  kind: string;
  rule: string;
  author: string;
}

export interface CorrectionsPage {
  items: CorrectionOut[];
  next_cursor: string | null;
}

// Eval runs (GET /evals, GET /evals/{id}). Counts and rates only: the API whitelists every
// field, and no per alert truth is ever returned.

export interface Confusion {
  n: number | null;
  flagged: number | null;
  positives: number | null;
  tp: number | null;
  fp: number | null;
  fn: number | null;
  tn: number | null;
  precision: number | null;
  recall: number | null;
}

export interface TruthPair {
  strict: Confusion | null;
  loose: Confusion | null;
}

export interface SplitScore {
  alerts: number | null;
  baseline: {
    raised: number | null;
    true_strict: number | null;
    false_strict: number | null;
    precision_strict: number | null;
    precision_loose: number | null;
    recall_strict: number | null;
  } | null;
  agent_with_human_review: TruthPair | null;
  agent_proposed_only: TruthPair | null;
  human_review: { count: number | null; rate: number | null; reasons: Record<string, number> } | null;
  fp_reduction: {
    at_target_recall: {
      target_recall: number | null;
      threshold: number | null;
      flagged: number | null;
      tp: number | null;
      fp: number | null;
      recall: number | null;
      baseline_fp: number | null;
      fp_reduction: number | null;
    } | null;
    at_agent_decisions: {
      flagged: number | null;
      tp: number | null;
      fp: number | null;
      recall: number | null;
      baseline_fp: number | null;
      fp_reduction: number | null;
    } | null;
  } | null;
  typology: {
    strict_positives_with_typology: number | null;
    correct: number | null;
    accuracy: number | null;
    none_rate_on_negatives: number | null;
  } | null;
  grounding: { claims: number | null; grounded: number | null; rate: number | null } | null;
  calibration: { n: number | null; ece: number | null; indicative: boolean | null } | null;
  effort: {
    total_cost_usd: string | null;
    cost_per_alert_usd: string | null;
    latency_ms_mean: number | null;
    tool_steps_mean: number | null;
  } | null;
  review: {
    reviewed: number | null;
    approved: number | null;
    rejected: number | null;
    failed: number | null;
    reject_rate: number | null;
    precision_change: number | null;
    recall_change: number | null;
  } | null;
}

export type SplitName = "early" | "late" | "all";

export interface ScoreOut {
  headline: string | null;
  late_cutoff: string | null;
  target_recall: number | null;
  splits: Record<SplitName, SplitScore | null> | null;
  lessons: { enabled: boolean | null; cases_with_lessons: number | null } | null;
  notes: string[];
}

export interface Headline {
  split: string;
  alerts: number | null;
  precision: number | null;
  recall: number | null;
  baseline_precision: number | null;
  human_review_rate: number | null;
  reviewer_reject_rate: number | null;
}

export interface EvalRunSummary {
  eval_run_id: string;
  started_at: string;
  finished_at: string | null;
  dataset: string;
  dataset_version: string;
  provider: string;
  model: string;
  prompt_version: string;
  cases: number | null;
  total_cost_usd: string | null;
  scored: boolean;
  headline: Headline | null;
}

export interface EvalRunsPage {
  items: EvalRunSummary[];
}

export interface EvalRunDetail extends EvalRunSummary {
  dataset_sha256: string;
  git_commit: string | null;
  git_dirty: boolean | null;
  profile: string;
  prompt_sha256: string;
  limits: Record<string, number>;
  counts: {
    total: number | null;
    by_status: Record<string, number>;
    by_human_review_reason: Record<string, number>;
    by_review_verdict: Record<string, number>;
  } | null;
  scored_at: string | null;
  score: ScoreOut | null;
}

// Watch an investigation (GET /showcase, GET /runs/{id}/steps). Recorded runs replayed as
// steps; every explanation is written by deterministic code in the API, not by a model.

export type StepKind =
  | "alert"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "retry"
  | "checks"
  | "review"
  | "outcome";
export type StepActor = "rule" | "agent" | "tools" | "harness" | "reviewer";

export interface AlertCard {
  alert_id: string;
  detector: string | null;
  detector_label: string | null;
  summary: string | null;
  anchor: string | null;
  txn_count: number | null;
  account_count: number | null;
  window_start: string | null;
  window_end: string | null;
}

export interface FlowNode {
  id: string;
  bank: number;
  acct: string;
  role: "anchor" | "alert" | "found";
  x: number;
  y: number;
  step: number;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  count: number;
  txn_ids: number[];
  step: number;
  /** The transfers behind the edge, from the showcase bundle. Absent from the live API. */
  transfers?: EdgeTransfer[];
}

export interface FlowGraph {
  nodes: FlowNode[];
  edges: FlowEdge[];
  hidden_accounts: number;
}

export interface CheckOut {
  name: string;
  passed: boolean;
  errors: string[];
}

export interface ClaimOut {
  statement: string;
  txn_ids: number[];
}

export interface ReplayStep {
  index: number;
  kind: StepKind;
  actor: StepActor;
  title: string;
  explanation: string;
  tool: string | null;
  arguments: Record<string, unknown> | null;
  ok: boolean | null;
  summary: string | null;
  raw: string | null;
  /** True when the bundle export cut raw short. Absent from the live API. */
  raw_truncated?: boolean;
  text: string | null;
  checks: CheckOut[];
  errors: string[];
  reasons: string[];
  claims: ClaimOut[];
  verdict: string | null;
  status: string | null;
  reason: string | null;
  recommendation: string | null;
  confidence: number | null;
  typology: string | null;
  narrative: string | null;
  txn_ids: number[];
}

export interface RunSteps {
  run_id: string;
  case_id: string;
  module: string;
  created_at: string;
  status: string;
  reason: string | null;
  model: ModelInfo;
  eval_run_id: string | null;
  tool_steps: number;
  wall_ms: number;
  cost_usd: string;
  alert: AlertCard;
  steps: ReplayStep[];
  graph: FlowGraph;
}

export interface ShowcaseItem {
  run_id: string;
  case_id: string;
  created_at: string;
  status: string;
  reason: string | null;
  recommendation: string | null;
  confidence: number | null;
  typology: string | null;
  review_verdict: ReviewState | null;
  tool_steps: number;
  model: ModelInfo;
  alert: AlertCard;
  graph: FlowGraph;
  teaser: string;
}

export interface ShowcasePage {
  items: ShowcaseItem[];
}

/** status 0 means the API could not be reached. detail is the API's generic message; errors
 * lists the reasons of a 422 (failed checks or invalid fields), empty otherwise. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly errors: string[] = [],
  ) {
    super(`API error ${status}`);
  }
}

/** Requests give up after this long, so a sleeping free-tier API shows a friendly message
 * instead of a page that hangs. */
const API_TIMEOUT_MS = 20_000;

/** Shown when the API cannot be reached or does not answer in time (status 0). */
export const API_WAKING_MESSAGE =
  "The API is waking up (free hosting sleeps when idle). Try again in about a minute.";

function baseUrl(): string {
  return (process.env.API_BASE_URL ?? "http://localhost:8000").replace(/\/+$/, "");
}

/** One message per item of a list detail: plain strings from the API's own checks, or the
 * msg of a request validation error. */
function errorsOf(detail: unknown): string[] {
  if (!Array.isArray(detail)) return [];
  return detail.flatMap((item: unknown) => {
    if (typeof item === "string") return [item];
    if (item && typeof item === "object" && "msg" in item) {
      const msg = (item as { msg: unknown }).msg;
      if (typeof msg === "string") return [msg.replace(/^Value error, /, "")];
    }
    return [];
  });
}

async function detailOf(response: Response): Promise<{ detail: string; errors: string[] }> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return { detail, errors: [] };
      return { detail: "invalid request", errors: errorsOf(detail) };
    }
  } catch {
    // not JSON
  }
  return { detail: response.statusText || "error", errors: [] };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      ...init,
      cache: "no-store",
      signal: AbortSignal.timeout(API_TIMEOUT_MS),
    });
  } catch {
    // Connection failure or timeout.
    throw new ApiError(0, "the API is unreachable");
  }
  if (!response.ok) {
    const { detail, errors } = await detailOf(response);
    throw new ApiError(response.status, detail, errors);
  }
  return (await response.json()) as T;
}

export function getQueue(state: QueueState, cursor?: string, limit = 50): Promise<QueuePage> {
  const params = new URLSearchParams({ state, limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return request<QueuePage>(`/queue?${params.toString()}`);
}

export function getRun(runId: string): Promise<RunDetail> {
  return request<RunDetail>(`/runs/${encodeURIComponent(runId)}`);
}

export function getCorrections(cursor?: string, limit = 50): Promise<CorrectionsPage> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return request<CorrectionsPage>(`/corrections?${params.toString()}`);
}

export function getEvals(limit = 20): Promise<EvalRunsPage> {
  return request<EvalRunsPage>(`/evals?limit=${String(limit)}`);
}

export function getEval(evalRunId: string): Promise<EvalRunDetail> {
  return request<EvalRunDetail>(`/evals/${encodeURIComponent(evalRunId)}`);
}

export function postDecision(runId: string, body: DecisionRequest): Promise<DecisionResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = webDecisionsEnabled() ? process.env.REVIEWER_TOKEN : undefined;
  if (token) headers["X-Reviewer-Token"] = token;
  return request<DecisionResponse>(`/runs/${encodeURIComponent(runId)}/decision`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
}

export function getShowcase(limit = 200): Promise<ShowcasePage> {
  return request<ShowcasePage>(`/showcase?limit=${String(limit)}`);
}

export function getRunSteps(runId: string): Promise<RunSteps> {
  return request<RunSteps>(`/runs/${encodeURIComponent(runId)}/steps`);
}
