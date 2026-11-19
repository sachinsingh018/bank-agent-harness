import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import type { RunDetail, RunSteps } from "../../../lib/api";
import { getBundleRun } from "../../../lib/bundle";
import type { BundleRun } from "../../../lib/bundle-types";
import { getRun, getRunSteps, listBundleRunIds } from "../../../lib/data";
import { loadErrorText } from "../../../lib/errors";
import { isValidRunId } from "../../../lib/flags";
import { pageMeta, runDescription, runTitle, SITE_DESCRIPTION } from "../../../lib/meta";
import { formatSeconds, formatUsd } from "../../../lib/format";
import { modelLabel, patternLabel } from "../../../lib/replay";
import { isBundleMode } from "../../../lib/source";
import { CaseFileView } from "./case-file-view";
import { ProvenanceStrip } from "./provenance";
import { Replay, StepFromUrl, type ReviewSummary } from "./replay";

/** Title and description name the alert pattern and how the case ended. */
export async function generateMetadata({
  params,
}: {
  params: Promise<{ runId: string }>;
}): Promise<Metadata> {
  const { runId } = await params;
  let run: RunSteps | null = null;
  try {
    run = isValidRunId(runId) ? await getRunSteps(runId) : null;
  } catch {
    run = null; // the page itself shows the load error
  }
  if (!run) return pageMeta("Recorded investigation | AML Alert Desk", SITE_DESCRIPTION);
  return pageMeta(runTitle(run), runDescription(run));
}

/** Seconds for short runs, minutes for long ones. */
function duration(ms: number): string {
  return ms < 90_000 ? formatSeconds(ms) : `${Math.round(ms / 60_000)} min`;
}

async function bundleParams(): Promise<{ runId: string }[]> {
  return (await listBundleRunIds()).map((runId) => ({ runId }));
}

/** Bundle mode prerenders every recorded run. API mode exports no generateStaticParams, so the
 * route renders on request as before: an empty list would make it a static route whose live
 * API fetches fail at runtime. */
export const generateStaticParams = isBundleMode() ? bundleParams : undefined;

/** The case file in API mode, or null when the API cannot serve it; the tab is then hidden. */
async function liveCase(runId: string): Promise<RunDetail | null> {
  try {
    return await getRun(runId);
  } catch {
    return null;
  }
}

/** The stored reviewer agent row, or, without a case file, the replay's own review step. */
function reviewOf(detail: RunDetail | null, run: RunSteps): ReviewSummary | null {
  if (detail) {
    return detail.review
      ? { verdict: detail.review.verdict, failure_reason: detail.review.failure_reason }
      : null;
  }
  const step = run.steps.find((s) => s.kind === "review");
  return step?.verdict ? { verdict: step.verdict, failure_reason: null } : null;
}

export default async function WatchRun({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  if (!isValidRunId(runId)) notFound();

  let run: RunSteps | null | undefined;
  let error: string | null = null;
  try {
    run = await getRunSteps(runId);
  } catch (err) {
    error = loadErrorText(err, "investigation");
  }
  // Outside the try: notFound() works by throwing.
  if (run === null) notFound();

  // The queue case page exists only in API mode.
  const bundleMode = isBundleMode();
  const caseLink = !bundleMode;
  let recorded: BundleRun | null = null;
  let detail: RunDetail | null = null;
  if (run) {
    recorded = bundleMode ? await getBundleRun(runId) : null;
    detail = bundleMode ? (recorded?.case ?? null) : await liveCase(runId);
  }

  if (!run) {
    return (
      <>
        <p className="back">
          <Link href="/watch">Case library</Link>
        </p>
        <p className="notice error">{error}</p>
      </>
    );
  }

  const provenance = recorded?.provenance ?? null;
  const effort = recorded?.effort ?? null;
  const caseFile: ReactNode = detail ? <CaseFileView run={detail} /> : undefined;
  const props = { run, caseLink, caseFile, effort, review: reviewOf(detail, run) };

  return (
    <>
      <p className="back">
        <Link href="/watch">Case library</Link>
      </p>
      <header className="watch-head">
        <p className="eyebrow">
          {patternLabel(run.alert.detector)} alert {run.alert.alert_id}
        </p>
        <h1 className="display">
          {run.alert.detector_label ?? "A monitoring rule raised this alert"}
        </h1>
        <p className="meta">
          Investigated by {modelLabel(run.model)} in {duration(run.wall_ms)} with{" "}
          {run.tool_steps} tool call{run.tool_steps === 1 ? "" : "s"}.
          {provenance ? "" : ` Model cost ${formatUsd(run.cost_usd)}.`} Press play, or step
          through with the arrow keys.
        </p>
      </header>
      {provenance && (
        <ProvenanceStrip
          provenance={provenance}
          effort={effort}
          rawHref={`/watch/${encodeURIComponent(run.run_id)}/raw`}
        />
      )}
      {/* The replay reads ?step= on the client, so the page itself stays static. */}
      <Suspense fallback={<Replay {...props} />}>
        <StepFromUrl {...props} />
      </Suspense>
    </>
  );
}
