import type { Metadata } from "next";
import Link from "next/link";

import { getResults } from "../../lib/data";
import { pageMeta } from "../../lib/meta";
import type { HumanReviewView } from "../../lib/results-view";
import {
  groundingView,
  headlineHeldout,
  humanReviewView,
  REVIEW_REASONS,
  rulesContext,
  splitView,
  type Outcome,
  type SplitView,
} from "../../lib/results-view";
import { Attribution } from "../_ui/attribution";
import { CountBar } from "../_ui/count-bar";
import { Guilloche } from "../_ui/guilloche";
import styles from "./results.module.css";

export const metadata: Metadata = pageMeta(
  "Performance | AML Alert Desk",
  "How the agent performed on held-out alerts it had never seen, and on the development alerts the prompts were tuned on, in plain counts: the false positives it would close next to the true positives it would also close.",
);

function escalated(o: Outcome): number {
  return o.realTotal - o.realClosed + (o.falseTotal - o.falseClosed);
}

/** The agent and the rules on the same alerts, side by side, in counts. */
function SplitTable({ view, caption }: { view: SplitView; caption: string }) {
  const rows: { name: string; o: Outcome }[] = [];
  if (view.rules) rows.push({ name: "Rules alone", o: view.rules });
  rows.push({ name: "Agent, with analyst review on unclear cases", o: view.agent });
  return (
    <div className={`table-wrap ${styles.table}`}>
      <table>
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Approach</th>
            <th scope="col" className="num">
              Escalated or routed to analyst review
            </th>
            <th scope="col" className="num">
              False positives closed
            </th>
            <th scope="col" className="num">
              True positives closed
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ name, o }) => (
            <tr key={name}>
              <th scope="row">{name}</th>
              <td className="num">
                {escalated(o)} of {view.n}
              </td>
              <td className="num">
                {o.falseClosed} of {o.falseTotal}
              </td>
              <td className="num">
                {o.realClosed} of {o.realTotal}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CountPair({ o }: { o: Outcome }) {
  return (
    <div className={styles.pair}>
      <div className={styles.count}>
        <p className={styles.figure}>
          Closes <span className={`${styles.big} condensed`}>{o.falseClosed}</span> of{" "}
          {o.falseTotal} false positives
        </p>
        <CountBar part={o.falseClosed} total={o.falseTotal} tone="good" />
      </div>
      <div className={styles.count}>
        <p className={styles.figure}>
          and also <span className={`${styles.big} ${styles.miss} condensed`}>{o.realClosed}</span>{" "}
          of {o.realTotal} true positives
        </p>
        <CountBar part={o.realClosed} total={o.realTotal} tone="miss" />
      </div>
    </div>
  );
}

function ReviewReasons({ review }: { review: HumanReviewView }) {
  if (review.reasons.length === 0) return null;
  return (
    <ul className={styles.reasons}>
      {review.reasons.map((r) => (
        <li key={r.key}>
          <span className={`${styles.reasonCount} condensed`}>{r.count}</span>{" "}
          {REVIEW_REASONS[r.key] ?? r.key.replaceAll("_", " ")}
        </li>
      ))}
    </ul>
  );
}

export default async function ResultsPage() {
  const results = await getResults();
  const early = splitView(results?.splits?.early);
  const all = splitView(results?.splits?.all);
  const review = results ? humanReviewView(results) : null;
  const rules171 = results ? rulesContext(results) : null;
  const held = headlineHeldout(results);
  const heldEarly = splitView(held?.splits?.early);
  const heldAll = splitView(held?.splits?.all);
  const heldReview = held ? humanReviewView(held) : null;
  const grounding = held ? groundingView(held) : null;
  const cutoff = results?.late_cutoff ?? null;
  const late =
    early && all
      ? {
          n: all.n - early.n,
          real: all.agent.realTotal - early.agent.realTotal,
        }
      : null;

  return (
    <>
      <h1>Performance</h1>
      {!results || !early ? (
        <p className="notice">No results have been exported yet.</p>
      ) : (
        <>
          <p className="lede">
            The agent was scored on two sets of recorded alerts: held-out alerts it had never
            seen, and development alerts the prompts were tuned on. A false positive the agent
            closes saves analyst time; a true positive it closes is laundering that would go
            unreported. Both are shown together, because one without the other would mislead.
          </p>

          {held && heldEarly && (
            <section aria-labelledby="heldout-title">
              <h2 id="heldout-title">
                The headline: {heldEarly.n} early held-out alerts the agent had never seen
              </h2>
              <p className="prose">
                These alerts were set aside and never used to tune the prompts.{" "}
                {heldEarly.agent.falseTotal} of the {heldEarly.n} early ones were false positives
                and {heldEarly.agent.realTotal} were true positives. The agent would recommend
                closing {heldEarly.agent.falseClosed} of the false positives. It would also
                recommend closing {heldEarly.agent.realClosed} of the{" "}
                {heldEarly.agent.realTotal} true positives. That is why an analyst approves every
                close.
              </p>
              <CountPair o={heldEarly.agent} />
              <h3>Against the rules on the same {heldEarly.n} held-out alerts</h3>
              <p className="prose meta">
                The rules escalate every alert they raise, so they close nothing and miss nothing
                they raised.
              </p>
              <SplitTable
                view={heldEarly}
                caption={`Early held-out alerts: rules and agent on ${heldEarly.n} alerts`}
              />
              {heldAll && (
                <>
                  <h3>All {heldAll.n} held-out alerts</h3>
                  <p className="prose">
                    Including the {heldAll.n - heldEarly.n} late held-out alerts, the agent would
                    close {heldAll.agent.falseClosed} of {heldAll.agent.falseTotal} false positives
                    and {heldAll.agent.realClosed} of {heldAll.agent.realTotal} true positives.
                    Late alerts are easier to call, for the reason given further down.
                  </p>
                  <SplitTable
                    view={heldAll}
                    caption={`All held-out alerts: rules and agent on ${heldAll.n} alerts`}
                  />
                </>
              )}
              {heldReview && (
                <>
                  <h3>Held-out cases routed to analyst review</h3>
                  <p className="prose">
                    The harness routed {heldReview.count} of the {heldAll?.n ?? heldEarly.n}{" "}
                    held-out alerts to analyst review instead of making a recommendation. They
                    count as escalated above, because an analyst works them.
                  </p>
                  <ReviewReasons review={heldReview} />
                </>
              )}
              {grounding && (
                <>
                  <h3>Citations checked by code</h3>
                  <p className="prose">
                    Of the {grounding.claims} claims the agent made on the held-out alerts,{" "}
                    {grounding.grounded} passed the citation checks: every transfer they cite
                    exists, was fetched during that run, and has matching amounts and currencies.
                  </p>
                </>
              )}
              {held.eval_run_id && (
                <p className="meta">
                  Eval run {held.eval_run_id}
                  {held.prompt_version ? `, prompt ${held.prompt_version}` : ""}.
                </p>
              )}
            </section>
          )}

          {held && (
            <p className="notice warn" role="note">
              The held-out run used prompt {held.prompt_version ?? "a later version"} and the
              development run used prompt {results.prompt_version ?? "an earlier version"}.
              Different prompt versions mean the two sections are not a head-to-head comparison.
            </p>
          )}

          <section aria-labelledby="early-title">
            <h2 id="early-title">Development alerts: {early.n} early alerts</h2>
            <p className="prose">
              The prompts were tuned on these alerts, so these numbers may be optimistic.{" "}
              {early.agent.falseTotal} of them were false positives and {early.agent.realTotal}{" "}
              were true positives. The agent would recommend closing{" "}
              {early.agent.falseClosed} of the false positives. It would also recommend closing{" "}
              {early.agent.realClosed} of the {early.agent.realTotal} true positives. That is why
              an analyst approves every close.
            </p>
            <CountPair o={early.agent} />
            <h3>Against the rules on the same {early.n} development alerts</h3>
            <p className="prose meta">
              The rules escalate every alert they raise, so they close nothing and miss nothing
              they raised.
            </p>
            <SplitTable
              view={early}
              caption={`Early development alerts: rules and agent on ${early.n} alerts`}
            />
          </section>

          {all && (
            <section aria-labelledby="all-title">
              <h2 id="all-title">All {all.n} development alerts</h2>
              <p className="prose">
                Including the late alerts, the agent would close {all.agent.falseClosed} of{" "}
                {all.agent.falseTotal} false positives and {all.agent.realClosed} of{" "}
                {all.agent.realTotal} true positives. These numbers look easier than the early
                ones for the reason below.
              </p>
              <SplitTable view={all} caption={`All development alerts: rules and agent on ${all.n} alerts`} />
            </section>
          )}

          {cutoff && late && (
            <section aria-labelledby="split-title">
              <h2 id="split-title">Why early and late alerts are counted apart</h2>
              <div className="prose">
                <p>
                  The transfers are synthetic. After {cutoff}, ordinary transfers in this data
                  almost stop, while laundering continues. So an alert late in the data is
                  very likely to be laundering just because of its date. Of the {late.n} late
                  development alerts, {late.real} are true positives; of the {early.n} early
                  ones, only {early.agent.realTotal}.
                </p>
                <p>
                  A real bank would not see this pattern, so it is a leak: a clue that makes
                  scores look better for the wrong reason. The headline numbers in both sections use
                  only alerts that end on or before {cutoff}, and the prompts never mention this
                  date pattern.
                </p>
              </div>
            </section>
          )}

          {review && (
            <section aria-labelledby="review-title">
              <h2 id="review-title">Development cases routed to analyst review</h2>
              <p className="prose">
                The harness routed {review.count} of the {all?.n ?? early.n} development alerts
                to analyst review instead of making a recommendation. They count as escalated
                above, because an analyst works them.
              </p>
              <ReviewReasons review={review} />
            </section>
          )}

          <section aria-labelledby="read-title">
            <h2 id="read-title">How to read these numbers</h2>
            <ul className={`plain prose ${styles.notes}`}>
              {(results.notes ?? []).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
            {results.eval_run_id && (
              <p className="meta">
                Development eval run {results.eval_run_id}
                {results.prompt_version ? `, prompt ${results.prompt_version}` : ""}.
              </p>
            )}
          </section>

          {rules171 && (
            <section aria-labelledby="context-title" className={styles.context}>
              <h2 id="context-title">Context: the rules on all {rules171.alerts} alerts</h2>
              <p className="prose">
                The monitoring rules raised {rules171.alerts} alerts in total. Under strict truth{" "}
                {rules171.real} were true positives and {rules171.false} were false positives.
              </p>
              {rules171.population && <p className="prose meta">{rules171.population}</p>}
              <p className="prose meta">
                Do not add these to the numbers above: they count a different set of alerts.
              </p>
            </section>
          )}

          <Guilloche />
          <Attribution />
          <p className="meta">
            To follow one case end to end, <Link href="/watch">watch an investigation</Link>.
          </p>
        </>
      )}
    </>
  );
}
