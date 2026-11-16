# Plan: approval queue (M3 first slice)

Status: drafted 2026-11-16. The owner asked to build this while M2 evaluation waits for model requests. M2 code is complete; its full evaluation is still open (docs/session-log.md).

## Goal

A human can see what the agent proposed, read the evidence, and approve or reject it. Every decision is written to the append-only audit log (`case_events`). This is the four-eyes step from CLAUDE.md: the agent proposes, a person decides.

## Plain-English background

- **Approval queue.** The list of cases waiting for a human decision.
- **Four-eyes.** Two parties must agree before a risky action. Here the agent is the first pair of eyes and you are the second.

## 1. What counts as waiting

A case run (`agent_runs` row) is waiting when its audit trail has `proposal_created` or `human_review_created` and no later `approved` or `rejected` event for the same run. Both kinds wait: a proposal needs approval, a human review case needs a person to decide from scratch.

## 2. API (FastAPI, `apps/api/`)

| Endpoint | What it does |
|---|---|
| `GET /queue?state=waiting|decided|all&limit&cursor` | List runs with alert id, status, reason, recommendation, confidence, typology, created time, decision if any. Newest first, keyset paging. |
| `GET /runs/{run_id}` | Full detail: findings (narrative, claims, figures), verifier results, tool call summary from model_calls and the trace, the cited transfers (read through the existing guarded read-only door, `get_transaction`), and the audit events for the run. |
| `POST /runs/{run_id}/decision` | Body: `decision` (`approve` or `reject`), `note` (required for reject, max 2,000 characters). Appends one `approved` or `rejected` event with actor `human:<reviewer>` through `harness.records.append_case_event`. A second decision on the same run returns 409. |

Rules:
- Reads of runs and events use the records connection (`HARNESS_WRITE_DATABASE_URL`); reads of transfers use the guarded tool door (`DATABASE_URL`). The API never reads the labels table (existing guard scans `apps/`).
- **Writes are off unless configured.** The decision endpoint needs `REVIEWER_TOKEN` and `REVIEWER_NAME` set on the server and a matching `X-Reviewer-Token` header. Without them it returns 403. The repo is public and a deployed API must not accept decisions from anyone.
- Responses are Pydantic models. Errors never echo database details.
- The agent is untouched. It still cannot write; only this human flow writes decisions.

## 3. Web (Next.js App Router, `apps/web/`)

- `/queue`: a table of waiting cases (tabs for waiting and decided), with recommendation, confidence, typology or human review reason.
- `/queue/[runId]`: the case page. Narrative, claims with their cited transfers (amounts, currencies, sides, accounts), figures, verifier results, tool calls, audit trail, and an approve or reject form with a note.
- The page talks to the API from the server side only (`API_BASE_URL`). The decision form uses a server action that adds `X-Reviewer-Token` on the server, so the token never reaches the browser.
- Plain CSS, no UI library. Strict TypeScript.

## 4. Tests

- API: pytest with the local test database (`TEST_DATABASE_URL`), runs created through the scripted runner and `record_case_result`: queue states, detail shape, decision writes one event and the chain still verifies, second decision 409, missing or wrong token 403, reject without note 422, unknown run 404, no label words in responses.
- Web: typecheck, lint and build in CI (existing web job). A small fetch layer with typed responses.

## 5. Build order

1. API endpoints and tests.
2. Web pages.
3. Run both locally against the local database with a scripted eval run recorded, click through one approve and one reject, verify the audit chain.

Deferred: an `edited` decision (change findings before approving), real login, the maker-checker reviewer agent, corrections memory.
