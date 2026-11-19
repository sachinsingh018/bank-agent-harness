# 0009: Store run traces in their own append-only table

**What:** Migration 0006 adds `run_traces`: one row per agent run with the maker's trace and the checker's (messages, model calls, tool calls, tool results capped at 12,000 characters, retries, seen transfer ids, lesson ids), plus a small `case_card` (the alert as the agent first read it). `GET /runs/{id}/steps` turns a trace into replay steps, and `GET /showcase` lists traced runs for the gallery. `python -m evals.backfill_traces` fills traces for runs recorded before 0006 from the local `evals/runs/*/results.jsonl`, with no model calls and no new case events.

**Why:**
- The replay ("watch the agent investigate") needs the steps of a run, not only its final findings.
- A separate table, not a new `agent_runs` column, because every recorded run's `run_digest` covers all `agent_runs` columns. A new column would break the digests of runs already in the audit log.
- Append-only like the other run records (the `records_refuse_change` triggers from 0003). A run recorded with its trace carries a `trace_digest` in its case event.
- The system prompt is not stored (only its version and sha256), and the tools refuse the label table, so the answer key cannot enter a trace.

**Alternatives:**
- Read traces from `evals/runs/*/results.jsonl` at request time: those files are gitignored and absent on a hosted API.
- Store full uncapped tool results: larger rows for little display value.

**Status:** applied locally, with the backfill. Not applied on Neon: since decision 0008 the public site reads a static bundle, so Neon does not need 0006 for visitors.
