// Typed helpers for the approval queue API. Server side only: import this module from
// server components and server actions, never from a client component. The reviewer token
// is read from the server environment here and never reaches the browser, and is only sent
// when WEB_DECISIONS_ENABLED is "1".

import { webDecisionsEnabled } from "./flags";

export type QueueState = "waiting" | "decided" | "all";
export type Decision = "approve" | "reject" | "approve_with_edits";
export type DecisionEvent = "approved" | "rejected" | "edited";
export type CorrectionKind = "recommendation" | "typology" | "confidence" | "evidence" | "other";

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
