# Session log and handoff

Start here in a new chat. This is a summary, not a raw transcript. The repo is public, so full chat logs stay out of git. Full transcripts live in the `claude-home` Docker volume (see decisions/0002) and in `.claude-home-backup/` (gitignored).

Update this file at the end of each working session. Do not track setup status here: hosting, database and secrets are managed outside the repo.

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
- Local database setup in a fresh Codespace: `DATABASE_URL` (devcontainer) points at the local `harness` database, already loaded. For the database tests create `harness_test`, run `alembic upgrade head` and `python -m data.load_aml_slice load` against it with `DATABASE_URL` set to it, then run pytest with `TEST_DATABASE_URL` set to the devcontainer `DATABASE_URL` with the database name changed to `harness_test`. Without it 57 tests skip.
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
1. Done: neutral model types, model profiles with exact cost, harness limits, scripted adapter (commit 82b3d7f).
2. In progress: Anthropic and OpenRouter adapters (`anthropic==2.3.0`, `openai==3.42.0`).
3. Then MCP client, ModuleSpec, prompt loader, AML output model and prompt v1; 4 the loop; 5 verifiers; 6 datasets, runner, scorer, guard changes, CI fast eval; 7 first real scored run (needs an API key and owner approval for spend); 8 audit and run tables.
Neon smoke test is not needed for this work (local database has the same data). Do it before the hosted demo.
Optional later: ruleset v2 that tunes the noisy fan-out and gather-scatter detectors using the development schemes only.

## Future hardening (deferred)
- Give the MCP tools their own read-only Postgres role that can select only from transactions, accounts, alerts and alert_transactions. It would make the labels guard hold at the database level too. It needs a new connection string, so it is deferred. Until then the guard is code, a runtime SQL check and tests (docs/plans/mcp-tools.md).
