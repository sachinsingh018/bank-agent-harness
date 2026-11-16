# Session log and handoff

Start here in a new chat. This is a summary, not a raw transcript. The repo is public, so full chat logs stay out of git. Full transcripts live in the `claude-home` Docker volume (see decisions/0002) and in `.claude-home-backup/` (gitignored).

Update this file at the end of each working session. Do not track setup status here: hosting, database and secrets are managed outside the repo.

## Current state (2026-11-16)
- M2 steps 1 to 6 and 8 are built and pushed. Step 7 has started: the first real run is done (below). M2 is done once a real scored run on the dev set exists.
- `OPENROUTER_API_KEY` is set as a Codespaces secret. It is on OpenRouter's free tier: 50 free-model requests per day (a case needs about 6 to 13 model calls), and free models are slow (about 25 s per call).
- `openrouter-free-dev` now uses `nvidia/nemotron-3-ultra-550b-a55b:free`.
- First real run `evals/runs/20261116T014628Z-c9d334` (3 smoke v2 alerts, local database, recorded, chain ok): 1 proposed (a real laundering alert, correctly escalated, but typology wrong), 1 human_review `limit_output` (the model used all 8000 output tokens before answering), 1 human_review `verifier_failed` (wrongly escalated a non-laundering alert; its stated sums were wrong, so the arithmetic verifier caught it). Both submissions first failed the schema (an empty `txn_ids` list on a claim; 21 amounts where the cap is 20) and used the retry.
- Proposed fixes, waiting for the owner: prompt v2 that states the schema limits and asks for fewer, cheaper tool calls; a read-only `sum_transfers` tool so the model never adds decimals itself; a higher output token cap for this profile. Every change is compared against v1 on the same fixed alerts.
- Full transcript of the 2026-11-15/16 build session: `.claude-home-backup/transcripts/2026-11-16-m2-build.jsonl` (gitignored, local only).
- Working style: one builder subagent per step, then tests plus an independent reviewer subagent, fix findings, commit. Ask the owner before every push.

## Where the work stands
- M1 (Data) is done. The AML slice is loaded into Neon and verified (25,493 transfers, 522 laundering, 64 schemes).
- The AML rules layer is done and verified on Neon: five detectors (fan-out, fan-in, cycle, scatter-gather, gather-scatter), 171 v1 alerts stored, and the v1 baseline in `evals/baselines/aml_rules_v1.json`. The Neon run produced a byte-identical baseline to the local one.
- M2 first piece is done: the read-only AML MCP tools server (`tools/aml/`). Nine tools (eight database tools plus `describe_detector`), the runtime SQL guard, label guard tests, caps, keyset paging, a schema snapshot and an end-to-end stdio test. Built in the three commits the plan laid out, pushed, CI green. Not yet smoke tested against Neon.
- Plans: `docs/plans/load-aml-slice.md`, `docs/plans/aml-rules-layer.md`, `docs/plans/mcp-tools.md`.
- Stack in place: alerts and labels live in separate tables. Only `evals/aml_rules_score.py` reads labels, and guard tests fail if other code does.

## How we work
- Loop: builder, then tester, then reviewer, then commit. Ask before pushing unless told to push.
- No em dashes or en dashes anywhere.
- Never commit secrets. The repo is public.

