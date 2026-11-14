# CLAUDE.md: Bank Client Lifecycle Agent Harness

Short rules file, loaded every session. The full brief is in docs/brief.md. Read the section you need from it, not the whole file. When a decision changes, update docs/brief.md and add a note to docs/decisions/.

## Purpose

A personal portfolio project with three goals:
1. Learn harness engineering by building an agent harness from scratch (loop, tools, verifiers, guardrails, memory, evals, observability), with no agent framework.
2. Build finance domain knowledge (AML, KYB, credit) by automating bank analyst work.
3. Ship a hosted, demoable system with measurable results (accuracy, false-positive reduction, calibration, cost per case).

The owner knows Python, FastAPI, Next.js, TypeScript, Postgres, Docker, GitHub Actions, RAG and LLM agents. They are new to finance and say they are a vibe coder: explain things simply. When a finance term comes up, explain it briefly and add it to docs/glossary.md.

## Build order

Three modules share one harness, one case UI and one eval runner. Build strictly in order, each complete and evaluated before the next:
1. Monitor (AML alert investigation), 2. Onboard (KYB), 3. Lend (credit memo).
A bank reconciliation agent is parked. Do not start it.
Milestones M0 to M6 are listed in docs/brief.md section 13. Current step: M0 setup (hosting and secrets left, see docs/session-log.md).

## Hard rules

- Agent is read-only. It never writes to the database, posts decisions or changes data. Writes happen only through the human approval flow, outside the agent, and are written to the append-only audit log.
- Own agent loop. No LangChain, LangGraph, LlamaIndex or the Claude Agent SDK.
- Tools are read-only MCP servers written with the MCP Python SDK.
- Every agent output is a Pydantic model. No free-text-only outputs.
- Deterministic verifiers (schema, citations, arithmetic, module checks) run on every output before any LLM judge. One retry on failure, then human review.
- A hit limit (steps, tokens, time) routes the case to human review. Never auto-close.
- Four-eyes: escalations, KYB risk ratings and credit recommendations need explicit human approval.
- Model IDs live in config, never hard-coded. Prompts are versioned files, never inline strings.
- Every change to prompts, tools, models or harness is evaluated against a fixed versioned dataset.

## Security

- Personal accounts and billing only. No client code, client data or work accounts.
- All data is synthetic or public. Chinese model APIs only receive synthetic or public data.
- Secrets only in Codespaces secrets and Render or Vercel env vars. Never commit secrets, keys or data files. The repo is public. One exception: small synthetic slices under 10 MB listed in data/README.md (see docs/decisions/0003).
- Development happens only in GitHub Codespaces. Heavy data work happens in Kaggle Notebooks.

## Conventions

- Never use em dashes anywhere: docs, comments, UI text, commit messages.
- Python 3.12, uv, type hints everywhere. Pydantic, FastAPI, SQLAlchemy 2.0 with Alembic, pytest, ruff, mypy.
- Next.js App Router with strict TypeScript.
- Small, focused commits with clear messages. Build in thin vertical slices: one end-to-end path before widening.
- Record significant decisions as short notes in docs/decisions/ (what, why, alternatives).
- Update docs/glossary.md whenever a new finance term appears.

## Project subagents (.claude/agents/)

- builder: implements one task at a time, runs relevant tests, replies in 10 lines or fewer.
- tester: runs pytest, ruff, mypy and TypeScript checks, returns only failures or PASS. Never edits.
- reviewer: checks the latest changes against these rules, returns PASS or FAIL with up to 5 issues. Never edits.
Typical loop: builder, then tester, then reviewer, then commit.

## Pointers

- docs/session-log.md: current state and next steps. Read first in a new chat, update at session end
- docs/brief.md: full brief (scope, architecture, harness design, evals, data sources, stack, hosting, milestones, decisions, open questions)
- docs/glossary.md: finance terms
- docs/decisions/: decision notes
- Planned layout: apps/api, apps/web, harness, tools, modules/{aml,kyb,credit}, evals, data, docs
