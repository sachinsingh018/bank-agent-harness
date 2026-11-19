// Small pure helpers for the watch pages. Safe to import from client components: no server
// env, no fetch. Types come from api.ts as type-only imports.

import type { FlowGraph, ShowcaseItem, StepActor } from "./api";

export const PATTERNS = [
  { key: "fan-out", label: "Fan-out" },
  { key: "fan-in", label: "Fan-in" },
  { key: "cycle", label: "Cycle" },
  { key: "scatter-gather", label: "Scatter-gather" },
  { key: "gather-scatter", label: "Gather-scatter" },
] as const;

export type OutcomeKey = "escalate" | "close" | "person";

export const OUTCOMES: { key: OutcomeKey; label: string }[] = [
  { key: "escalate", label: "Escalate" },
  { key: "close", label: "Close" },
  { key: "person", label: "Sent to a person" },
];

export function patternLabel(detector: string | null): string {
  const found = PATTERNS.find((p) => p.key === detector);
  return found ? found.label : "Alert";
}

/** Where a case ended: a proposal to escalate or close, or a person's desk. */
export function outcomeOf(item: { status: string; recommendation: string | null }): OutcomeKey {
  if (item.status !== "proposed") return "person";
  return item.recommendation === "escalate" ? "escalate" : "close";
}

export function outcomeLabel(key: OutcomeKey): string {
  if (key === "escalate") return "Proposed: escalate";
  if (key === "close") return "Proposed: close";
  return "Sent to a person";
}

/** The mark class from globals.css for an outcome. */
export function outcomeMark(key: OutcomeKey): "escalate" | "close" | "review" {
  return key === "person" ? "review" : key;
}

export const ACTOR_LABELS: Record<StepActor, string> = {
  rule: "Monitoring rule",
  agent: "Agent",
  tools: "Data tools",
  harness: "Harness",
  reviewer: "Reviewer agent",
};

export function modelLabel(model: { provider: string; model: string }): string {
  if (model.provider === "scripted") return "scripted test run";
  const name = model.model.split("/").pop() ?? model.model;
  return name.replace(/:free$/, "");
}

/** A featured case for the landing page: a real model run that got through the checks, with
 * a few tool calls, else the newest run. */
export function pickFeatured(items: ShowcaseItem[]): ShowcaseItem | null {
  const real = items.filter((i) => i.model.provider !== "scripted" && i.graph.nodes.length > 2);
  const good = real.filter((i) => i.status === "proposed" && i.tool_steps >= 4);
  return good[0] ?? real[0] ?? items[0] ?? null;
}

export function countVisible(graph: FlowGraph, upTo: number): { nodes: number; edges: number } {
  return {
    nodes: graph.nodes.filter((n) => n.step <= upTo).length,
    edges: graph.edges.filter((e) => e.step <= upTo).length,
  };
}

/** Argument values as short text for the step chips. */
export function argText(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text === undefined) return "";
  return text.length > 60 ? `${text.slice(0, 57)}...` : text;
}
