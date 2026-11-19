// The money-flow diagram: accounts as dots, transfers as curved arrows, thicker when there are
// more transfers. Inline SVG with positions from the API's deterministic layout, so the same
// case always looks the same. No hooks, so server and client components can both use it.
// Arrowheads are drawn as small triangles (no marker ids, so many diagrams can share a page).

import type { FlowEdge, FlowGraph, FlowNode } from "../../lib/api";

type Variant = "card" | "board";

interface Sizes {
  anchor: number;
  alert: number;
  found: number;
  arrow: number;
  base: number;
}

const SIZES: Record<Variant, Sizes> = {
  card: { anchor: 7, alert: 4.6, found: 4, arrow: 4.2, base: 1.1 },
  board: { anchor: 5.6, alert: 3.6, found: 3, arrow: 2.8, base: 0.55 },
};

interface Point {
  x: number;
  y: number;
}

function unit(dx: number, dy: number): Point {
  const length = Math.hypot(dx, dy) || 1;
  return { x: dx / length, y: dy / length };
}

function radius(node: FlowNode, variant: Variant): number {
  return SIZES[variant][node.role];
}

function round(value: number): string {
  return value.toFixed(2);
}

/** A gentle curve from source to target that stops at the target's edge, plus its arrowhead.
 * The bend always goes to the same side, so A to B and B to A never overlap. */
function edgeShape(from: FlowNode, to: FlowNode, variant: Variant, width: number) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  const bend = Math.min(0.18 * length, 14);
  const control = {
    x: (from.x + to.x) / 2 - (dy / length) * bend,
    y: (from.y + to.y) / 2 + (dx / length) * bend,
  };
  const out = unit(control.x - from.x, control.y - from.y);
  const into = unit(to.x - control.x, to.y - control.y);
  const arrow = SIZES[variant].arrow + width * 0.6;
  const r1 = radius(from, variant);
  const r2 = radius(to, variant) + 0.8;
  const start = { x: from.x + out.x * r1, y: from.y + out.y * r1 };
  const tip = { x: to.x - into.x * r2, y: to.y - into.y * r2 };
  const end = { x: tip.x - into.x * arrow * 0.8, y: tip.y - into.y * arrow * 0.8 };
  const side = { x: -into.y * arrow * 0.5, y: into.x * arrow * 0.5 };
  const head =
    `M${round(tip.x)} ${round(tip.y)} ` +
    `L${round(end.x + side.x)} ${round(end.y + side.y)} ` +
    `L${round(end.x - side.x)} ${round(end.y - side.y)} Z`;
  const path =
    `M${round(start.x)} ${round(start.y)} ` +
    `Q${round(control.x)} ${round(control.y)} ${round(end.x)} ${round(end.y)}`;
  return { path, head };
}

function strokeWidth(edge: FlowEdge, variant: Variant): number {
  return SIZES[variant].base + Math.min(2.4, Math.log2(edge.count + 1) * 0.55);
}

const ROLE_WORDS: Record<FlowNode["role"], string> = {
  anchor: "flagged by the rule",
  alert: "in the alert",
  found: "found by the agent",
};

function edgeTitle(e: FlowEdge): string {
  return `${e.source} paid ${e.target}: ${e.count} transfer${e.count === 1 ? "" : "s"}`;
}

function shortAccount(node: FlowNode): string {
  return `${node.bank}/${node.acct}`;
}

export function FlowDiagram({
  graph,
  variant,
  label,
  upTo,
  freshStep,
  highlight,
}: {
  graph: FlowGraph;
  variant: Variant;
  label: string;
  /** Show only what the investigation had seen by this step; all when undefined. */
  upTo?: number;
  /** Accounts and transfers first seen at this step grow in. */
  freshStep?: number;
  /** Transfer ids to mark as cited or in focus. */
  highlight?: readonly number[];
}) {
  const visible = (step: number) => upTo === undefined || step <= upTo;
  const nodes = graph.nodes.filter((n) => visible(n.step));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const marked = new Set(highlight ?? []);
  const edges = graph.edges.filter(
    (e) => visible(e.step) && byId.has(e.source) && byId.has(e.target),
  );
  const hot = (e: FlowEdge) => e.txn_ids.some((t) => marked.has(t));
  const hotNodes = new Set(edges.filter(hot).flatMap((e) => [e.source, e.target]));
  // Highlighted transfers are drawn last, so they sit on top.
  const ordered = [...edges.filter((e) => !hot(e)), ...edges.filter(hot)];

  return (
    <svg
      className={`flow flow-${variant}`}
      viewBox="-104 -104 208 208"
      role="img"
      aria-label={label}
      preserveAspectRatio="xMidYMid meet"
    >
      <g className="flow-guides" aria-hidden="true">
        <circle r="56" />
        <circle r="90" />
      </g>
      <g className="flow-edges">
        {ordered.map((e) => {
          const from = byId.get(e.source);
          const to = byId.get(e.target);
          if (!from || !to) return null;
          const width = strokeWidth(e, variant);
          const { path, head } = edgeShape(from, to, variant, width);
          const classes = ["flow-edge", hot(e) ? "hot" : "", e.step === freshStep ? "fresh" : ""]
            .filter(Boolean)
            .join(" ");
          return (
            <g key={e.id} className={classes}>
              <title>{edgeTitle(e)}</title>
              <path d={path} strokeWidth={width} pathLength={1} />
              <path d={head} className="head" />
            </g>
          );
        })}
      </g>
      <g className="flow-nodes">
        {nodes.map((n) => {
          const classes = [
            "flow-node",
            n.role,
            hotNodes.has(n.id) ? "hot" : "",
            n.step === freshStep ? "fresh" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <g key={n.id} className={classes} transform={`translate(${round(n.x)} ${round(n.y)})`}>
              <title>{`Account ${shortAccount(n)}, ${ROLE_WORDS[n.role]}`}</title>
              <circle r={radius(n, variant)} />
              {variant === "board" && n.role === "anchor" && (
                <text y={-radius(n, variant) - 3} textAnchor="middle">
                  {shortAccount(n)}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}
