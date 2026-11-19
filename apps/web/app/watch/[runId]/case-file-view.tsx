// The case file tab of the replay: the queue page's read-only sections, rendered on the
// server and handed to the replay as a ReactNode. No decision form here.

import type { RunDetail } from "../../../lib/api";
import { readFindings } from "../../../lib/format";
import {
  AuditSection,
  ChecksSection,
  ClaimItem,
  LessonsSection,
  ReviewerSection,
  RunSection,
  Verdict,
} from "../../_ui/case-file";

export function CaseFileView({ run }: { run: RunDetail }) {
  const findings = readFindings(run.findings);
  const byId = new Map(run.cited_transfers.map((t) => [t.txn_id, t]));
  const missing = new Set(run.missing_txn_ids);
  return (
    <div className="case-grid">
      <div className="case-main">
        <Verdict run={run} />
        {findings ? (
          <>
            <h2>Narrative</h2>
            <p className="prose">{findings.narrative}</p>
            <h2>Claims</h2>
            <p className="meta">Each claim cites real transfers. Open a transfer to see its details.</p>
            {findings.claims.length === 0 ? (
              <p className="muted">The agent made no claims.</p>
            ) : (
              <ol className="claims">
                {findings.claims.map((claim, i) => (
                  <ClaimItem key={i} claim={claim} byId={byId} missing={missing} />
                ))}
              </ol>
            )}
          </>
        ) : (
          <p className="muted">No findings were stored for this case.</p>
        )}
      </div>
      <aside className="rail" aria-label="Checks, review and run">
        <ChecksSection run={run} />
        {run.review && <ReviewerSection run={run} />}
        <RunSection run={run} />
        <LessonsSection run={run} />
        <AuditSection run={run} />
      </aside>
    </div>
  );
}
