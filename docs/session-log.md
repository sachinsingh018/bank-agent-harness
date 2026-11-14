# Session log and handoff

Start here in a new chat. This is a summary, not a raw transcript. The repo is public, so full chat logs stay out of git. Full transcripts live in the `claude-home` Docker volume (see decisions/0002) and in `.claude-home-backup/` (gitignored).

Update this file at the end of each working session.

## Current state (2026-11-14)

Milestone M0 (setup) is about 70 percent done. Next: hosting and secrets.

Done and pushed to GitHub (`main`):
- Rules in CLAUDE.md, full brief in docs/brief.md, architecture sketch, glossary, decision notes 0001 and 0002.
- Devcontainer: Python 3.12, uv, Node, local Postgres, pre-commit (gitleaks, ruff), git-lfs installed in post-create.
- Repo skeleton: apps, harness, tools, modules/{aml,kyb,credit}, evals, data.
- apps/api: FastAPI with `GET /health`, one pytest, mypy strict.
- apps/web: Next.js 16.4.6, React 19.3.1, strict TypeScript, versions pinned exactly. TypeScript held at 5.9.3 because eslint-config-next rejects TS 7. Revisit later.
- CI (.github/workflows/ci.yml): `api` job (ruff, mypy, pytest) and `web` job (typecheck, lint, build). Node pinned to 26. First run on GitHub was green.

Not done yet (M0):
- Neon Postgres with dev and prod branches.
- Codespaces secrets.
- Render deploy of the API (health check path `/health`).
- Vercel deploy of the web app (root directory `apps/web`).
- End-to-end check. Setup steps are in docs/setup-guide-prompt.md, to paste into regular Claude, which guides the dashboard work.

## How we work
- Loop: builder, then tester, then reviewer, then commit. Ask before pushing.
- Push only when the owner says so.
- No em dashes or en dashes anywhere.

## Things learned
- A rebuild wiped `~/.claude` once. History now persists through the `claude-home` volume. A Codespace delete would still lose it.
- Auto mode blocked deleting git hooks. Installing git-lfs fixed the push instead.
- `mypy apps` only covers apps. Add `harness`, `tools`, `modules`, `evals` to the CI mypy command when they get Python files (mypy errors on empty directories).
- CI actions (checkout@v4, setup-uv@v6, setup-node@v4) warn about Node 20 deprecation. Bump to newer majors in a small commit.
- `ubuntu-latest` is moving to Ubuntu 26. Pin `ubuntu-24.04` if CI should not change under us.

## Next steps
1. Do Part A of docs/setup-guide-prompt.md (Neon, secrets, Render, Vercel, checks).
2. Bring back any repo changes it asks for (for example render.yaml). Claude Code makes them.
3. Then M1: Kaggle slice of the IBM AML data, ingestion into Postgres, rules layer, baseline metrics.
