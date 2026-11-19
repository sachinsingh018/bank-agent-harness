import Link from "next/link";
import { redirect } from "next/navigation";

import {
  getEval,
  getEvals,
  type EvalRunDetail,
  type EvalRunSummary,
  type SplitName,
  type SplitScore,
} from "../../lib/api";
import { loadErrorText } from "../../lib/errors";
import {
  formatCount,
  formatPointChange,
  formatRate,
  formatTime,
  formatUsd,
  shortHash,
} from "../../lib/format";
import { isValidEvalRunId } from "../../lib/flags";
import { isBundleMode } from "../../lib/source";

export const metadata = { title: "Evals | AML Alert Desk" };

const SHOWN_RUNS = 20;
const SPLITS: { name: SplitName; label: string }[] = [
  { name: "early", label: "Early window" },
  { name: "late", label: "Late window" },
  { name: "all", label: "All alerts" },
];

type Metric = { label: string; value: (s: SplitScore) => string };

const METRICS: Metric[] = [
  { label: "Alerts scored", value: (s) => formatCount(s.alerts) },
  {
    label: "Precision, agent",
    value: (s) => formatRate(s.agent_with_human_review?.strict?.precision),
  },
  { label: "Precision, rules alone", value: (s) => formatRate(s.baseline?.precision_strict) },
  { label: "Recall, agent", value: (s) => formatRate(s.agent_with_human_review?.strict?.recall) },
  {
    label: "False positives, agent vs rules",
    value: (s) => {
      const d = s.fp_reduction?.at_agent_decisions;
      return d ? `${formatCount(d.fp)} vs ${formatCount(d.baseline_fp)}` : "n/a";
    },
  },
  {
    label: "False positives removed",
    value: (s) => formatRate(s.fp_reduction?.at_agent_decisions?.fp_reduction),
  },
  {
    label: "Routed to analyst review",
    value: (s) =>
      s.human_review
        ? `${formatRate(s.human_review.rate)} (${formatCount(s.human_review.count)})`
        : "n/a",
  },
  {
    label: "Reviewer rejects",
    value: (s) =>
      s.review
        ? `${formatRate(s.review.reject_rate)} (${formatCount(s.review.rejected)} of ${formatCount(s.review.reviewed)})`
        : "n/a",
  },
  { label: "Precision change from review", value: (s) => formatPointChange(s.review?.precision_change) },
  { label: "Claims grounded in transfers", value: (s) => formatRate(s.grounding?.rate) },
  {
    label: "Calibration error (ECE)",
    value: (s) => (typeof s.calibration?.ece === "number" ? s.calibration.ece.toFixed(2) : "n/a"),
  },
  { label: "Pattern named correctly", value: (s) => formatRate(s.typology?.accuracy) },
  { label: "Cost per alert", value: (s) => formatUsd(s.effort?.cost_per_alert_usd) },
  {
    label: "Mean time per alert",
    value: (s) =>
      typeof s.effort?.latency_ms_mean === "number"
        ? `${(s.effort.latency_ms_mean / 1000).toFixed(1)} s`
        : "n/a",
  },
];

