import type { Metadata } from "next";

import type { ResultsSplit } from "../../lib/bundle-types";
import { getResults } from "../../lib/data";

export const metadata: Metadata = {
  title: "Results | Alert desk",
  description: "How the agent did on recorded development alerts, in plain counts.",
};

/** One split in plain words, or a short note when the export did not include it. */
function splitText(split: ResultsSplit | null | undefined): string {
  const c = split?.counts;
  if (
    typeof split?.n !== "number" ||
    typeof c?.false_closed !== "number" ||
    typeof c.false_total !== "number" ||
    typeof c.real_closed !== "number" ||
    typeof c.real_total !== "number"
  ) {
    return "not recorded";
  }
  return (
    `${split.n} alerts. The agent would close ${c.false_closed} of ${c.false_total} false ` +
    `alerts and ${c.real_closed} of ${c.real_total} real ones.`
  );
}

// Placeholder until the results page is designed: always reads the committed bundle.
export default async function ResultsPage() {
  const results = await getResults();
  return (
    <>
      <h1>Results</h1>
      {results ? (
        <ul className="plain">
          <li>Early window: {splitText(results.splits?.early)}</li>
          <li>All alerts: {splitText(results.splits?.all)}</li>
        </ul>
      ) : (
        <p className="notice">No results have been exported yet.</p>
      )}
    </>
  );
}
