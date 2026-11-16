"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { ApiError, postDecision, type Decision } from "../../../lib/api";
import { isValidRunId, webDecisionsEnabled } from "../../../lib/flags";

const MAX_NOTE = 2000;

type DecisionOutcome =
  | "approved"
  | "rejected"
  | "note-required"
  | "note-too-long"
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
  if (err.status === 422) return "note-required";
  return "error";
}

/** Posts the decision from the server, adding the reviewer token from the server env, then
 * sends the reviewer back to the case page with the outcome in the URL. Refused before any API
 * call unless WEB_DECISIONS_ENABLED is "1" and the run id has the expected format. */
export async function submitDecision(runId: string, formData: FormData): Promise<void> {
  if (!isValidRunId(runId)) {
    redirect("/queue?outcome=invalid");
  }
  if (!webDecisionsEnabled()) {
    redirect(`/queue/${runId}?outcome=disabled`);
  }
  const raw = formData.get("decision");
  const noteRaw = formData.get("note");
  const note = typeof noteRaw === "string" ? noteRaw.trim() : "";
  let outcome: DecisionOutcome;
  if (raw !== "approve" && raw !== "reject") {
    outcome = "invalid";
  } else if (note.length > MAX_NOTE) {
    outcome = "note-too-long";
  } else if (raw === "reject" && note === "") {
    outcome = "note-required";
  } else {
    const decision: Decision = raw;
    try {
      const result = await postDecision(runId, { decision, note: note === "" ? null : note });
      outcome = result.event;
    } catch (err) {
      outcome = outcomeFor(err);
    }
  }
  revalidatePath("/queue");
  revalidatePath(`/queue/${encodeURIComponent(runId)}`);
  redirect(`/queue/${encodeURIComponent(runId)}?outcome=${outcome}`);
}
