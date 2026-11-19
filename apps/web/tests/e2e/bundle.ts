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
