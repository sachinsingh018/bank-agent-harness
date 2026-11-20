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

interface ResultsFile {
  prompt_version: string;
  splits: { early: { counts: SplitCounts } };
  heldout: {
    eval_run_id: string;
    prompt_version: string;
    splits: { early: { n: number; counts: SplitCounts }; all: { n: number } };
    human_review: { count: number };
    grounding: { claims: number; grounded: number };
  };
}

function readResults(): ResultsFile {
  const file = path.join(process.cwd(), "content", "showcase", "v1", "results.json");
  return JSON.parse(readFileSync(file, "utf8")) as ResultsFile;
}

/** The development early split counts from the committed results.json. */
export function earlyCounts(): SplitCounts {
  return readResults().splits.early.counts;
}

/** The held-out block from the committed results.json. */
export function heldout(): ResultsFile["heldout"] {
  return readResults().heldout;
}

/** The development prompt version from the committed results.json. */
export function devPromptVersion(): string {
  return readResults().prompt_version;
}
