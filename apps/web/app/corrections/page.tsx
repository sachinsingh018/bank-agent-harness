import Link from "next/link";

import { ApiError, getCorrections, type CorrectionsPage } from "../../lib/api";
import { formatTime } from "../../lib/format";

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function CorrectionsPageView({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const cursor = one((await searchParams).cursor);

  let page: CorrectionsPage | null = null;
  let error: string | null = null;
  try {
    page = await getCorrections(cursor);
  } catch (err) {
    if (err instanceof ApiError && err.status === 0) {
      error = "The API is unreachable. Check that it is running and that API_BASE_URL is set.";
    } else if (err instanceof ApiError && err.status === 422) {
      error = "That page link is no longer valid. Start again from the first page.";
    } else if (err instanceof ApiError) {
      error = `The API returned an error (${err.status}): ${err.detail}.`;
    } else {
      error = "Something went wrong while loading the lessons.";
    }
  }

  return (
    <>
      <h1>Lessons</h1>
      <p className="muted">
        Short rules reviewers saved with their decisions, newest first. They are stored for
        future cases; the agent does not read them yet.
      </p>

      {error && <p className="notice error">{error}</p>}

      {page && page.items.length === 0 && <p className="notice">No lessons saved yet.</p>}

      {page && page.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Saved</th>
                <th>Kind</th>
                <th>Rule</th>
                <th>Case</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((c) => (
                <tr key={c.id}>
                  <td>{formatTime(c.created_at)}</td>
                  <td>{c.kind}</td>
                  <td className="wrap">{c.rule}</td>
                  <td>
                    <Link href={`/queue/${encodeURIComponent(c.run_id)}`} className="mono">
                      {c.case_id}
                    </Link>
                  </td>
                  <td>{c.author.replace(/^human:/, "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="pager">
        {cursor && <Link href="/corrections">First page</Link>}
        {page?.next_cursor && (
          <Link href={`/corrections?cursor=${encodeURIComponent(page.next_cursor)}`}>
            Next page
          </Link>
        )}
      </div>
    </>
  );
}
