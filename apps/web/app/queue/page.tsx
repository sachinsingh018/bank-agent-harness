import Link from "next/link";
import { notFound } from "next/navigation";

import { getQueue, type QueuePage, type QueueState } from "../../lib/api";
import { loadErrorText } from "../../lib/errors";
import {
  actorLabel,
  decisionLabel,
  formatConfidence,
  formatTime,
  reasonLabel,
  saysKind,
} from "../../lib/format";
import { Meter, ReviewerWord, Says } from "../_ui/marks";
import { isBundleMode } from "../../lib/source";

const VIEWS: { state: QueueState; label: string }[] = [
  { state: "waiting", label: "Waiting" },
  { state: "decided", label: "Decided" },
  { state: "all", label: "All" },
];

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function toState(value: string | undefined): QueueState {
  return value === "decided" || value === "all" ? value : "waiting";
}

export default async function QueuePageView({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  // The public site has no API: these pages exist only in API mode.
  if (isBundleMode()) notFound();
  const params = await searchParams;
  const state = toState(one(params.state));
  const cursor = one(params.cursor);

  let page: QueuePage | null = null;
  let error: string | null = null;
  try {
    page = await getQueue(state, cursor);
  } catch (err) {
    error = loadErrorText(err, "queue");
  }

  return (
    <>
      <h1>Queue</h1>
      <p className="lede">
        Alerts raised by the monitoring rules and investigated by the agent. A second agent
        reviews each answer, and a person makes the final call. Waiting cases have no human
        decision yet.
      </p>
      <nav className="views" aria-label="Which cases">
        {VIEWS.map((view) => (
          <Link
            key={view.state}
            href={`/queue?state=${view.state}`}
            aria-current={view.state === state ? "page" : undefined}
          >
            {view.label}
          </Link>
        ))}
      </nav>

      {error && <p className="notice error">{error}</p>}

      {page && page.items.length === 0 && (
        <p className="notice">
          {state === "waiting"
            ? "No cases are waiting for a decision. Open All to see decided cases."
            : "No cases to show yet."}
        </p>
      )}

      {page && page.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Alert</th>
                <th scope="col">Agent says</th>
                <th scope="col">Confidence</th>
                <th scope="col">Typology</th>
                <th scope="col">Reviewer</th>
                <th scope="col">Outcome</th>
                <th scope="col">Recorded</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => {
                const kind = saysKind(item.recommendation, item.status);
                return (
                  <tr key={item.run_id}>
                    <td>
                      <Link href={`/queue/${encodeURIComponent(item.run_id)}`}>
                        {item.case_id}
                      </Link>
                    </td>
                    <td>
                      <Says recommendation={item.recommendation} status={item.status} />
                      {item.status === "human_review" && (
                        <span className="meta"> to a person: {reasonLabel(item.reason)}</span>
                      )}
                    </td>
                    <td>
                      {item.confidence === null ? (
                        <span className="muted">n/a</span>
                      ) : (
                        <>
                          {formatConfidence(item.confidence)}
                          <Meter value={item.confidence} kind={kind} mini />
                        </>
                      )}
                    </td>
                    <td className="typology">{item.typology ?? ""}</td>
                    <td>
                      <ReviewerWord verdict={item.review_verdict} />
                    </td>
                    <td>
                      {item.decision
                        ? `${decisionLabel(item.decision.decision)} by ${actorLabel(item.decision.actor)}`
                        : "Waiting"}
                    </td>
                    <td className="meta">{formatTime(item.created_at)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="pager">
        {cursor && <Link href={`/queue?state=${state}`}>First page</Link>}
        {page?.next_cursor && (
          <Link href={`/queue?state=${state}&cursor=${encodeURIComponent(page.next_cursor)}`}>
            Next page
          </Link>
        )}
      </div>
    </>
  );
}
