# 0007: Audit log and run records

**What:** Five tables, named as in docs/brief.md section 9, created by migration 0003:
- `case_events`: the append-only audit log. Every agent outcome adds one row (`proposal_created` or `human_review_created`, actor `agent-runner`). Later the approval UI adds `approved`, `rejected` and `edited` rows with actor `human:<name>`.
- `agent_runs` (one row per case run) and `model_calls` (tokens, cost, latency per model call).
- `eval_runs` (one row per eval run, from the runner manifest) and `eval_results` (the scorer output: counts and rates only, never per alert truth).

**How the audit log is protected:**
- A Postgres trigger refuses UPDATE and DELETE on `case_events`, and a statement trigger refuses TRUNCATE.
- A hash chain: each row stores the hash of the row before it (`prev_hash`) and its own hash, the sha256 of a fixed JSON form of (prev_hash, at, actor, event, case_id, run_id, payload). Editing any old row breaks the chain from that row on. `uv run python -m harness.records verify` recomputes it and CI runs it after the fast eval. `prev_hash` is unique and only one row may have no `prev_hash`, so the chain cannot fork.
- Appends take a transaction level advisory lock, so two writers never read the same last hash.
- An agent run, its model calls and its audit event are written in one transaction.
- `agent_runs` and `model_calls` are append-only too (same triggers): a run is written once and never updated. Each audit event carries `run_digest`, the sha256 of the full `agent_runs` row and its `model_calls` rows (in id order, ids left out). `verify` recomputes these digests from the current rows by default (`--skip-run-digests` to skip), so an edited run record shows up even though the run tables sit outside the chain.
- `eval_runs` and `eval_results` stay mutable on purpose: the runner updates `eval_runs` once when the run finishes, and scoring a run again replaces its `eval_results` row. They are summaries that can be rebuilt from the run files, not audit evidence.
- The migration 0003 downgrade refuses to drop a non-empty `case_events`. `alembic -x allow_audit_drop=1 downgrade 0002` is a dev only escape for local databases.

**Write path:**
- Only `harness/records.py` (plus `harness/db/`) writes, through its own env var `HARNESS_WRITE_DATABASE_URL`, never `DATABASE_URL`. That URL is never passed to the tools server, which gets only `DATABASE_URL` and `PYTHONPATH`. It gets the same local-only check as `DATABASE_URL` (`--allow-remote` to override).
- Recording is opt in: `--record` on `evals/run_aml_agent.py` and `evals/aml_agent_score.py`. Without it nothing is written.
- `harness/runner.py` imports no database code. It takes an optional `recorder` callable that the eval runner builds. A recording failure never changes the case outcome: the same CaseResult is returned and a generic line (exception class only) goes to stderr. The eval runner counts failures and exits 1 if any case was not recorded. A static test checks that only the eval scripts import `harness.records`.

**Known limits (plainly):**
- Locally and in CI, `DATABASE_URL` and `HARNESS_WRITE_DATABASE_URL` use the same superuser, and it owns the tables. A determined admin can disable the triggers, edit rows and recompute the whole chain and every run digest, and `verify` would still pass. Today the triggers and the chain catch mistakes and casual edits, not a malicious owner.
- Removing rows from the end of the chain cannot be detected without an external anchor (a copy of the latest hash kept somewhere the database admin cannot change).

**Deferred (future hardening):**
- Separate database roles: a read-only role for the tools server, an insert-only role (no UPDATE, DELETE, TRUNCATE, no table ownership) for the writer, and migrations run by a third owner role.
- An external anchor of the latest hash, for example committed to the repo or published on a schedule, so a rehashed chain or a cut tail no longer matches.
- Approval rows (`approved`, `rejected`, `edited`) are written by the approval UI in M3.

**Alternatives:**
- Trigger only: a superuser can disable it without a trace. Hash chain only: nothing stops the edit in the first place. Both together are cheap.
- Writing from the agent loop: breaks the read-only rule. Writing always (not opt in): every local test and experiment would fill the audit log, which cannot be cleaned.
- One URL for reads and writes: the tools server would see a connection that can write.
