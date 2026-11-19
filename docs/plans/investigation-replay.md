# Plan: "Watch an investigation" (portfolio showpiece, phase 1)

Status: drafted 2026-11-19. Owner priority: presentation and visibility over accuracy; this is a portfolio project. No login. Phase 1 replays recorded runs only, so it costs nothing and cannot be abused. Phase 2 (live runs on visitor data with hard limits) is later and optional.

## Goal

A visitor picks a case and watches, step by step, how the harness investigated it: the alert, every tool call and what came back, the evidence building up, the deterministic checks, the reviewer, and the outcome, each explained in plain words. It must look polished and be obvious to use without reading anything first.

## 1. Data: store each run's steps

- New append-only table `run_traces` (migration 0006): run_id (FK agent_runs, unique), trace JSONB (the RunTrace: model calls, tool calls, messages with tool results, retry info, lesson ids, review trace), created_at. Same refuse UPDATE, DELETE and TRUNCATE trigger as the other record tables. A separate table so existing run digests are unchanged; the case event payload gets `trace_digest` when present and `harness.records verify` checks it.
- `record_case_result` stores the trace; `evals/record_run.py` copies traces from results.jsonl, and a small backfill command records traces for runs already recorded (local and, with owner approval, Neon).
- Size guard: tool result strings are capped (for example 12,000 characters each) when stored.

## 2. API

- `GET /runs/{run_id}/steps`: the run as an ordered list of display steps, built server side from the stored trace: `alert` (the alert summary), `thinking` (the model's visible text between calls, if any), `tool_call` (tool name, plain description of what it asked, arguments), `tool_result` (a short plain summary plus the raw JSON, capped), `retry` (what failed and the correction), `checks` (each verifier, passed or failed with reasons), `review` (verdict, reasons), `outcome` (proposed or human review, recommendation, confidence). Each step has a plain one sentence explanation written by deterministic code (for example "Asked who this account pays and who pays it"), not by a model.
- `GET /showcase`: a curated list of cases to watch: every recorded run that has a stored trace, with alert id, detector, pattern, outcome, and a short teaser. Newest first.

## 3. Web

### Pick a case (`/watch`)
- A gallery of cases. Each card shows the alert's money flow as a small diagram (accounts as nodes, transfers as arrows, sized by count), the detector in plain words, and the outcome. Filters by pattern (fan-out, fan-in, cycle, scatter-gather, gather-scatter) and outcome.

### Watch it (`/watch/[runId]`)
- Left: a timeline of steps with play, pause, next, previous and a speed control; keyboard arrows step through. Each step reveals with one short, purposeful animation (respecting reduced motion).
- Right: the evidence board. The money-flow diagram of the alert grows as tools return data (new accounts and transfers appear), cited transfers highlight when a claim uses them, and the verdict meter from the case page fills at the end.
- Each step shows the plain explanation first and lets the visitor open the raw tool call and result.
- End screen: the final findings, the checks, the reviewer verdict, and links to the full case page and the How it works page.

### Landing
- The home page becomes a short, striking introduction: one sentence on what this is, a "Watch an investigation" button, and a live looking preview (a few steps auto-playing from a featured run).

## 4. Design

Keep the design tokens from docs/plans/ui-redesign.md (banknote green, amber, brick, Archivo with condensed headings, rules not shadows). The one bold element on this page is the evidence board growing as the investigation proceeds. The money-flow diagram is inline SVG, drawn with a simple deterministic layout (no new dependencies).

## 5. Tests

API: steps endpoint builds the right sequence for a scripted run with a retry and a review; showcase lists only runs with traces; trace table append-only; trace digest verified. Web: typecheck, lint, build; the replay works without JavaScript errors and is keyboard operable.

## 6. Not in phase 1

Live runs, uploads, login.
