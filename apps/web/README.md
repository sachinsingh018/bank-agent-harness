# Case UI (Next.js)

The approval queue: a list of cases the agent has worked on, and a case page where a person
approves, approves with edits or rejects the agent's proposal. With "Approve with edits" the
reviewer can change the recommendation, confidence, typology and narrative; the API runs the
same deterministic checks as for the agent and refuses (listing the problems) if they fail. Any
decision can also save a lesson ("correction") for future cases; lessons are listed on
`/corrections` and are not read by the agent yet. The web app talks to the API from the server side
only, so the reviewer token never reaches the browser.

## Run locally

Use the local database only (host `db` in the Codespace), never the remote one.

1. Start the API from the repo root:

```bash
DATABASE_URL=postgresql+psycopg://postgres:postgres@db:5432/harness \
HARNESS_WRITE_DATABASE_URL=postgresql+psycopg://postgres:postgres@db:5432/harness \
REVIEWER_TOKEN=local-dev-token \
REVIEWER_NAME=owner \
uv run uvicorn apps.api.main:app --port 8000
```

Without `REVIEWER_TOKEN` and `REVIEWER_NAME` the API still shows the queue, but refuses
decisions ("decisions are disabled on this server"). Never set `REVIEWER_TOKEN` on a public API
deployment: until a real reviewer login exists, anyone who can reach the API with the token
could write a decision.

2. Start the web app from `apps/web`:

```bash
npm ci
API_BASE_URL=http://localhost:8000 REVIEWER_TOKEN=local-dev-token WEB_DECISIONS_ENABLED=1 npm run dev
```

Without `WEB_DECISIONS_ENABLED=1` the case page shows "Decisions are disabled in this web
deployment" and the decision action refuses before calling the API.

For a production build use `npm run build` and then `npm run start` with the same variables.

3. Open http://localhost:3000/queue (lessons at http://localhost:3000/corrections).

The API needs migration 0004 (the corrections table) applied to its records database.

## Environment

| Variable | Where | What |
|---|---|---|
| `API_BASE_URL` | web server | Base URL of the API (default `http://localhost:8000`) |
| `REVIEWER_TOKEN` | web server and API | Shared secret sent as `X-Reviewer-Token` by the decision form's server action. Local only: never set it on a public web or API deployment |
| `WEB_DECISIONS_ENABLED` | web server | `1` turns the decision form on. Local only until a real reviewer login exists. Never set it, or REVIEWER_TOKEN, on a public deployment. |
| `REVIEWER_NAME` | API | Name written to the audit log as `human:<name>` |
| `API_ALLOW_REMOTE_DATABASE` | API | Set to `1` only on a deployed API whose databases are not local |

## Checks

```bash
npm run typecheck && npm run lint && npm run build
```
