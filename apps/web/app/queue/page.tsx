import Link from "next/link";

import { ApiError, getQueue, type QueuePage, type QueueState } from "../../lib/api";
import { formatConfidence, formatStatus, formatTime } from "../../lib/format";

const TABS: { state: QueueState; label: string }[] = [
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
  const params = await searchParams;
  const state = toState(one(params.state));
  const cursor = one(params.cursor);

  let page: QueuePage | null = null;
  let error: string | null = null;
  try {
    page = await getQueue(state, cursor);
  } catch (err) {
    if (err instanceof ApiError && err.status === 0) {
      error = "The API is unreachable. Check that it is running and that API_BASE_URL is set.";
    } else if (err instanceof ApiError && err.status === 422) {
      error = "That page link is no longer valid. Start again from the first page.";
    } else if (err instanceof ApiError) {
      error = `The API returned an error (${err.status}): ${err.detail}.`;
    } else {
      error = "Something went wrong while loading the queue.";
    }
  }

  return (
    <>
      <h1>Approval queue</h1>
      <p className="muted">
        The agent proposes, a person decides. Waiting cases have no human decision yet.
      </p>
      <nav className="tabs" aria-label="Queue state">
        {TABS.map((tab) => (
          <Link
            key={tab.state}
            href={`/queue?state=${tab.state}`}
            aria-current={tab.state === state ? "page" : undefined}
          >
            {tab.label}
          </Link>
        ))}
      </nav>

      {error && <p className="notice error">{error}</p>}

      {page && page.items.length === 0 && (
        <p className="notice">
          {state === "waiting" ? "No cases are waiting for a decision." : "No cases to show."}
        </p>
      )}

      {page && page.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Alert</th>
                <th>Status</th>
                <th>Recommendation</th>
                <th className="num">Confidence</th>
                <th>Typology</th>
                <th>Created</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((item) => (
                <tr key={item.run_id}>
                  <td>
                    <Link href={`/queue/${encodeURIComponent(item.run_id)}`} className="mono">
                      {item.case_id}
                    </Link>
                  </td>
                  <td>{formatStatus(item.status, item.reason)}</td>
                  <td>{item.recommendation ?? ""}</td>
                  <td className="num">{formatConfidence(item.confidence)}</td>
                  <td>{item.typology ?? ""}</td>
                  <td>{formatTime(item.created_at)}</td>
                  <td>
                    {item.decision
                      ? `${item.decision.decision} by ${item.decision.actor.replace(/^human:/, "")}`
                      : "waiting"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="pager">
        {cursor && <Link href={`/queue?state=${state}`}>First page</Link>}
        {page?.next_cursor && (
          <Link
            href={`/queue?state=${state}&cursor=${encodeURIComponent(page.next_cursor)}`}
          >
            Next page
          </Link>
        )}
      </div>
    </>
  );
}
