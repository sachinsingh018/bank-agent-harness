import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import type { RunSteps } from "../../../lib/api";
import { getRunSteps, listBundleRunIds } from "../../../lib/data";
import { loadErrorText } from "../../../lib/errors";
import { isValidRunId } from "../../../lib/flags";
import { formatSeconds, formatUsd } from "../../../lib/format";
import { modelLabel, patternLabel } from "../../../lib/replay";
import { isBundleMode } from "../../../lib/source";
import { Replay, StepFromUrl } from "./replay";

export const metadata: Metadata = {
  title: "Watch an investigation | Alert desk",
};

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
  const caseLink = !isBundleMode();

  if (!run) {
    return (
      <>
        <p className="back">
          <Link href="/watch">All cases</Link>
        </p>
        <p className="notice error">{error}</p>
      </>
    );
  }

  return (
    <>
      <p className="back">
        <Link href="/watch">All cases</Link>
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
          {run.tool_steps} tool call{run.tool_steps === 1 ? "" : "s"}. Model cost{" "}
          {formatUsd(run.cost_usd)}. Press play, or step through with the arrow keys.
        </p>
      </header>
      {/* The replay reads ?step= on the client, so the page itself stays static. */}
      <Suspense fallback={<Replay run={run} caseLink={caseLink} />}>
        <StepFromUrl run={run} caseLink={caseLink} />
      </Suspense>
    </>
  );
}
