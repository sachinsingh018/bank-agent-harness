// Small display helpers and a defensive reader for the findings JSON.

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.toISOString().slice(0, 19).replace("T", " ")} UTC`;
}

export function formatConfidence(value: number | null): string {
  return value === null ? "" : value.toFixed(2);
}

export function formatStatus(status: string, reason: string | null): string {
  if (status === "human_review") {
    return reason ? `human review (${reason.replaceAll("_", " ")})` : "human review";
  }
  return status;
}

/** The checker's verdict for display; empty when the checker did not run. */
export function reviewLabel(verdict: string | null): string {
  if (verdict === "approve") return "approved";
  if (verdict === "reject") return "rejected";
  if (verdict === "failed") return "failed";
  return "";
}

export interface CitedAmount {
  txn_id: number;
  side: string;
  value: string;
  currency: string;
}

export interface Claim {
  statement: string;
  txn_ids: number[];
  amounts: CitedAmount[];
}

export interface Figure {
  kind: string;
  value: string;
  currency: string | null;
  side: string | null;
  txn_ids: number[];
}

export interface Findings {
  narrative: string;
  claims: Claim[];
  figures: Figure[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function optStr(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function ids(value: unknown): number[] {
  return Array.isArray(value)
    ? value.filter((v): v is number => typeof v === "number" && Number.isInteger(v))
    : [];
}

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecord) : [];
}

export function readFindings(findings: Record<string, unknown> | null): Findings | null {
  if (findings === null) return null;
  return {
    narrative: str(findings.narrative),
    claims: records(findings.claims).map((c) => ({
      statement: str(c.statement),
      txn_ids: ids(c.txn_ids),
      amounts: records(c.amounts).map((a) => ({
        txn_id: typeof a.txn_id === "number" ? a.txn_id : 0,
        side: str(a.side),
        value: str(a.value),
        currency: str(a.currency),
      })),
    })),
    figures: records(findings.figures).map((f) => ({
      kind: str(f.kind),
      value: str(f.value),
      currency: optStr(f.currency),
      side: optStr(f.side),
      txn_ids: ids(f.txn_ids),
    })),
  };
}

export function findingText(findings: Record<string, unknown> | null, key: string): string {
  if (findings === null) return "";
  const value = findings[key];
  if (typeof value === "string") return value;
  if (typeof value === "number") return value.toFixed(2);
  return "";
}

// Choices for the "approve with edits" form. The API checks the values with the module's
// output model, so these lists only make the form easier to fill in.
export const RECOMMENDATIONS = ["escalate", "close"] as const;
export const TYPOLOGIES = [
  "FAN-OUT",
  "FAN-IN",
  "CYCLE",
  "SCATTER-GATHER",
  "GATHER-SCATTER",
  "STACK",
  "BIPARTITE",
  "RANDOM",
  "NONE",
] as const;
export const CORRECTION_KINDS = [
  "recommendation",
  "typology",
  "confidence",
  "evidence",
  "other",
] as const;

/** A rate in [0, 1] as a whole percent; "n/a" when there is nothing to divide by. */
export function formatRate(value: number | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  return `${Math.round(value * 100)}%`;
}

/** A change in a rate, in percentage points, with a word for the direction. */
export function formatPointChange(value: number | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  const points = Math.round(value * 100);
  if (points === 0) return "no change";
  return points > 0 ? `up ${points} points` : `down ${Math.abs(points)} points`;
}

export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? "n/a" : String(value);
}

export function formatUsd(value: string | null | undefined): string {
  if (value === null || value === undefined) return "n/a";
  const n = Number(value);
  if (!Number.isFinite(n)) return value;
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

export function shortHash(value: string | null | undefined, length = 8): string {
  return value ? value.slice(0, length) : "n/a";
}

/** The visual kind of an agent answer: drives the colour and the shape of its mark. */
export type SaysKind = "escalate" | "close" | "review" | "none";

export function saysKind(recommendation: string | null, status: string): SaysKind {
  if (recommendation === "escalate") return "escalate";
  if (recommendation === "close") return "close";
  if (status === "human_review") return "review";
  return "none";
}

export function saysLabel(kind: SaysKind): string {
  if (kind === "escalate") return "Escalate";
  if (kind === "close") return "Close";
  return "No answer";
}

/** Human review reasons in plain words. */
export function reasonLabel(reason: string | null): string {
  return reason ? reason.replaceAll("_", " ") : "sent to a person";
}

/** Verifier names in user words ("Checks", not "verifiers"). */
export function checkLabel(name: string): string {
  return name.replaceAll("_", " ");
}

export function actorLabel(actor: string): string {
  if (actor.startsWith("human:")) return actor.slice("human:".length);
  return actor.replaceAll("_", " ");
}

export function decisionLabel(decision: string): string {
  if (decision === "approve") return "Approved";
  if (decision === "reject") return "Rejected";
  if (decision === "approve_with_edits") return "Approved with edits";
  return decision;
}
