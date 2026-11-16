import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiError, getRun, type RunDetail, type TransferRecord } from "../../../lib/api";
import {
  findingText,
  formatStatus,
  formatTime,
  readFindings,
  type Claim,
} from "../../../lib/format";
import { isValidRunId, webDecisionsEnabled } from "../../../lib/flags";
import { submitDecision } from "./actions";

const OUTCOME_TEXT: Record<string, { kind: "success" | "error"; text: string }> = {
  approved: { kind: "success", text: "Approved. The decision is in the audit log." },
  rejected: { kind: "success", text: "Rejected. The decision is in the audit log." },
  "note-required": { kind: "error", text: "A rejection needs a note explaining why." },
  "note-too-long": { kind: "error", text: "The note is too long (2000 characters at most)." },
  invalid: { kind: "error", text: "Choose approve or reject." },
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

function DecisionPanel({ run }: { run: RunDetail }) {
  if (run.decision) {
    const d = run.decision;
    return (
      <div className="panel">
        <p>
          <strong>{d.decision === "approve" ? "Approved" : "Rejected"}</strong> by{" "}
          {d.actor.replace(/^human:/, "")} at {formatTime(d.at)}.
        </p>
        {d.note && <p>Note: {d.note}</p>}
      </div>
    );
  }
  if (!webDecisionsEnabled()) {
    return <p className="notice">Decisions are disabled in this web deployment.</p>;
  }
  const action = submitDecision.bind(null, run.run_id);
  return (
    <form className="decision panel" action={action}>
      <fieldset>
        <legend className="muted">Your decision</legend>
        <label>
          <input type="radio" name="decision" value="approve" required /> Approve
        </label>
        <label>
          <input type="radio" name="decision" value="reject" /> Reject
        </label>
      </fieldset>
      <label htmlFor="note">Note (required when you reject, 2000 characters at most)</label>
      <textarea id="note" name="note" maxLength={2000} />
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
  const outcome = one((await searchParams).outcome);
  if (!isValidRunId(runId)) notFound();

  let run: RunDetail;
  try {
    run = await getRun(runId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    const text =
      err instanceof ApiError && err.status === 0
        ? "The API is unreachable. Check that it is running and that API_BASE_URL is set."
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
      {notice && <p className={`notice ${notice.kind}`}>{notice.text}</p>}

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
      </dl>

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
