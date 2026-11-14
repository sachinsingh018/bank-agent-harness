# Session log and handoff

Start here in a new chat. This is a summary, not a raw transcript. The repo is public, so full chat logs stay out of git. Full transcripts live in the `claude-home` Docker volume (see decisions/0002) and in `.claude-home-backup/` (gitignored).

Update this file at the end of each working session. Do not track setup status here: hosting, database and secrets are managed outside the repo.

## How we work
- Loop: builder, then tester, then reviewer, then commit. Ask before pushing unless told to push.
- No em dashes or en dashes anywhere.
- Never commit secrets. The repo is public.

## Things learned
- A rebuild wiped `~/.claude` once. History now persists through the `claude-home` volume. A Codespace delete would still lose it.
- Auto mode blocked deleting git hooks. Installing git-lfs fixed the push instead.
- `mypy apps` only covers apps. Add `harness`, `tools`, `modules`, `evals` to the CI mypy command when they get Python files (mypy errors on empty directories).
- CI actions (checkout@v4, setup-uv@v6, setup-node@v4) warn about Node 20 deprecation. Bump to newer majors in a small commit.
- `ubuntu-latest` is moving to Ubuntu 26. Pin `ubuntu-24.04` if CI should not change under us.
- TypeScript is held at 5.9.3 because eslint-config-next rejects TS 7. Revisit later.
- The pre-commit large-file hook (500 KB) exempts only `data/aml_slice_hi_small.csv` (decisions/0003).

## Next piece of work
Load `data/aml_slice_hi_small.csv` into Postgres (M1): tables for accounts and transactions, an Alembic migration, and a loader script that is safe to rerun. Then the AML rules layer and the rules-only baseline metrics. See docs/brief.md section 13 and data/README.md.
