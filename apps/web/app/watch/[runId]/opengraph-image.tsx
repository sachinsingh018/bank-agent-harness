// The share image for one recorded run: its alert pattern and how the case ended. Bundle mode
// prerenders one per run at build time; API mode draws it on request.

import type { RunSteps } from "../../../lib/api";
import { listBundleRunIds } from "../../../lib/bundle";
import { getRunSteps } from "../../../lib/data";
import { isValidRunId } from "../../../lib/flags";
import { alertName, runSummary } from "../../../lib/meta";
import { isBundleMode } from "../../../lib/source";
import { OG_CONTENT_TYPE, OG_SIZE, ogCard } from "../../_ui/og-card";

export const alt = "A recorded money laundering alert investigation, replayed step by step";
export const size = OG_SIZE;
export const contentType = OG_CONTENT_TYPE;

async function bundleParams(): Promise<{ runId: string }[]> {
  return (await listBundleRunIds()).map((runId) => ({ runId }));
}

export const generateStaticParams = isBundleMode() ? bundleParams : undefined;

export default async function Image({ params }: { params: Promise<{ runId: string }> }) {
  const { runId } = await params;
  let run: RunSteps | null = null;
  try {
    run = isValidRunId(runId) ? await getRunSteps(runId) : null;
  } catch {
    run = null; // a plain card is better than no card
  }
  if (!run) {
    return ogCard({
      eyebrow: "Watch an investigation",
      title: "A recorded money laundering alert, replayed step by step.",
      detail: "Read-only tools, checks in code, and a person approves every decision.",
    });
  }
  const { pattern, outcome } = runSummary(run);
  const calls = `${run.tool_steps} tool call${run.tool_steps === 1 ? "" : "s"}`;
  return ogCard({
    eyebrow: `${alertName(pattern)} ${run.alert.alert_id}`,
    title: `Outcome: ${outcome}`,
    detail: `Replayed step by step: the agent made ${calls}, checks in code ran on its answer, and a person approves the decision.`,
  });
}
