import type { Metadata } from "next";
import Link from "next/link";

import { getShowcase, type ShowcaseItem } from "../../lib/api";
import { loadErrorText } from "../../lib/errors";
import {
  OUTCOMES,
  PATTERNS,
  modelLabel,
  outcomeLabel,
  outcomeMark,
  outcomeOf,
  patternLabel,
  type OutcomeKey,
} from "../../lib/replay";
import { FlowDiagram } from "../_ui/flow";

export const metadata: Metadata = {
  title: "Watch an investigation | Alert desk",
  description: "Pick a recorded money laundering alert and watch the agent investigate it.",
};

function one(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function toPattern(value: string | undefined): string | null {
  return PATTERNS.some((p) => p.key === value) ? (value as string) : null;
}

function toOutcome(value: string | undefined): OutcomeKey | null {
  return OUTCOMES.some((o) => o.key === value) ? (value as OutcomeKey) : null;
}

function href(pattern: string | null, outcome: OutcomeKey | null): string {
  const params = new URLSearchParams();
  if (pattern) params.set("pattern", pattern);
  if (outcome) params.set("outcome", outcome);
  const query = params.toString();
  return query ? `/watch?${query}` : "/watch";
}

function Filter({
  label,
  options,
  current,
  link,
  counts,
}: {
  label: string;
  options: { key: string; label: string }[];
  current: string | null;
  link: (key: string | null) => string;
  counts: Map<string, number>;
}) {
  return (
    <div className="filter" role="group" aria-label={label}>
      <span className="filter-label">{label}</span>
      <Link
        href={link(null)}
        className="chip"
        aria-current={current === null ? "true" : undefined}
      >
        All
      </Link>
      {options.map((o) => (
        <Link
          key={o.key}
          href={link(o.key)}
          className="chip"
          aria-current={current === o.key ? "true" : undefined}
        >
          {o.label} <span className="count">{counts.get(o.key) ?? 0}</span>
        </Link>
      ))}
    </div>
  );
}

function CaseCard({ item }: { item: ShowcaseItem }) {
  const outcome = outcomeOf(item);
  const pattern = patternLabel(item.alert.detector);
  return (
    <li>
      <Link href={`/watch/${encodeURIComponent(item.run_id)}`} className="case-card">
        <span className="card-flow">
          <FlowDiagram
            graph={item.graph}
            variant="card"
            label={`Money flow of alert ${item.alert.alert_id}: ${item.graph.nodes.length} accounts`}
          />
        </span>
        <span className="card-body">
          <span className="card-pattern condensed">{pattern}</span>
          <span className="card-detector">
            {item.alert.detector_label ?? "A monitoring rule raised this alert."}
          </span>
          <span className={`says word-${outcomeMark(outcome)} card-outcome`}>
            <span className={`mark ${outcomeMark(outcome)}`} aria-hidden="true" />
            {outcomeLabel(outcome)}
          </span>
          <span className="card-teaser">{item.teaser}</span>
          <span className="card-meta meta">
            {item.alert.alert_id} · {modelLabel(item.model)}
          </span>
        </span>
      </Link>
    </li>
  );
}

export default async function WatchGallery({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const pattern = toPattern(one(params.pattern));
  const outcome = toOutcome(one(params.outcome));

  let items: ShowcaseItem[] = [];
  let error: string | null = null;
  try {
    items = (await getShowcase()).items;
  } catch (err) {
    error = loadErrorText(err, "recorded cases");
  }

  const byOutcome = items.filter((i) => outcome === null || outcomeOf(i) === outcome);
  const byPattern = items.filter((i) => pattern === null || i.alert.detector === pattern);
  const patternCounts = new Map<string, number>();
  for (const i of byOutcome) {
    const key = i.alert.detector ?? "";
    patternCounts.set(key, (patternCounts.get(key) ?? 0) + 1);
  }
  const outcomeCounts = new Map<string, number>();
  for (const i of byPattern) {
    const key = outcomeOf(i);
    outcomeCounts.set(key, (outcomeCounts.get(key) ?? 0) + 1);
  }
  const shown = byOutcome.filter((i) => pattern === null || i.alert.detector === pattern);

  return (
    <>
      <p className="eyebrow">Watch an investigation</p>
      <h1 className="display">Pick a case</h1>
      <p className="lede">
        Each card is a real alert from a monitoring rule, drawn as its money flow: dots are bank
        accounts, arrows are transfers. Open one to watch, step by step, how the agent
        investigated it and what the checks and the reviewer made of its answer.
      </p>

      {error && <p className="notice error">{error}</p>}

      {!error && (
        <div className="filters">
          <Filter
            label="Pattern"
            options={PATTERNS.map((p) => ({ key: p.key, label: p.label }))}
            current={pattern}
            link={(key) => href(key, outcome)}
            counts={patternCounts}
          />
          <Filter
            label="Outcome"
            options={OUTCOMES}
            current={outcome}
            link={(key) => href(pattern, key as OutcomeKey | null)}
            counts={outcomeCounts}
          />
        </div>
      )}

      {!error && items.length === 0 && (
        <p className="notice">
          No recorded cases with stored steps yet. Record an eval run, or backfill the steps of
          earlier runs with python -m evals.backfill_traces.
        </p>
      )}

      {items.length > 0 && shown.length === 0 && (
        <p className="notice">
          No cases match these filters. <Link href="/watch">Show all cases</Link>.
        </p>
      )}

      {shown.length > 0 && (
        <>
          <p className="meta shown">
            {shown.length} case{shown.length === 1 ? "" : "s"}, newest first
          </p>
          <ul className="case-gallery">
            {shown.map((item) => (
              <CaseCard key={item.run_id} item={item} />
            ))}
          </ul>
        </>
      )}
    </>
  );
}
