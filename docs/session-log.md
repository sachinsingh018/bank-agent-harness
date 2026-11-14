# Session log and handoff

Start here in a new chat. This is a summary, not a raw transcript. The repo is public, so full chat logs stay out of git. Full transcripts live in the `claude-home` Docker volume (see decisions/0002) and in `.claude-home-backup/` (gitignored).

Update this file at the end of each working session. Do not track setup status here: hosting, database and secrets are managed outside the repo.

## Where the work stands
- M1 (Data) is done. The AML slice is loaded into Neon and verified (25,493 transfers, 522 laundering, 64 schemes).
- The AML rules layer is done and verified on Neon: five detectors (fan-out, fan-in, cycle, scatter-gather, gather-scatter), 171 v1 alerts stored, and the v1 baseline in `evals/baselines/aml_rules_v1.json`. The Neon run produced a byte-identical baseline to the local one.
- Plans: `docs/plans/load-aml-slice.md`, `docs/plans/aml-rules-layer.md`.
- Stack in place: alerts and labels live in separate tables. Only `evals/aml_rules_score.py` reads labels, and guard tests fail if other code does.

## How we work
- Loop: builder, then tester, then reviewer, then commit. Ask before pushing unless told to push.
- No em dashes or en dashes anywhere.
- Never commit secrets. The repo is public.

## Things learned
- A rebuild wiped `~/.claude` once. History now persists through the `claude-home` volume. A Codespace delete would still lose it.
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
M2, the agent harness. First piece: the read-only MCP tools the agent uses to investigate an alert (get_alert, get_account_history, get_counterparties and a few more). Tools read only transactions, accounts, alerts and alert_transactions, never the labels table. A plan goes in `docs/plans/mcp-tools.md` and needs owner approval before any code. Optional later: ruleset v2 that tunes the noisy fan-out and gather-scatter detectors using the development schemes only.
