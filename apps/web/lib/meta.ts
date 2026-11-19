// Page titles, descriptions and share card text. Pure helpers: no env, no fetch. The share
// images themselves come from the opengraph-image files, which Next adds to every page below.

import type { Metadata } from "next";

import type { RunSteps } from "./api";
import { outcomeLabel, outcomeOf, patternLabel } from "./replay";

export const SITE_NAME = "AML Alert Desk";

export const SITE_DESCRIPTION =
  "Watch an AI agent investigate money laundering alerts step by step: read-only tools, checks in code, and an analyst approves every decision.";

/** Title, description and the matching Open Graph and Twitter fields for one page. */
export function pageMeta(title: string, description: string): Metadata {
  return {
    title,
    description,
    openGraph: { title, description, siteName: SITE_NAME, type: "website" },
    twitter: { card: "summary_large_image", title, description },
  };
}

/** The alert pattern and where the case ended, for a run's title, description and card. */
export function runSummary(run: RunSteps): { pattern: string; outcome: string } {
  const step = [...run.steps].reverse().find((s) => s.kind === "outcome");
  const key = outcomeOf({ status: run.status, recommendation: step?.recommendation ?? null });
  return { pattern: patternLabel(run.alert.detector), outcome: outcomeLabel(key) };
}

/** "Fan-out alert", or just "Alert" when the pattern is unknown (never "Alert alert"). */
export function alertName(pattern: string): string {
  return pattern === "Alert" ? "Alert" : `${pattern} alert`;
}

/** Absolute base for share image URLs: the production domain on Vercel, localhost otherwise. */
export function siteUrl(): URL {
  const host = process.env.VERCEL_PROJECT_PRODUCTION_URL;
  return new URL(host ? `https://${host}` : "http://localhost:3000");
}

export function runTitle(run: RunSteps): string {
  const { pattern, outcome } = runSummary(run);
  return `${alertName(pattern)}, ${outcome.toLowerCase()} | ${SITE_NAME}`;
}

export function runDescription(run: RunSteps): string {
  const { pattern, outcome } = runSummary(run);
  const calls = `${run.tool_steps} tool call${run.tool_steps === 1 ? "" : "s"}`;
  return (
    `A recorded ${alertName(pattern).toLowerCase()}, replayed step by step: the agent made ${calls} ` +
    `and the case ended as "${outcome}". An analyst approves every decision.`
  );
}
