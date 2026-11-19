// The one data interface for the showcase pages. SHOWCASE_SOURCE picks the implementation:
// the committed bundle (default, used by the public site) or the live API (local
// development). Results and the manifest always come from the bundle. Server side only.

import * as api from "./api";
import type { RunDetail, RunSteps, ShowcasePage } from "./api";
import * as bundle from "./bundle";
import type { Manifest, Results } from "./bundle-types";
import { isBundleMode } from "./source";

export interface ShowcaseData {
  getShowcase(limit?: number): Promise<ShowcasePage>;
  /** null when the run does not exist. */
  getRunSteps(runId: string): Promise<RunSteps | null>;
  /** null when the run does not exist. */
  getRun(runId: string): Promise<RunDetail | null>;
  getManifest(): Promise<Manifest | null>;
  getResults(): Promise<Results | null>;
  listBundleRunIds(): Promise<string[]>;
}

/** A 404 from the API becomes null; every other error is thrown for the page to show. */
async function orNull<T>(load: Promise<T>): Promise<T | null> {
  try {
    return await load;
  } catch (err) {
    if (err instanceof api.ApiError && err.status === 404) return null;
    throw err;
  }
}

const bundleSource: ShowcaseData = {
  getShowcase: bundle.getShowcase,
  getRunSteps: bundle.getRunSteps,
  getRun: bundle.getRun,
  getManifest: bundle.getManifest,
  getResults: bundle.getResults,
  listBundleRunIds: bundle.listBundleRunIds,
};

const apiSource: ShowcaseData = {
  getShowcase: (limit) => api.getShowcase(limit),
  getRunSteps: (runId) => orNull(api.getRunSteps(runId)),
  getRun: (runId) => orNull(api.getRun(runId)),
  getManifest: bundle.getManifest,
  getResults: bundle.getResults,
  listBundleRunIds: bundle.listBundleRunIds,
};

function source(): ShowcaseData {
  return isBundleMode() ? bundleSource : apiSource;
}

export const getShowcase: ShowcaseData["getShowcase"] = (limit) => source().getShowcase(limit);
export const getRunSteps: ShowcaseData["getRunSteps"] = (runId) => source().getRunSteps(runId);
export const getRun: ShowcaseData["getRun"] = (runId) => source().getRun(runId);
export const getManifest: ShowcaseData["getManifest"] = () => source().getManifest();
export const getResults: ShowcaseData["getResults"] = () => source().getResults();
export const listBundleRunIds: ShowcaseData["listBundleRunIds"] = () =>
  source().listBundleRunIds();
