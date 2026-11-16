"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import {
  ApiError,
  postDecision,
  type CorrectionKind,
  type DecisionRequest,
} from "../../../lib/api";
import { isValidRunId, webDecisionsEnabled } from "../../../lib/flags";
import { CORRECTION_KINDS } from "../../../lib/format";

const MAX_NOTE = 2000;
const MIN_RULE = 10;
const MAX_RULE = 500;
const MAX_NARRATIVE = 1200;
const MAX_SHOWN_ERRORS = 10;
const MAX_ERROR_LENGTH = 300;

type DecisionOutcome =
  | "approved"
  | "rejected"
  | "edited"
  | "note-required"
  | "note-too-long"
  | "edits-invalid"
  | "rule-invalid"
  | "checks-failed"
  | "invalid"
  | "disabled"
  | "forbidden"
  | "conflict"
  | "not-found"
  | "unreachable"
  | "error";

function outcomeFor(err: unknown): DecisionOutcome {
  if (!(err instanceof ApiError)) return "error";
  if (err.status === 0) return "unreachable";
  if (err.status === 403) {
    return err.detail === "decisions are disabled on this server" ? "disabled" : "forbidden";
  }
  if (err.status === 409) return "conflict";
  if (err.status === 404) return "not-found";
  if (err.status === 422) return "checks-failed";
  return "error";
}

function text(formData: FormData, name: string): string {
  const value = formData.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function isCorrectionKind(value: string): value is CorrectionKind {
  return (CORRECTION_KINDS as readonly string[]).includes(value);
}

/** Builds the request from the form, or returns the outcome that explains what is missing. */
function buildRequest(formData: FormData): DecisionRequest | DecisionOutcome {
  const decision = formData.get("decision");
  const note = text(formData, "note");
  if (decision !== "approve" && decision !== "reject" && decision !== "approve_with_edits") {
    return "invalid";
  }
  if (note.length > MAX_NOTE) return "note-too-long";
  if (decision !== "approve" && note === "") return "note-required";
  const body: DecisionRequest = { decision, note: note === "" ? null : note };

  if (decision === "approve_with_edits") {
    const confidence = Number(text(formData, "edit_confidence"));
    const narrative = text(formData, "edit_narrative");
    const recommendation = text(formData, "edit_recommendation");
    const typology = text(formData, "edit_typology");
    if (
      !Number.isFinite(confidence) ||
      text(formData, "edit_confidence") === "" ||
      narrative === "" ||
      narrative.length > MAX_NARRATIVE ||
      recommendation === "" ||
      typology === ""
    ) {
      return "edits-invalid";
    }
    body.edits = { recommendation, confidence, typology, narrative };
  }

  const kind = text(formData, "correction_kind");
  const rule = text(formData, "correction_rule");
  if (kind !== "" || rule !== "") {
    if (!isCorrectionKind(kind) || rule.length < MIN_RULE || rule.length > MAX_RULE) {
      return "rule-invalid";
    }
    body.correction = { kind, rule };
  }
  return body;
}

/** Posts the decision from the server, adding the reviewer token from the server env, then
 * sends the reviewer back to the case page with the outcome (and any check errors) in the URL.
 * Refused before any API call unless WEB_DECISIONS_ENABLED is "1" and the run id has the
 * expected format. */
export async function submitDecision(runId: string, formData: FormData): Promise<void> {
  if (!isValidRunId(runId)) {
    redirect("/queue?outcome=invalid");
  }
  if (!webDecisionsEnabled()) {
    redirect(`/queue/${runId}?outcome=disabled`);
  }
  const built = buildRequest(formData);
  let outcome: DecisionOutcome;
  let errors: string[] = [];
  if (typeof built === "string") {
    outcome = built;
  } else {
    try {
      const result = await postDecision(runId, built);
      outcome = result.event;
    } catch (err) {
      outcome = outcomeFor(err);
      if (err instanceof ApiError) errors = err.errors;
    }
  }
  const params = new URLSearchParams({ outcome });
  for (const e of errors.slice(0, MAX_SHOWN_ERRORS)) {
    params.append("error", e.slice(0, MAX_ERROR_LENGTH));
  }
  revalidatePath("/queue");
  revalidatePath("/corrections");
  revalidatePath(`/queue/${encodeURIComponent(runId)}`);
  redirect(`/queue/${encodeURIComponent(runId)}?${params.toString()}`);
}
