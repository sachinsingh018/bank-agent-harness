import Link from "next/link";
import { notFound } from "next/navigation";

import { API_WAKING_MESSAGE, ApiError, getRun, type RunDetail, type TransferRecord } from "../../../lib/api";
import {
  CORRECTION_KINDS,
  RECOMMENDATIONS,
  TYPOLOGIES,
  actorLabel,
  checkLabel,
  decisionLabel,
  findingText,
  formatSeconds,
  formatTime,
  formatUsd,
  readFindings,
  reasonLabel,
  saysKind,
  saysLabel,
  type CitedAmount,
  type Claim,
} from "../../../lib/format";
import { isValidRunId, webDecisionsEnabled } from "../../../lib/flags";
import { CheckResult, Meter, ReviewerWord } from "../../_ui/marks";
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

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function account(a: { bank: number; acct: string }): string {
  return `${a.bank}/${a.acct}`;
}

// ------------------------------------------------------------------ verdict

function Verdict({ run }: { run: RunDetail }) {
  const recommendation = findingText(run.findings, "recommendation") || null;
  const confidence = run.findings?.confidence;
  const kind = saysKind(recommendation, run.status);
  const typology = findingText(run.findings, "typology");
  return (
    <section className="verdict" aria-label="Agent says">
      <p className="meta">Agent says</p>
      <div className="line">
        <p className={`word condensed word-${kind}`}>
          <span className={`mark ${kind}`} aria-hidden="true" />
          {saysLabel(kind)}
        </p>
        {typeof confidence === "number" && (
          <span className="number condensed" aria-label={`confidence ${confidence.toFixed(2)}`}>
            {confidence.toFixed(2)}
          </span>
        )}
      </div>
      {typeof confidence === "number" && (
        <>
          <Meter value={confidence} kind={kind} />
          <div className="meter-scale" aria-hidden="true">
            <span className="zero">0</span>
            <span className="half">0.5 decision line</span>
            <span className="one">1</span>
          </div>
          <p className="about meta">
            Confidence is the agent&apos;s estimate that the alert is real money laundering. At 0.5
            or above it must escalate; below 0.5 it must close.
          </p>
        </>
      )}
      {typology && typology !== "NONE" && (
        <p>
          <span className="typology">{typology}</span>{" "}
          <span className="meta">pattern named by the agent</span>
        </p>
      )}
      {run.status === "human_review" && (
        <p className="notice error">
          <span className="mark review" aria-hidden="true" />
          Sent to a person: {reasonLabel(run.reason)}. The agent never closes a case like this
          on its own.
        </p>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ claims

function quotedFor(amounts: CitedAmount[], txnId: number): CitedAmount[] {
  return amounts.filter((a) => a.txn_id === txnId);
}

function Strip({ transfer, quoted }: { transfer: TransferRecord; quoted: CitedAmount[] }) {
  const t = transfer;
  return (
    <details className="strip">
      <summary>
        <span className="route">
          {account(t.from_account)}
          <span className="to">to</span>
          {account(t.to_account)}
        </span>
        <span className="amount">
          {t.amount_paid.value} {t.amount_paid.currency} paid
        </span>
        <span className="format">{t.format}</span>
      </summary>
      <div className="body">
        <dl className="kv">
          <dt>Transfer</dt>
          <dd>{t.txn_id}</dd>
          <dt>Time</dt>
          <dd>{formatTime(t.ts)}</dd>
          <dt>From</dt>
          <dd>
            bank {t.from_account.bank}, account {t.from_account.acct}
          </dd>
          <dt>To</dt>
          <dd>
            bank {t.to_account.bank}, account {t.to_account.acct}
          </dd>
          <dt>Paid</dt>
          <dd>
            {t.amount_paid.value} {t.amount_paid.currency}
          </dd>
          <dt>Received</dt>
          <dd>
            {t.amount_received.value} {t.amount_received.currency}
          </dd>
          <dt>Format</dt>
          <dd>{t.format}</dd>
        </dl>
        {quoted.length > 0 && (
          <p className="quoted">
            The agent quoted{" "}
            {quoted.map((a) => `${a.value} ${a.currency} ${a.side}`).join(" and ")}.
          </p>
        )}
      </div>
    </details>
  );
}

function ClaimItem({
  claim,
  byId,
  missing,
}: {
  claim: Claim;
  byId: Map<number, TransferRecord>;
  missing: Set<number>;
}) {
  const absent = claim.txn_ids.filter((id) => !byId.has(id));
  return (
    <li>
      <div>
        <p className="statement">{claim.statement}</p>
        {claim.txn_ids.flatMap((id) => {
          const t = byId.get(id);
          return t ? [<Strip key={id} transfer={t} quoted={quotedFor(claim.amounts, id)} />] : [];
        })}
        {absent.length > 0 && (
          <p className="notice warn">
            Transfers {absent.join(", ")} are not shown:{" "}
            {absent.every((id) => missing.has(id))
              ? "they were not found in the data."
              : "they were not fetched (lookup limit)."}
          </p>
        )}
      </div>
    </li>
  );
}

// ------------------------------------------------------------------ rail

function ChecksSection({ run }: { run: RunDetail }) {
  return (
    <section aria-labelledby="checks">
      <h2 id="checks">Checks</h2>
      <p className="meta">Fixed rules run on every answer before anyone sees it.</p>
      {run.verifier_results.length === 0 ? (
        <p className="muted">No checks ran for this case.</p>
      ) : (
        <ul className="checks">
          {run.verifier_results.map((v, i) => (
            <li key={i}>
              <span>{checkLabel(v.name)}</span>
              <CheckResult passed={v.passed} />
              {v.errors.length > 0 && (
                <span className="errors">
                  <ul>
                    {v.errors.map((e, j) => (
                      <li key={j}>{e}</li>
                    ))}
                  </ul>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ReviewerSection({ run }: { run: RunDetail }) {
  const review = run.review;
  if (!review) {
    return (
      <section aria-labelledby="reviewer">
        <h2 id="reviewer">Reviewer</h2>
        <p className="muted">No reviewer agent ran for this case.</p>
      </section>
    );
  }
  const body = review.review ?? {};
  const reasons = strings(body.reasons);
  const concerns = Array.isArray(body.concerns) ? body.concerns.filter(isRecord) : [];
  const confidence = typeof body.confidence === "number" ? body.confidence.toFixed(2) : null;
  const count = reasons.length + concerns.length;
  return (
    <section aria-labelledby="reviewer">
      <h2 id="reviewer">Reviewer</h2>
      <p>
        <ReviewerWord verdict={review.verdict} />
        {review.failure_reason ? ` (${reasonLabel(review.failure_reason)})` : ""}
        {confidence !== null && <span className="meta">, sure at {confidence}</span>}
      </p>
      <p className="meta">
        A second agent checks the answer against its own checklist. A rejection never closes the
        case: it comes to a person.
      </p>
      {count > 0 && (
        <details className="expander simple">
          <summary>
            {reasons.length} {reasons.length === 1 ? "reason" : "reasons"}
            {concerns.length > 0 &&
              `, ${concerns.length} ${concerns.length === 1 ? "concern" : "concerns"}`}
          </summary>
          {reasons.length > 0 && (
            <ul className="plain">
              {reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          )}
          {concerns.length > 0 && (
            <>
              <h3>Concerns</h3>
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
        </details>
      )}
      <p className="meta">
        {review.model.model}, prompt {review.prompt.version}, {review.tool_steps} tool steps,{" "}
        {formatSeconds(review.wall_ms)}, {formatUsd(review.cost_usd)}
      </p>
    </section>
  );
}

function RunSection({ run }: { run: RunDetail }) {
  return (
    <section aria-labelledby="run">
      <h2 id="run">Run</h2>
      <dl className="kv">
        <dt>Model</dt>
        <dd>
          {run.model.model} <span className="meta">via {run.model.provider}</span>
        </dd>
        <dt>Prompt</dt>
        <dd>{run.prompt.version}</dd>
        <dt>Tool steps</dt>
        <dd>{run.tool_steps}</dd>
        <dt>Tokens</dt>
        <dd>
          {run.totals.input_tokens.toLocaleString("en-US")} in,{" "}
          {run.totals.output_tokens.toLocaleString("en-US")} out
        </dd>
        <dt>Cost</dt>
        <dd>{formatUsd(run.cost_usd)}</dd>
        <dt>Time</dt>
        <dd>{formatSeconds(run.wall_ms)}</dd>
        <dt>Recorded</dt>
        <dd>{formatTime(run.created_at)}</dd>
        {run.eval_run_id && (
          <>
            <dt>Eval run</dt>
            <dd>
              <Link href="/evals">{run.eval_run_id}</Link>
            </dd>
          </>
        )}
        {run.trace_id && (
          <>
            <dt>Trace</dt>
            <dd>{run.trace_id}</dd>
          </>
        )}
      </dl>
    </section>
  );
}

function LessonsSection({ run }: { run: RunDetail }) {
  const lessons = run.lessons ?? [];
  if (lessons.length === 0) return null;
  return (
    <section aria-labelledby="lessons-shown">
      <h2 id="lessons-shown">Lessons shown</h2>
      <p className="meta">
        Saved by people on earlier, different alerts. Shown to the agent as general guidance, not
        as evidence about this case.
      </p>
      <ul className="plain">
        {lessons.map((lesson) => (
          <li key={lesson.id}>
            {lesson.text} <span className="meta">(lesson {lesson.id})</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function AuditSection({ run }: { run: RunDetail }) {
  return (
    <section aria-labelledby="audit">
      <h2 id="audit">Audit trail</h2>
      <p className="meta">Append only: entries are never changed or removed.</p>
      {run.events.length === 0 ? (
        <p className="muted">No events recorded.</p>
      ) : (
        <ol className="audit">
          {run.events.map((e, i) => (
            <li key={i}>
              <span className="what">{e.event.replaceAll("_", " ")}</span>
              <span className="meta">
                {actorLabel(e.actor)}, {formatTime(e.at)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
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

function DecisionForm({ run }: { run: RunDetail }) {
  const action = submitDecision.bind(null, run.run_id);
  const canEdit = run.module === "aml" && run.findings !== null;
  const confidence = run.findings?.confidence;
  return (
    <form className="decision" action={action}>
      <fieldset>
        <legend>Your decision</legend>
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
        Note (needed when you reject or approve with edits, 2000 characters at most)
      </label>
      <textarea id="note" name="note" maxLength={2000} />

      {canEdit && (
        <details>
          <summary>Edits (used only with Approve with edits)</summary>
          <p className="meta">
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
        </details>
      )}

      <details>
        <summary>Save a lesson for future cases (optional)</summary>
        <p className="meta">
          A short rule in plain words, for example &quot;Regular payroll from one employer to many
          staff accounts is not fan-out.&quot; No dates, month names or years.
        </p>
        <label htmlFor="correction_kind">About</label>
        <select id="correction_kind" name="correction_kind" defaultValue="">
          <option value="">no lesson</option>
          {CORRECTION_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <label htmlFor="correction_rule">Lesson (10 to 500 characters)</label>
        <textarea id="correction_rule" name="correction_rule" minLength={10} maxLength={500} />
      </details>
      <button type="submit">Submit decision</button>
    </form>
  );
}

function DecisionSection({ run }: { run: RunDetail }) {
  let content;
  if (run.decision) {
    const d = run.decision;
    content = (
      <>
        <p>
          <strong>{decisionLabel(d.decision)}</strong> by {actorLabel(d.actor)}
        </p>
        <p className="meta">{formatTime(d.at)}</p>
        {d.note && <p>Note: {d.note}</p>}
        <EditedValues run={run} />
      </>
    );
  } else if (!webDecisionsEnabled()) {
    content = (
      <p className="muted">
        Decisions are disabled in this web deployment. In the full setup a person approves,
        edits or rejects the agent&apos;s answer here.
      </p>
    );
  } else {
    content = <DecisionForm run={run} />;
  }
  return (
    <section aria-labelledby="decision">
      <h2 id="decision">Decision</h2>
      {content}
    </section>
  );
}

// ------------------------------------------------------------------ page

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
        : "The case could not be loaded. Go back to the queue and try again.";
    return (
      <>
        <p className="back">
          <Link href="/queue">Back to the queue</Link>
        </p>
        <p className="notice error">{text}</p>
      </>
    );
  }

  const findings = readFindings(run.findings);
  const byId = new Map(run.cited_transfers.map((t) => [t.txn_id, t]));
  const missing = new Set(run.missing_txn_ids);
  const notice = outcome ? OUTCOME_TEXT[outcome] : undefined;

  return (
    <>
      <p className="back">
        <Link href="/queue">Back to the queue</Link>
      </p>
      {notice && (
        <div className={`notice ${notice.kind}`} role="status">
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

      <div className="case-grid">
        <div className="case-main">
          <h1>
            <span className="meta">Alert </span>
            {run.case_id}
          </h1>
          <Verdict run={run} />

          {findings ? (
            <>
              <h2>Narrative</h2>
              <p className="prose">{findings.narrative}</p>

              <h2>Claims</h2>
              <p className="meta">
                Each claim cites real transfers. Open a transfer to see its details.
              </p>
              {findings.claims.length === 0 ? (
                <p className="muted">The agent made no claims.</p>
              ) : (
                <ol className="claims">
                  {findings.claims.map((claim, i) => (
                    <ClaimItem key={i} claim={claim} byId={byId} missing={missing} />
                  ))}
                </ol>
              )}

              {findings.figures.length > 0 && (
                <>
                  <h2>Figures</h2>
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th scope="col">What</th>
                          <th scope="col" className="num">
                            Value
                          </th>
                          <th scope="col">Side</th>
                          <th scope="col">Transfers</th>
                        </tr>
                      </thead>
                      <tbody>
                        {findings.figures.map((f, i) => (
                          <tr key={i}>
                            <td>{f.kind.replaceAll("_", " ")}</td>
                            <td className="num">
                              {f.value} {f.currency ?? ""}
                            </td>
                            <td>{f.side ?? ""}</td>
                            <td className="wrap">{f.txn_ids.join(", ")}</td>
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
              The agent did not produce an answer for this case. Decide from the alert itself.
            </p>
          )}

          {run.not_fetched_txn_ids.length > 0 && (
            <p className="notice warn">
              {run.not_fetched_txn_ids.length} cited transfers were not fetched (lookup limit).
            </p>
          )}

          <h2>Run detail</h2>
          <details className="expander simple">
            <summary>Model calls ({run.model_calls.length})</summary>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Model</th>
                    <th scope="col" className="num">
                      Input tokens
                    </th>
                    <th scope="col" className="num">
                      Output tokens
                    </th>
                    <th scope="col" className="num">
                      Cache read
                    </th>
                    <th scope="col" className="num">
                      Latency
                    </th>
                    <th scope="col">Stop reason</th>
                  </tr>
                </thead>
                <tbody>
                  {run.model_calls.map((c, i) => (
                    <tr key={i}>
                      <td>{c.model}</td>
                      <td className="num">{c.input_tokens}</td>
                      <td className="num">{c.output_tokens}</td>
                      <td className="num">{c.cache_read_tokens}</td>
                      <td className="num">{c.latency_ms} ms</td>
                      <td>{c.stop_reason.replaceAll("_", " ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
          <details className="expander simple">
            <summary>
              Tool calls{run.tool_calls === null ? "" : ` (${run.tool_calls.length})`}
            </summary>
            {run.tool_calls === null ? (
              <p className="muted">Tool call detail is not available for this run.</p>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th scope="col" className="num">
                        Step
                      </th>
                      <th scope="col">Tool</th>
                      <th scope="col">Arguments</th>
                      <th scope="col">Result</th>
                      <th scope="col" className="num">
                        Time
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {run.tool_calls.map((t) => (
                      <tr key={t.index}>
                        <td className="num">{t.index}</td>
                        <td>{t.name.replaceAll("_", " ")}</td>
                        <td className="wrap">{JSON.stringify(t.arguments)}</td>
                        <td>
                          {t.ok ? "ok" : `failed (${t.error_code ?? "error"})`}
                          {t.truncated ? ", cut short" : ""}
                        </td>
                        <td className="num">{t.duration_ms} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </details>
        </div>

        <aside className="rail" aria-label="Checks, review and decision">
          <ChecksSection run={run} />
          <ReviewerSection run={run} />
          <RunSection run={run} />
          <LessonsSection run={run} />
          <AuditSection run={run} />
          <DecisionSection run={run} />
        </aside>
      </div>
    </>
  );
}