function counts(values: Record<string, number> | undefined): string {
  if (!values) return "n/a";
  const parts = Object.entries(values).map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`);
  return parts.length > 0 ? parts.join(", ") : "none";
}

function precisionCell(run: EvalRunSummary): string {
  const h = run.headline;
  if (!h) return "not scored";
  return `${formatRate(h.precision)} vs ${formatRate(h.baseline_precision)}`;
}

function ScoreTable({ detail }: { detail: EvalRunDetail }) {
  const splits = detail.score?.splits;
  if (!splits) return <p className="muted">This run has not been scored yet.</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">Measure</th>
            {SPLITS.map((s) => (
              <th key={s.name} scope="col" className="num">
                {s.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => (
            <tr key={m.label}>
              <th scope="row">{m.label}</th>
              {SPLITS.map((s) => {
                const split = splits[s.name];
                return (
                  <td key={s.name} className="num">
                    {split ? m.value(split) : "n/a"}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunDetailBlock({ detail }: { detail: EvalRunDetail | null }) {
  if (!detail) {
    return <p className="muted">The details of this run could not be loaded. Reload to try again.</p>;
  }
  const alerts = detail.score?.splits?.early?.alerts ?? detail.cases;
  return (
    <>
      <p className="notice warn">
        Small sets are indicative. This run covers {formatCount(detail.cases)} alerts
        {alerts !== null && alerts !== detail.cases ? `, ${alerts} in the early window` : ""}.
        Read the numbers as a direction, not a measurement.
      </p>
      <ScoreTable detail={detail} />
      <h3>Run setup</h3>
      <dl className="kv">
        <dt>Run</dt>
        <dd>{detail.eval_run_id}</dd>
        <dt>Started</dt>
        <dd>{formatTime(detail.started_at)}</dd>
        <dt>Finished</dt>
        <dd>{detail.finished_at ? formatTime(detail.finished_at) : "did not finish"}</dd>
        <dt>Dataset</dt>
        <dd>
          {detail.dataset} {detail.dataset_version}{" "}
          <span className="meta">sha256 {shortHash(detail.dataset_sha256)}</span>
        </dd>
        <dt>Model</dt>
        <dd>
          {detail.model} <span className="meta">via {detail.provider}</span>
        </dd>
        <dt>Prompt</dt>
        <dd>
          {detail.prompt_version}{" "}
          <span className="meta">sha256 {shortHash(detail.prompt_sha256)}</span>
        </dd>
        <dt>Code</dt>
        <dd>
          {shortHash(detail.git_commit)}
          {detail.git_dirty ? " with uncommitted changes" : ""}
        </dd>
        <dt>Limits</dt>
        <dd>{counts(detail.limits)}</dd>
        <dt>Cases by status</dt>
        <dd>{counts(detail.counts?.by_status)}</dd>
        <dt>Why routed to an analyst</dt>
        <dd>{counts(detail.counts?.by_human_review_reason)}</dd>
        <dt>Lessons</dt>
        <dd>
          {detail.score?.lessons?.enabled
            ? `on, used in ${formatCount(detail.score.lessons.cases_with_lessons)} cases`
            : "off"}
        </dd>
        <dt>Scored</dt>
        <dd>{detail.scored_at ? formatTime(detail.scored_at) : "not yet"}</dd>
      </dl>
    </>
  );
}

export default async function EvalsPage() {
  // The public site has no API: results come from the committed bundle instead.
  if (isBundleMode()) redirect("/results");
  let runs: EvalRunSummary[] | null = null;
  let error: string | null = null;
  try {
    runs = (await getEvals(SHOWN_RUNS)).items;
  } catch (err) {
    error = loadErrorText(err, "eval runs");
  }

  // Details for the expandable rows, fetched on the server so no client code is needed.
  const details = new Map<string, EvalRunDetail | null>();
  if (runs) {
    const settled = await Promise.allSettled(
      runs.filter((r) => isValidEvalRunId(r.eval_run_id)).map((r) => getEval(r.eval_run_id)),
    );
    for (const result of settled) {
      if (result.status === "fulfilled") details.set(result.value.eval_run_id, result.value);
    }
  }

  return (
    <>
      <h1>Evals</h1>
      <div className="lede">
        <p>
          Every change to prompts, tools or models is run against a fixed, versioned set of alerts
          and scored against the known answers. The rules alone escalate every alert they raise,
          so they are the baseline to beat.
        </p>
        <p>
          Precision: of the alerts the agent escalated, the share that were real laundering.
          Recall: of the real ones, the share it escalated. The headline is the early window
          (alerts up to 2022-09-10); later alerts sit in a stretch where normal traffic almost
          stops, so they are reported apart.
        </p>
      </div>

      {error && <p className="notice error">{error}</p>}

      {runs && runs.length === 0 && (
        <p className="notice">No eval runs are recorded yet.</p>
      )}

      {runs && runs.length > 0 && (
        <div className="eval-list">
          <div className="inner">
            <div className="eval-row-grid eval-head">
              <span />
              <span>When</span>
              <span>Dataset</span>
              <span>Model</span>
              <span>Prompt</span>
              <span className="num">Cases</span>
              <span className="num">Precision vs rules</span>
              <span className="num">Recall</span>
              <span className="num">To an analyst</span>
              <span className="num">Reviewer rejects</span>
              <span className="num">Cost</span>
            </div>
            {runs.map((run) => {
              const h = run.headline;
              return (
                <details key={run.eval_run_id} className="expander" id={run.eval_run_id}>
                  <summary className="eval-row-grid">
                    <span>{formatTime(run.started_at)}</span>
                    <span>
                      {run.dataset} {run.dataset_version}
                    </span>
                    <span>{run.model}</span>
                    <span>{run.prompt_version}</span>
                    <span className="num">{formatCount(run.cases)}</span>
                    <span className="num">{precisionCell(run)}</span>
                    <span className="num">{h ? formatRate(h.recall) : ""}</span>
                    <span className="num">{h ? formatRate(h.human_review_rate) : ""}</span>
                    <span className="num">{h ? formatRate(h.reviewer_reject_rate) : ""}</span>
                    <span className="num">{formatUsd(run.total_cost_usd)}</span>
                  </summary>
                  <div className="eval-detail">
                    <RunDetailBlock detail={details.get(run.eval_run_id) ?? null} />
                  </div>
                </details>
              );
            })}
          </div>
        </div>
      )}

      <p className="meta">
        Numbers count cases and rates only; no answer for a single alert is shown here. See{" "}
        <Link href="/about">Methodology</Link> for what is measured and how much so far.
      </p>
    </>
  );
}