## Things learned
- `DATABASE_URL` in this Codespace comes from the Codespaces secret and points at Neon, overriding the devcontainer default. Always set `DATABASE_URL` explicitly to the local database (devcontainer URL, database `harness`) for development commands; on 2026-11-15 a few read-only checks hit Neon by mistake because of this. The local `harness` database must be migrated, loaded (`python -m data.load_aml_slice load`) and given v1 alerts (`python -m modules.aml.run_rules --ruleset v1`) in a fresh Codespace. For the database tests create `harness_test`, run `alembic upgrade head` and `python -m data.load_aml_slice load` against it with `DATABASE_URL` set to it, then run pytest with `TEST_DATABASE_URL` set to the devcontainer `DATABASE_URL` with the database name changed to `harness_test`. Without it 57 tests skip.
- Late date leak in the slice: after 2022-09-10 most transfers are laundering (decisions/0006). Agent metrics are split by it.
- The pre-commit ruff format hook also reformats Python code blocks inside markdown files.
- A rebuild wiped `~/.claude` once. History was meant to persist through the `claude-home` volume, but on 2026-11-15 the previous chat history was gone again after the Codespace stopped. Rely on this log, not chat history.
- Auto mode blocked deleting git hooks. Installing git-lfs fixed the push instead.
- CI mypy covers `apps harness migrations data modules evals`. Add `tools` when it gets Python files (mypy errors on empty directories).
- A pre-commit hook that edits a file (whitespace, end of file) stops the commit. Re-stage and commit again.
- Database tests run only against a local Postgres through `TEST_DATABASE_URL` and skip otherwise. They never read `DATABASE_URL`. CI starts a throwaway Postgres for them.
- Anything run against Neon goes through a redaction filter first, and loader and rules scripts print only "local database" or "remote database".
- Reviewer passes caught real bugs (scatter-gather counted payments before receipt, misleading per-family numbers). Keep the reviewer step.
- CI actions are on checkout v7, setup-node v7 and setup-uv v10.3.3 (Node 24). setup-uv publishes no floating major tag, so it is pinned to an exact version and needs a manual bump.
- `ubuntu-latest` is moving to Ubuntu 26. Pin `ubuntu-24.04` if CI should not change under us.
- TypeScript is held at 5.9.3 because eslint-config-next rejects TS 7. Revisit later.
- The pre-commit large-file hook (500 KB) exempts only `data/aml_slice_hi_small.csv` (decisions/0003).

## Next piece of work
M2 core, following `docs/plans/agent-loop.md` (verified by a reviewer pass, owner said build after verification). Build order is in its section 13:
1. Done: neutral model types, model profiles with exact cost, harness limits, scripted adapter.
2. Done: Anthropic and OpenRouter adapters (`anthropic==2.3.0`, `openai==3.42.0`).
3. Done: MCP client, ModuleSpec, prompt loader, AmlFindings model, investigator prompt v1.
4. Done: agent loop (limits, single shared retry, stop reason table, run trace) and a minimal case runner.
5. Done: verifier framework and AML citation, arithmetic and alert membership verifiers.
6. Done: label guard changes, datasets (dev 98 alerts with 16 strict positives, heldout 73 with 23), smoke v2 (10 alerts, 2 early and 1 late strict positive), eval runner, scorer, CI fast eval with the scripted adapter, decision note 0005. Runner and scorer refuse a non-local database unless `--allow-remote`.
8. Done (built before 7 because it needs no key): `case_events` append-only audit log with a hash chain and run digests, append-only `agent_runs` and `model_calls`, `eval_runs` and `eval_results`, opt in recording with `--record` and `HARNESS_WRITE_DATABASE_URL`, `python -m harness.records verify`, decision note 0007. Follow-ups also done: verifiers bounded by the wall clock, verifier crashes end as `verifier_error` without spending the retry, close on a missing alert fails.
7. Next: first real scored run. Waiting on the owner to add an API key. Steps:
   - Sign up at OpenRouter (free models need no payment) and create an API key; or use an Anthropic Console key.
   - Add it as a Codespaces secret for this repo (GitHub, Settings, Codespaces, Secrets): `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY`. Restart the Codespace so it loads.
   - Pick a currently free model on OpenRouter and set it in the `openrouter-free-dev` profile in `config/models.toml` (the placeholder may no longer be free).
   - Run on the local database: `DATABASE_URL=<local harness URL> uv run python -m evals.run_aml_agent --dataset evals/datasets/aml_alerts_smoke_v2.json --profile openrouter-free-dev --record`, then score with `evals.aml_agent_score --run <run_dir> --record`. Read 3 traces by hand. Then the dev set.
Later hardening: a database side statement timeout for the tools and verifier connections (a hung query can hold a connection); separate database roles (read-only tools, insert-only writer) and an external anchor for the latest audit hash; confirm the US or EU host and prices for the `openrouter-eu-us-open` profile before benchmarks.
Neon smoke test is not needed for this work: the local database, once loaded, has the same data. Do it before the hosted demo.
Optional later: ruleset v2 that tunes the noisy fan-out and gather-scatter detectors using the development schemes only.

## Future hardening (deferred)
- Give the MCP tools their own read-only Postgres role that can select only from transactions, accounts, alerts and alert_transactions. It would make the labels guard hold at the database level too. It needs a new connection string, so it is deferred. Until then the guard is code, a runtime SQL check and tests (docs/plans/mcp-tools.md).
