import Link from "next/link";
import { notFound } from "next/navigation";

import { getCorrections, type CorrectionsPage } from "../../lib/api";
import { loadErrorText } from "../../lib/errors";
import { actorLabel, formatTime } from "../../lib/format";
import { isBundleMode } from "../../lib/source";

export const metadata = { title: "Lessons | Alert desk" };

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LessonsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  // The public site has no API: these pages exist only in API mode.
  if (isBundleMode()) notFound();
  const cursor = one((await searchParams).cursor);

  let page: CorrectionsPage | null = null;
  let error: string | null = null;
  try {
    page = await getCorrections(cursor);
  } catch (err) {
    error = loadErrorText(err, "lessons");
  }

  return (
    <>
      <h1>Lessons</h1>
      <p className="lede">
        Short rules people saved with their decisions, newest first. When lessons are turned on
        for a run, the agent is shown up to five relevant ones as general guidance, never from the
        same alert. They are off by default.
      </p>

      {error && <p className="notice error">{error}</p>}

      {page && page.items.length === 0 && (
        <p className="notice">No lessons saved yet. A reviewer can add one with any decision.</p>
      )}

      {page && page.items.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">Saved</th>
                <th scope="col">About</th>
                <th scope="col">Lesson</th>
                <th scope="col">From alert</th>
                <th scope="col">By</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((c) => (
                <tr key={c.id}>
                  <td className="meta">{formatTime(c.created_at)}</td>
                  <td>{c.kind}</td>
                  <td className="wrap">{c.rule}</td>
                  <td>
                    <Link href={`/queue/${encodeURIComponent(c.run_id)}`}>{c.case_id}</Link>
                  </td>
                  <td>{actorLabel(c.author)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="pager">
        {cursor && <Link href="/lessons">First page</Link>}
        {page?.next_cursor && (
          <Link href={`/lessons?cursor=${encodeURIComponent(page.next_cursor)}`}>Next page</Link>
        )}
      </div>
    </>
  );
}
