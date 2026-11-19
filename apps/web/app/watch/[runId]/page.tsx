import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiError, getRunSteps, type RunSteps } from "../../../lib/api";
import { loadErrorText } from "../../../lib/errors";
import { isValidRunId } from "../../../lib/flags";
import { formatSeconds, formatUsd } from "../../../lib/format";
import { modelLabel, patternLabel } from "../../../lib/replay";
import { Replay } from "./replay";

export const metadata: Metadata = {
  title: "Watch an investigation | Alert desk",
};

/** Seconds for short runs, minutes for long ones. */
function duration(ms: number): string {
  return ms < 90_000 ? formatSeconds(ms) : `${Math.round(ms / 60_000)} min`;
}

/** The 0-based step to open at from a 1-based ?step= value; the first step otherwise. */
function startAt(asked: number, count: number): number {
  return Number.isInteger(asked) ? Math.min(Math.max(asked, 1), count) - 1 : 0;
}

export default async function WatchRun({
  params,
  searchParams,
}: {
  params: Promise<{ runId: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { runId } = await params;
  const query = await searchParams;
  const asked = Number(Array.isArray(query.step) ? query.step[0] : query.step);
  if (!isValidRunId(runId)) notFound();

  let run: RunSteps | null = null;
  let error: string | null = null;
  try {
    run = await getRunSteps(runId);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) notFound();
    error = loadErrorText(err, "investigation");
  }

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
      <Replay run={run} initialStep={startAt(asked, run.steps.length)} />
    </>
  );
}
