# Plan: UI showpiece (replay-only public site)

Source prompt: `docs/notes/trythis.md`. Review behind it: `docs/notes/ui-architecture-check.md`. Branch `ui-showpiece`, safety tag `pre-ui-showpiece` on 97e2726.

## 1. Protected paths (not edited)
`harness/` (agent loop, model adapter, records, run_traces), `modules/aml/` (prompts, rules, detectors), `tools/`, `apps/api/`, `harness/db/` models, `migrations/`, `evals/run_aml_agent.py`, `evals/aml_agent_score.py`, `evals/aml_rules_score.py`, `evals/make_aml_datasets.py`, `evals/backfill_traces.py`, `evals/test_label_readers.py`, `data/load_aml_slice.py`, every existing test outside `apps/web`, `render.yaml`, `.devcontainer/`. If one of these looks necessary, I stop and ask.

Changed: `apps/web/**`, new `evals/export_showcase.py` and `evals/test_export_showcase*.py`, new `apps/web/content/showcase/v1/`, `.github/workflows/ci.yml` (web test steps only), docs.

## 2. Curated runs (all real, nemotron free, dev alerts, early window, traced, no decision yet)

| Order | Run | Alert, detector | Status now | Label | Story |
|---|---|---|---|---|---|
| 1 | f7eb0d02-e996-4921-9278-1762e0e5ed03 | AL-0679023ed0bc fan-out | proposed escalate 0.85 (v1) | Caught | prompt-versions |
| 2 | 823c8309-3f1d-465e-8b70-5ffb4fe19909 | same | proposed escalate 0.75 (v2) | Caught faster | prompt-versions |
| 3 | 29c00e8b-abe7-4fd9-80a1-1f920c492ab0 | same | proposed close 0.15 (v3) | Missed | prompt-versions |
| 4 | e75bcdb8-6779-4261-afed-3e70f759a2ff | same | human_review, reviewer_failed (v5 close 0.15, reviewer limit_time) | Sent to a person | prompt-versions |
| 5 | 029a419f-6e72-4ef8-85da-25a6fe1d5e8e | AL-833c795ec18e scatter-gather | proposed escalate 0.9 (v3) | Caught | other detectors |
| 6 | 20be587f-cbd0-4b2c-ad74-fbee69513bb5 | AL-986d0280c6ee fan-in | proposed escalate 0.75 (v3) | Caught | other detectors |
| 7 | 70353513-1255-4b6f-aba7-267c3402dd6e | AL-01cf1e794cc7 cycle | proposed close 0.15 (v3) | False alert closed | single |
| 8 | 61e56e79-9dd9-4115-8172-36e7775ddc24 | AL-3332102d5f16 gather-scatter | human_review, verifier_failed (v1) | Checks sent it to a person | single |

All 8 can take one decision in the owner review step (the endpoint accepts any status). Featured: **f7eb0d02** (shortest clean catch, 50 KB, starts the story). The ending caption for run 4 is written after your decision in S2.

## 3. Bundle format (`apps/web/content/showcase/v1/`)
- `manifest.json`: `format: 1`, `export_date`, `git_commit` (both passed in), `eval_run_id`, `ruleset_version`, `models`, `prompt_versions`, `featured`, `order`, `stories` (id, title, run ids), `cases` (run id, label, caption).
- `showcase.json`: `ShowcaseItem[]` for the 8 runs, from `list_showcase`.
- `runs/<runId>.json`: `{ steps: RunSteps, case: RunDetail, provenance, effort }`.
- `results.json`: from cd5c6c `score.json`: `early` (headline) and `all` (secondary), each with its own `baseline` and `agent_with_human_review.strict`, plus `human_review`, plus `rules_171` context from `evals/baselines/aml_rules_v1.json`, plus fixed notes (strict truth, person counts as escalated, dev only, no held-out run, free model, date leak).

**Allowlist.** Keep every field the TS types require. `RunSteps` and `ShowcaseItem`: all fields; step `raw` capped at 2,000 chars with `raw_truncated: true`. `RunDetail`: all fields except `trace_id` (dropped, optional in TS); `events[].payload` keeps only `event, status, reason, recommendation, confidence, typology, decision, note`; `model_calls` keep the existing `ModelCallOut` fields (no request ids exist there). Graph edges gain optional `transfers: {txn_id, ts, amount, currency}[]` from `transactions` (amount and currency are the paid side). `provenance`: recorded date, model, prompt version, ruleset version. `effort`: per tool_call step `{tokens_in, tokens_out, latency_ms}` only when model call count equals tool_call step count, else run totals; tool `duration_ms` from `tool_calls`.

