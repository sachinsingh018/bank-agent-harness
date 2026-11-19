// Reads the committed showcase bundle (content/showcase/v1) from disk. Server side only: it
// runs at build time for static pages, and never calls the API.

import { readFile } from "node:fs/promises";
import path from "node:path";

import type { RunDetail, RunSteps, ShowcaseItem, ShowcasePage } from "./api";
import type { BundleRun, Manifest, Results } from "./bundle-types";
import { isValidRunId } from "./flags";

function bundleDir(): string {
  return path.join(process.cwd(), "content", "showcase", "v1");
}

/** The parsed JSON file, or null when it does not exist. */
async function readJson<T>(...parts: string[]): Promise<T | null> {
  try {
    return JSON.parse(await readFile(path.join(bundleDir(), ...parts), "utf8")) as T;
  } catch (err) {
    if (err instanceof Error && "code" in err && err.code === "ENOENT") return null;
    throw err;
  }
}

export function getManifest(): Promise<Manifest | null> {
  return readJson<Manifest>("manifest.json");
}

export function getResults(): Promise<Results | null> {
  return readJson<Results>("results.json");
}

/** Run ids in gallery order; empty when there is no bundle. */
export async function listBundleRunIds(): Promise<string[]> {
  const manifest = await getManifest();
  return (manifest?.order ?? []).filter(isValidRunId);
}

/** The cases in manifest order, at most limit of them. */
export async function getShowcase(limit = 200): Promise<ShowcasePage> {
  const items = (await readJson<ShowcaseItem[]>("showcase.json")) ?? [];
  const order = await listBundleRunIds();
  const rank = new Map(order.map((id, i) => [id, i]));
  const sorted = items
    .filter((item) => rank.has(item.run_id))
    .sort((a, b) => (rank.get(a.run_id) ?? 0) - (rank.get(b.run_id) ?? 0));
  return { items: sorted.slice(0, limit) };
}

/** The whole run file, or null for an id that is not in the bundle. */
export async function getBundleRun(runId: string): Promise<BundleRun | null> {
  if (!isValidRunId(runId)) return null;
  if (!(await listBundleRunIds()).includes(runId)) return null;
  return readJson<BundleRun>("runs", `${runId}.json`);
}

export async function getRunSteps(runId: string): Promise<RunSteps | null> {
  return (await getBundleRun(runId))?.steps ?? null;
}

export async function getRun(runId: string): Promise<RunDetail | null> {
  return (await getBundleRun(runId))?.case ?? null;
}
