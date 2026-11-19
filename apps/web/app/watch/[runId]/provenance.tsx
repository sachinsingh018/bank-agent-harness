// The provenance strip above a recorded replay: where the run came from, what it cost and a
// link to its raw JSON. Bundle mode only; the live API has no provenance block. Server safe.

import type { Provenance, RunEffort } from "../../../lib/bundle-types";
import { formatUsd } from "../../../lib/format";
import { modelLabel } from "../../../lib/replay";
import styles from "./provenance.module.css";

function day(iso: string | null | undefined): string | null {
  return iso ? iso.slice(0, 10) : null;
}

/** "$0 (free model)" only when the stored cost is zero on a free model id. */
function costText(cost: string | undefined, model: string | null | undefined): string | null {
  if (cost === undefined) return null;
  const free = Number(cost) === 0 && (model ?? "").endsWith(":free");
  return `${formatUsd(cost)}${free ? " (free model)" : ""}`;
}

export function ProvenanceStrip({
  provenance,
  effort,
  rawHref,
}: {
  provenance: Provenance;
  effort: RunEffort | null;
  rawHref: string;
}) {
  const totals = effort?.totals ?? null;
  const recorded = day(provenance.recorded_at);
  const cost = costText(totals?.cost_usd, provenance.model);
  const tokens =
    totals && totals.tokens_in !== undefined && totals.tokens_out !== undefined
      ? (totals.tokens_in + totals.tokens_out).toLocaleString("en-US")
      : null;
  const latency =
    totals?.latency_ms !== undefined ? `${(totals.latency_ms / 1000).toFixed(1)} s` : null;
  return (
    <section className={styles.strip} aria-label="About this recording">
      <p className={styles.badge}>Recorded run, replayed step by step</p>
      <dl className={styles.facts}>
        {recorded && (
          <div>
            <dt>Recorded</dt>
            <dd>{recorded}</dd>
          </div>
        )}
        {provenance.model && (
          <div>
            <dt>Model</dt>
            <dd>
              {modelLabel({ provider: provenance.provider ?? "", model: provenance.model })}
            </dd>
          </div>
        )}
        {provenance.prompt_version && (
          <div>
            <dt>Prompt</dt>
            <dd>{provenance.prompt_version}</dd>
          </div>
        )}
        {provenance.ruleset_version && (
          <div>
            <dt>Rules</dt>
            <dd>{provenance.ruleset_version}</dd>
          </div>
        )}
        {cost && (
          <div>
            <dt>Cost</dt>
            <dd>{cost}</dd>
          </div>
        )}
        {tokens && (
          <div>
            <dt>Tokens</dt>
            <dd>{tokens}</dd>
          </div>
        )}
        {latency && (
          <div>
            <dt>Model time</dt>
            <dd>{latency}</dd>
          </div>
        )}
      </dl>
      <p className={styles.line}>
        The answer key was hidden from the agent.{" "}
        <a href={rawHref} download className={styles.download}>
          Download raw run (JSON)
        </a>
      </p>
    </section>
  );
}