Determinism: sorted keys, fixed separators, trailing newline, runs in manifest order, no clock reads.

## 4. Reuse of existing functions
`list_showcase(engine, limit=200)` filtered to the 8 ids; `get_run_steps(engine, run_id)`; `get_run(run_id, engine, tool_engine)` with `build_tool_engine(url)`. Engine: `create_engine(url)` after `is_local_url(url)`; every call inside a read-only connection (`SET TRANSACTION READ ONLY`). `HTTPException` becomes a clear CLI error. Refusals: scripted profile or provider, alert not in the dev list or in the held-out list, missing `results.jsonl`, run over 120 KB, bundle over 1.5 MB.

## 5. Routes per mode

| Route | bundle (default, public) | api (local `.env.local`) |
|---|---|---|
| `/` | static, featured mini-replay, early headline | as today plus new layout |
| `/watch` | static, client-side filters | as today |
| `/watch/[runId]` | prerendered for 8 ids, else `notFound()` | as today |
| `/results` | static from `results.json` | same (always bundle) |
| `/about` | static (force-dynamic removed) | same |
| `/queue`, `/queue/[runId]`, `/lessons`, `/corrections` | `notFound()` | unchanged |
| `/evals` | redirect to `/results` | unchanged |

Nav: Watch, Results, How it works; Queue, Evals and Lessons only in api mode.

## 6. Design direction
Keep the banknote engraving look: paper and ink, intaglio green for agent actions, amber `--signal` for alerts and money in motion, brick for misses and failures, steel for meta. New CSS Modules only. Additions as tokens in the module files: a guilloche hairline pattern for section breaks, monospace-free figures (Archivo tabular numbers), a 4-step motion scale (120, 240, 480, 960 ms) disabled under `prefers-reduced-motion`. The replay becomes a two-column ledger: steps on the left, money-flow plate on the right, case file as a tab.

## 7. Inspiration (general knowledge of public trace viewers; no code copied, nothing for docs/credits.md yet)
- Timeline of spans with nested tool calls (Langfuse, LangSmith, Arize Phoenix).
- Collapsed input and output with "show raw" per span.
- Token, latency and cost badges per span, run totals at the top.
- Score and verdict chips attached to the run.
- Click a span to highlight its evidence elsewhere (cross-linking).
- Playback scrubber for long runs (session replay tools).
- Keyboard first stepping (j and k, arrows).
- Deep links to one step (`?step=N`).
- A provenance strip with model and prompt version.
- Downloadable raw JSON of the run.

## 8. Test plan
- Python: `evals/test_export_showcase.py` with fixture rows in the test database and a temporary `results.jsonl`: allowlist (no `trace_id`, capped raw, required fields present), refusals (scripted, held-out, non-local URL, missing results file), determinism (two exports byte-identical).
- Web CI: `npx playwright install --with-deps chromium`, `npm run build`, static route check (each public route is ○ or ●), `next start` with `SHOWCASE_SOURCE=bundle` and `API_BASE_URL=http://127.0.0.1:9`, Playwright: landing, gallery, replay guided and explore, results, about, each with an axe check, and bundle-mode 404 for `/queue`.
- Local: screenshots at 390, 768, 1280; before and after screenshots of `/queue/[runId]` in api mode; Lighthouse only if Chrome is present.

## 9. Slices (one commit each, builder, tester, reviewer)
- **S1a** (medium): exporter, tests, first bundle (no decisions).
- **S1b** (large): data switch, static pages, route handling, case file extraction with no visual change, Playwright and axe in CI, static route check. Pushing becomes safe.
- **S2** (small, needs you): owner review setup, your decisions, re-export.
- **S3** (large): replay guided and explore, case file tab, technical details, provenance, download.
- **S4** (medium): graph reveal and money animation.
- **S5** (medium): landing, results, about with harness diagram.
- **S6** (medium): contact, Open Graph, favicon, mobile and a11y pass, docs (decision note, data/README.md, glossary, deploy.md section 4).

Parallel work to save time: S1a (Python) and the web-only parts of S1b (route handling, case file extraction, Playwright setup) touch different files and can run as two builder tasks at once; S1b's bundle reader joins them at the end. S3, S4 and S5 are separate files after S1b and can run in parallel the same way.
