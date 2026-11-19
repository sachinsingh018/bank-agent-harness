// Reads the committed showcase manifest so tests follow whatever runs the bundle holds.

import { readFileSync } from "node:fs";
import path from "node:path";

interface ManifestIds {
  order: string[];
}

export function firstRunId(): string {
  const file = path.join(process.cwd(), "content", "showcase", "v1", "manifest.json");
  const manifest = JSON.parse(readFileSync(file, "utf8")) as ManifestIds;
  const first = manifest.order[0];
  if (!first) throw new Error("The showcase manifest lists no runs.");
  return first;
}

export interface SplitCounts {
  false_closed: number;
  false_total: number;
  real_closed: number;
  real_total: number;
}

/** The early split counts from the committed results.json. */
export function earlyCounts(): SplitCounts {
  const file = path.join(process.cwd(), "content", "showcase", "v1", "results.json");
  const results = JSON.parse(readFileSync(file, "utf8")) as {
    splits: { early: { counts: SplitCounts } };
  };
  return results.splits.early.counts;
}
