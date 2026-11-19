// The money-flow diagram: accounts as dots, transfers as curved arrows, thicker when there are
// more transfers. Inline SVG with positions from the API's deterministic layout, so the same
// case always looks the same. No hooks, so server and client components can both use it.
// Arrowheads are drawn as small triangles (no marker ids, so many diagrams can share a page).
// The replay opts in to focusable money links and to money moving along the links (motion);
// the gallery and the landing keep the plain picture.

import type { FlowEdge, FlowGraph, FlowNode } from "../../lib/api";
import { amountText, shortTs } from "../../lib/replay";
import styles from "./flow.module.css";
import { FlowMotion, type MoneyTrip } from "./flow-motion";

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
  return { path, head, start, control, end };
}

/** Points along the quadratic curve, for a dot of money travelling it. */
function curvePoints(start: Point, control: Point, end: Point, count = 9): [number, number][] {
  return Array.from({ length: count }, (_, i) => {
    const t = i / (count - 1);
    const a = (1 - t) * (1 - t);
    const b = 2 * (1 - t) * t;
    const c = t * t;
    return [
      Number((a * start.x + b * control.x + c * end.x).toFixed(2)),
      Number((a * start.y + b * control.y + c * end.y).toFixed(2)),
    ];
  });
}

/** At most this many dots move per step; the rest of the transfers stay on the drawn line. */
const MAX_TRIPS = 30;

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

/** The edge in words with its amounts, for screen readers and the hover tooltip. */
export function edgeAmountsText(e: FlowEdge): string {
  const transfers = [...(e.transfers ?? [])].sort((a, b) => a.ts.localeCompare(b.ts));
  if (transfers.length === 0) return edgeTitle(e);
  const shown = transfers.slice(0, 4).map((t) => `${amountText(t.amount, t.currency)} on ${shortTs(t.ts)}`);
  const more = transfers.length > shown.length ? `, and ${transfers.length - shown.length} more` : "";
  return `${edgeTitle(e)}. ${shown.join("; ")}${more}`;
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
  motion = false,
  interactive,
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
  /** Opt in: dots of money move along the links first seen at freshStep, in time order. */
  motion?: boolean;
  /** Opt in: money links become focusable buttons that report hover and focus. */
  interactive?: {
    /** Unique prefix for the title and description ids. */
    idBase: string;
    description: string;
    /** The link in focus, drawn as in focus. */
    focusEdge: string | null;
    /** The link pinned by a click, if any. */
    pinnedEdge: string | null;
    onEdgeFocus: (edgeId: string | null) => void;
    onEdgePin: (edgeId: string) => void;
  };
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
  const trips: MoneyTrip[] = [];
  if (motion && freshStep !== undefined) {
    const moving = edges
      .filter((e) => e.step === freshStep)
      .flatMap((e) => (e.transfers ?? []).map((t) => ({ e, t })))
      .sort((a, b) => a.t.ts.localeCompare(b.t.ts) || a.t.txn_id - b.t.txn_id)
      .slice(0, MAX_TRIPS);
    const gap = moving.length > 0 ? Math.min(240, 1800 / moving.length) : 0;
    moving.forEach(({ e, t }, i) => {
      const from = byId.get(e.source);
      const to = byId.get(e.target);
      if (!from || !to) return;
      const shape = edgeShape(from, to, variant, strokeWidth(e, variant));
      trips.push({
        id: `${e.id}#${t.txn_id}`,
        points: curvePoints(shape.start, shape.control, shape.end),
        delay: Math.round(i * gap),
      });
    });
  }
  const titleId = interactive ? `${interactive.idBase}-title` : undefined;
  const descId = interactive ? `${interactive.idBase}-desc` : undefined;

  return (
    <svg
      className={`flow flow-${variant}`}
      viewBox="-104 -104 208 208"
      role={interactive ? "group" : "img"}
      aria-label={interactive ? undefined : label}
      aria-labelledby={titleId}
      aria-describedby={descId}
      preserveAspectRatio="xMidYMid meet"
    >
      {interactive && (
        <>
          <title id={titleId}>{label}</title>
          <desc id={descId}>{interactive.description}</desc>
        </>
      )}
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
          const focused =
            interactive && (interactive.focusEdge === e.id || interactive.pinnedEdge === e.id);
          const classes = [
            "flow-edge",
            hot(e) ? "hot" : "",
            e.step === freshStep ? "fresh" : "",
            interactive ? styles.edgeButton : "",
            focused ? styles.edgeFocus : "",
          ]
            .filter(Boolean)
            .join(" ");
          if (!interactive) {
            return (
              <g key={e.id} className={classes}>
                <title>{edgeTitle(e)}</title>
                <path d={path} strokeWidth={width} pathLength={1} />
                <path d={head} className="head" />
              </g>
            );
          }
          const words = edgeAmountsText(e);
          return (
            <g
              key={e.id}
              className={classes}
              data-edge={e.id}
              role="button"
              tabIndex={0}
              aria-label={words}
              aria-pressed={interactive.pinnedEdge === e.id}
              onMouseEnter={() => interactive.onEdgeFocus(e.id)}
              onMouseLeave={() => interactive.onEdgeFocus(null)}
              onFocus={() => interactive.onEdgeFocus(e.id)}
              onBlur={() => interactive.onEdgeFocus(null)}
              onClick={() => interactive.onEdgePin(e.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  event.stopPropagation();
                  interactive.onEdgePin(e.id);
                }
              }}
            >
              <path d={path} strokeWidth={width} pathLength={1} />
              <path d={head} className="head" />
              <path d={path} className={styles.hit} />
            </g>
          );
        })}
      </g>
      {trips.length > 0 && <FlowMotion trips={trips} />}
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
