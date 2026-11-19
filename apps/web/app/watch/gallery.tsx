"use client";

// The gallery list with its pattern and outcome filters. The filters live in the query string
// and are read on the client, so the /watch page itself can be static.

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import type { ShowcaseItem } from "../../lib/api";
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

function toPattern(value: string | null): string | null {
  return PATTERNS.some((p) => p.key === value) ? value : null;
}

function toOutcome(value: string | null): OutcomeKey | null {
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

/** The filters and cards for one pattern and outcome choice (null shows all). */
export function Gallery({
  items,
  pattern,
  outcome,
  recorded,
}: {
  items: ShowcaseItem[];
  pattern: string | null;
  outcome: OutcomeKey | null;
  /** True for the committed bundle, which is in curated order rather than newest first. */
  recorded: boolean;
}) {
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

      {items.length === 0 && (
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
            {recorded
              ? `${shown.length} recorded case${shown.length === 1 ? "" : "s"}`
              : `${shown.length} case${shown.length === 1 ? "" : "s"}, newest first`}
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

/** The gallery filtered by ?pattern= and ?outcome=. Render it inside a Suspense boundary. */
export function GalleryFromUrl({
  items,
  recorded,
}: {
  items: ShowcaseItem[];
  recorded: boolean;
}) {
  const params = useSearchParams();
  return (
    <Gallery
      items={items}
      recorded={recorded}
      pattern={toPattern(params.get("pattern"))}
      outcome={toOutcome(params.get("outcome"))}
    />
  );
}
