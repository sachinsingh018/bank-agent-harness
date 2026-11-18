import Link from "next/link";
import { notFound } from "next/navigation";

import { API_WAKING_MESSAGE, ApiError, getRun, type RunDetail, type TransferRecord } from "../../../lib/api";
import {
  CORRECTION_KINDS,
  RECOMMENDATIONS,
  TYPOLOGIES,
  decisionLabel,
  findingText,
  formatStatus,
  formatTime,
  readFindings,
  reviewLabel,
  type Claim,
} from "../../../lib/format";
import { isValidRunId, webDecisionsEnabled } from "../../../lib/flags";
import { submitDecision } from "./actions";

const OUTCOME_TEXT: Record<string, { kind: "success" | "error"; text: string }> = {
  approved: { kind: "success", text: "Approved. The decision is in the audit log." },
  rejected: { kind: "success", text: "Rejected. The decision is in the audit log." },
  edited: {
    kind: "success",
    text: "Approved with edits. The edited answer passed every check and is in the audit log.",
  },
  "note-required": {
    kind: "error",
    text: "A rejection or an approval with edits needs a note explaining why.",
  },
  "edits-invalid": {
    kind: "error",
    text: "Fill in every edit field: recommendation, confidence, typology and narrative.",
  },
  "rule-invalid": {
    kind: "error",
    text: "A lesson needs a kind and a rule of 10 to 500 characters (or leave both empty).",
  },
  "checks-failed": {
    kind: "error",
    text: "The decision was not saved. Fix these problems and try again:",
  },
  "note-too-long": { kind: "error", text: "The note is too long (2000 characters at most)." },
  invalid: { kind: "error", text: "Choose approve, approve with edits or reject." },
  disabled: {
    kind: "error",
    text:
      "Decisions are disabled: either this web deployment does not allow them, or the API " +
      "has no reviewer configured.",
  },
  forbidden: { kind: "error", text: "The reviewer token was not accepted." },
  conflict: { kind: "error", text: "This case already has a decision." },
  "not-found": { kind: "error", text: "This case no longer exists." },
  unreachable: { kind: "error", text: "The API is unreachable. The decision was not saved." },
  error: { kind: "error", text: "Something went wrong. The decision was not saved." },
};

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function all(value: string | string[] | undefined): string[] {
  if (value === undefined) return [];
  return Array.isArray(value) ? value : [value];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function EditedValues({ run }: { run: RunDetail }) {
  const event = run.events.find((e) => e.event === "edited");
  if (!event) return null;
  const edited = event.payload.edited;
  const original = event.payload.original;
  if (!isRecord(edited) || !isRecord(original)) return null;
  return (
    <ul className="plain">
      {Object.keys(edited).map((key) => (
        <li key={key}>
          {key}: {String(original[key] ?? "")} changed to {String(edited[key] ?? "")}
        </li>
      ))}
    </ul>
  );
}

function account(a: { bank: number; acct: string }): string {
  return `${a.bank} / ${a.acct}`;
}

function TransferTable({ transfers }: { transfers: TransferRecord[] }) {
  if (transfers.length === 0) return null;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="num">Txn</th>
            <th>Time</th>
            <th>From (bank / account)</th>
            <th>To (bank / account)</th>
            <th className="num">Paid</th>
            <th className="num">Received</th>
            <th>Format</th>
          </tr>
        </thead>
        <tbody>
          {transfers.map((t) => (
            <tr key={t.txn_id}>
              <td className="num mono">{t.txn_id}</td>
              <td>{formatTime(t.ts)}</td>
              <td className="mono">{account(t.from_account)}</td>
              <td className="mono">{account(t.to_account)}</td>
              <td className="num">
                {t.amount_paid.value} {t.amount_paid.currency}
              </td>
              <td className="num">
                {t.amount_received.value} {t.amount_received.currency}
              </td>
              <td>{t.format}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ClaimBlock({
  claim,
  index,
  byId,
  missing,
}: {
  claim: Claim;
  index: number;
  byId: Map<number, TransferRecord>;
  missing: Set<number>;
}) {
  const found = claim.txn_ids.flatMap((id) => {
    const t = byId.get(id);
    return t ? [t] : [];
  });
  const absent = claim.txn_ids.filter((id) => !byId.has(id));
  return (
    <div className="claim">
      <p>
        <strong>Claim {index + 1}.</strong> {claim.statement}
      </p>
      <TransferTable transfers={found} />
      {absent.length > 0 && (
        <p className="notice warn">
          Not shown: {absent.join(", ")} (
          {absent.every((id) => missing.has(id)) ? "not found in the data" : "not fetched"}).
        </p>
      )}
      {claim.amounts.length > 0 && (
        <>
          <p className="muted">Amounts the agent quoted:</p>
          <ul className="plain">
            {claim.amounts.map((a, i) => (
              <li key={i}>
                txn <span className="mono">{a.txn_id}</span>, {a.side}: {a.value} {a.currency}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function ReviewerPanel({ run }: { run: RunDetail }) {
  const review = run.review;
  if (!review) {
    return <p className="muted">No reviewer ran for this case.</p>;
  }
  const body = review.review ?? {};
  const reasons = strings(body.reasons);
  const concerns = Array.isArray(body.concerns) ? body.concerns.filter(isRecord) : [];
  const confidence = typeof body.confidence === "number" ? body.confidence.toFixed(2) : null;
  return (
    <div className="panel">
      <p className="muted">
        A second agent checked the findings with its own checklist. A rejection or failure never
        closes the case: it comes to you.
      </p>
      <p>
        Verdict: <strong>{reviewLabel(review.verdict)}</strong>
        {review.failure_reason ? ` (${review.failure_reason.replaceAll("_", " ")})` : ""}
        {confidence !== null ? `, reviewer confidence ${confidence}` : ""}
      </p>
      {reasons.length > 0 && (
        <>
          <p className="muted">Reasons:</p>
          <ul className="plain">
            {reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </>
      )}
      {concerns.length > 0 && (
        <>
          <p className="muted">Concerns:</p>
          <ul className="plain">
            {concerns.map((c, i) => (
              <li key={i}>
                {typeof c.claim_index === "number" ? `Claim ${c.claim_index + 1}: ` : ""}
                {typeof c.issue === "string" ? c.issue : ""}
              </li>
            ))}
          </ul>
        </>
      )}
      <p className="muted">
        {review.model.provider} / {review.model.model}, prompt {review.prompt.version},{" "}
        {review.tool_steps} tool steps, {(review.wall_ms / 1000).toFixed(1)} s, ${review.cost_usd}
      </p>
    </div>
  );
}

function DecisionPanel({ run }: { run: RunDetail }) {
  if (run.decision) {
    const d = run.decision;
    return (
      <div className="panel">
        <p>
          <strong>{decisionLabel(d.decision)}</strong> by {d.actor.replace(/^human:/, "")} at{" "}
          {formatTime(d.at)}.
        </p>
        {d.note && <p>Note: {d.note}</p>}
        <EditedValues run={run} />
      </div>
    );
  }
  if (!webDecisionsEnabled()) {
    return <p className="notice">Decisions are disabled in this web deployment.</p>;
  }
  const action = submitDecision.bind(null, run.run_id);
  const canEdit = run.module === "aml" && run.findings !== null;
  const confidence = run.findings?.confidence;
  return (
    <form className="decision panel" action={action}>
      <fieldset>
        <legend className="muted">Your decision</legend>
        <label>
          <input type="radio" name="decision" value="approve" required /> Approve
        </label>
        {canEdit && (
          <label>
            <input type="radio" name="decision" value="approve_with_edits" /> Approve with edits
          </label>
        )}
        <label>
          <input type="radio" name="decision" value="reject" /> Reject
        </label>
      </fieldset>
      <label htmlFor="note">
        Note (required when you reject or approve with edits, 2000 characters at most)
      </label>
      <textarea id="note" name="note" maxLength={2000} />

      {canEdit && (
        <fieldset className="stacked">
          <legend className="muted">Edits (used only with Approve with edits)</legend>
          <p className="muted">
            Claims and figures stay as the agent wrote them. The edited answer must pass the same
            checks as the agent&apos;s, or nothing is saved.
          </p>
          <label htmlFor="edit_recommendation">Recommendation</label>
          <select
            id="edit_recommendation"
            name="edit_recommendation"
            defaultValue={findingText(run.findings, "recommendation")}
          >
            {RECOMMENDATIONS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <label htmlFor="edit_confidence">Confidence (0 to 1)</label>
          <input
            id="edit_confidence"
            name="edit_confidence"
            type="number"
            min={0}
            max={1}
            step={0.01}
            defaultValue={typeof confidence === "number" ? confidence : undefined}
          />
          <label htmlFor="edit_typology">Typology</label>
          <select
            id="edit_typology"
            name="edit_typology"
            defaultValue={findingText(run.findings, "typology")}
          >
            {TYPOLOGIES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <label htmlFor="edit_narrative">Narrative (1200 characters at most)</label>
          <textarea
            id="edit_narrative"
            name="edit_narrative"
            maxLength={1200}
            defaultValue={findingText(run.findings, "narrative")}
          />
        </fieldset>
      )}

      <fieldset className="stacked">
        <legend className="muted">Save a lesson for future cases (optional)</legend>
        <p className="muted">
          A short rule in plain words, for example &quot;Regular payroll from one employer to
          many staff accounts is not fan-out.&quot; No dates, month names or years.
        </p>
        <label htmlFor="correction_kind">Kind</label>
        <select id="correction_kind" name="correction_kind" defaultValue="">
          <option value="">no lesson</option>
          {CORRECTION_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <label htmlFor="correction_rule">Rule (10 to 500 characters)</label>
        <textarea id="correction_rule" name="correction_rule" minLength={10} maxLength={500} />
      </fieldset>
      <button type="submit">Submit decision</button>
    </form>
  );
}

export default async function CasePage({
  params,
  searchParams,
}: {
  params: Promise<{ runId: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { runId } = await params;
  const query = await searchParams;
  const outcome = one(query.outcome);
  const errors = all(query.error);
  if (!isValidRunId(runId)) notFound();

  let run: RunDetail;
  try {
    run = await getRun(runId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    const text =
      err instanceof ApiError && err.status === 0
        ? API_WAKING_MESSAGE
        : "The case could not be loaded.";
    return (
      <>
        <p>
          <Link href="/queue">Back to the queue</Link>
        </p>
        <p className="notice error">{text}</p>
      </>
    );
  }

  const findings = readFindings(run.findings);
  const byId = new Map(run.cited_transfers.map((t) => [t.txn_id, t]));
  const lessons = run.lessons ?? [];
  const missing = new Set(run.missing_txn_ids);
  const notice = outcome ? OUTCOME_TEXT[outcome] : undefined;

  return (
    <>
      <p>
        <Link href="/queue">Back to the queue</Link>
      </p>
      <h1>
        Alert <span className="mono">{run.case_id}</span>
      </h1>
      {notice && (
        <div className={`notice ${notice.kind}`}>
          <p>{notice.text}</p>
          {errors.length > 0 && (
            <ul>
              {errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <dl className="facts panel">
        <div>
          <dt>Status</dt>
          <dd>{formatStatus(run.status, run.reason)}</dd>
        </div>
        <div>
          <dt>Recommendation</dt>
          <dd>{findingText(run.findings, "recommendation") || "none"}</dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>{findingText(run.findings, "confidence") || "none"}</dd>
        </div>
        <div>
          <dt>Typology</dt>
          <dd>{findingText(run.findings, "typology") || "none"}</dd>
        </div>
        <div>
          <dt>Model</dt>
          <dd>
            {run.model.provider} / {run.model.model}
          </dd>
        </div>
        <div>
          <dt>Prompt</dt>
          <dd>{run.prompt.version}</dd>
        </div>
        <div>
          <dt>Tool steps</dt>
          <dd>{run.tool_steps}</dd>
        </div>
        <div>
          <dt>Time and cost</dt>
          <dd>
            {(run.wall_ms / 1000).toFixed(1)} s, ${run.cost_usd}
          </dd>
        </div>
        {run.trace_id ? (
          <div>
            <dt>Langfuse trace</dt>
            <dd>
              <code>{run.trace_id}</code>
            </dd>
          </div>
        ) : null}
      </dl>

      <h2>Reviewer</h2>
      <ReviewerPanel run={run} />

      <h2>Decision</h2>
      <DecisionPanel run={run} />

      {findings ? (
        <>
          <h2>Narrative</h2>
          <p>{findings.narrative}</p>

          <h2>Claims</h2>
          {findings.claims.map((claim, i) => (
            <ClaimBlock key={i} claim={claim} index={i} byId={byId} missing={missing} />
          ))}

          {findings.figures.length > 0 && (
            <>
              <h2>Figures</h2>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Kind</th>
                      <th className="num">Value</th>
                      <th>Side</th>
                      <th>Transfers</th>
                    </tr>
                  </thead>
                  <tbody>
                    {findings.figures.map((f, i) => (
                      <tr key={i}>
                        <td>{f.kind}</td>
                        <td className="num">
                          {f.value} {f.currency ?? ""}
                        </td>
                        <td>{f.side ?? ""}</td>
                        <td className="wrap mono">{f.txn_ids.join(", ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      ) : (
        <p className="notice warn">
          The agent did not produce findings for this case. Decide from the alert itself.
        </p>
      )}

      {run.not_fetched_txn_ids.length > 0 && (
        <p className="notice warn">
          {run.not_fetched_txn_ids.length} cited transfers were not fetched (lookup limit).
        </p>
      )}

      {lessons.length > 0 && (
        <>
          <h2>Lessons shown to the agent</h2>
          <p className="muted">
            Saved by reviewers on earlier, different cases. Shown as general guidance, not as
            evidence about this case.
          </p>
          <ul>
            {lessons.map((lesson) => (
              <li key={lesson.id}>
                {lesson.text} <span className="muted mono">(lesson {lesson.id})</span>
              </li>
            ))}
          </ul>
        </>
      )}

      <h2>Verifier results</h2>
      {run.verifier_results.length === 0 ? (
        <p className="muted">No verifier ran.</p>
      ) : (
        <ul className="plain">
          {run.verifier_results.map((v, i) => (
            <li key={i}>
              <span className={v.passed ? "pass" : "fail"}>{v.passed ? "pass" : "fail"}</span>{" "}
              {v.name}
              {v.errors.length > 0 && (
                <ul className="plain">
                  {v.errors.map((e, j) => (
                    <li key={j}>{e}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}

      <h2>Model calls</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Model</th>
              <th className="num">Input tokens</th>
              <th className="num">Output tokens</th>
              <th className="num">Cache read</th>
              <th className="num">Latency</th>
              <th>Stop reason</th>
            </tr>
          </thead>
          <tbody>
            {run.model_calls.map((c, i) => (
              <tr key={i}>
                <td>
                  {c.provider} / {c.model}
                </td>
                <td className="num">{c.input_tokens}</td>
                <td className="num">{c.output_tokens}</td>
                <td className="num">{c.cache_read_tokens}</td>
                <td className="num">{c.latency_ms} ms</td>
                <td>{c.stop_reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Tool calls</h2>
      {run.tool_calls === null ? (
        <p className="muted">Tool call detail is not available for this run.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="num">#</th>
                <th>Tool</th>
                <th>Arguments</th>
                <th>Result</th>
                <th className="num">Duration</th>
              </tr>
            </thead>
            <tbody>
              {run.tool_calls.map((t) => (
                <tr key={t.index}>
                  <td className="num">{t.index}</td>
                  <td>{t.name}</td>
                  <td className="wrap mono">{JSON.stringify(t.arguments)}</td>
                  <td>
                    {t.ok ? "ok" : `failed (${t.error_code ?? "error"})`}
                    {t.truncated ? ", truncated" : ""}
                  </td>
                  <td className="num">{t.duration_ms} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h2>Audit trail</h2>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Event</th>
              <th>Actor</th>
              <th>Time</th>
            </tr>
          </thead>
          <tbody>
            {run.events.map((e, i) => (
              <tr key={i}>
                <td>{e.event.replaceAll("_", " ")}</td>
                <td>{e.actor}</td>
                <td>{formatTime(e.at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
