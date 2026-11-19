// The pure sections of a case file, shared by the queue case page and the replay. Server
// safe: no hooks and no client code.

import Link from "next/link";

import type { RunDetail, TransferRecord } from "../../lib/api";
import {
  actorLabel,
  checkLabel,
  findingText,
  formatSeconds,
  formatTime,
  formatUsd,
  reasonLabel,
  saysKind,
  saysLabel,
  type CitedAmount,
  type Claim,
} from "../../lib/format";
import { CheckResult, Meter, ReviewerWord } from "./marks";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

function account(a: { bank: number; acct: string }): string {
  return `${a.bank}/${a.acct}`;
}

// ------------------------------------------------------------------ verdict

export function Verdict({ run }: { run: RunDetail }) {
  const recommendation = findingText(run.findings, "recommendation") || null;
  const confidence = run.findings?.confidence;
  const kind = saysKind(recommendation, run.status);
  const typology = findingText(run.findings, "typology");
  return (
    <section className="verdict" aria-label="Agent says">
      <p className="meta">Agent recommendation</p>
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
            Confidence is the agent&apos;s estimate that the alert is a true positive (actual money laundering). At 0.5
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
          Routed to analyst review: {reasonLabel(run.reason)}. The agent never closes a case like this
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

export function Strip({ transfer, quoted }: { transfer: TransferRecord; quoted: CitedAmount[] }) {
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

export function ClaimItem({
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

export function ChecksSection({ run }: { run: RunDetail }) {
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

export function ReviewerSection({ run }: { run: RunDetail }) {
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
        {confidence !== null && <span className="meta">, confidence {confidence}</span>}
      </p>
      <p className="meta">
        A second agent checks the answer against its own checklist. A rejection never closes the
        case: it is routed to an analyst.
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

export function RunSection({ run }: { run: RunDetail }) {
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

export function LessonsSection({ run }: { run: RunDetail }) {
  const lessons = run.lessons ?? [];
  if (lessons.length === 0) return null;
  return (
    <section aria-labelledby="lessons-shown">
      <h2 id="lessons-shown">Lessons shown</h2>
      <p className="meta">
        Saved by analysts on earlier, different alerts. Shown to the agent as general guidance, not
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

export function AuditSection({ run }: { run: RunDetail }) {
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
