# Case UI (Next.js)

The approval queue: a list of cases the agent has worked on, and a case page where a person
approves, approves with edits or rejects the agent's proposal. With "Approve with edits" the
reviewer can change the recommendation, confidence, typology and narrative; the API runs the
same deterministic checks as for the agent and refuses (listing the problems) if they fail. Any
decision can also save a lesson for future cases; lessons are listed on `/lessons` (old
`/corrections` links redirect there) and are shown to the agent only on runs with lessons turned
on. `/evals` lists recorded eval runs (counts and rates only) with expandable score blocks, and
`/about` explains the flow in plain words. The web app talks to the API from the server side
only, so the reviewer token never reaches the browser.

## Bundle mode (the public site)

By default the showcase pages (`/`, `/watch`, `/watch/<run id>`, `/results`, `/about`) read the
recorded runs committed in `content/showcase/v1` and need no API: this is bundle mode, and it is
what the public site runs. `SHOWCASE_SOURCE=api` switches them to the live API for local work;
put it in `.env.local` (gitignored), for example:

```bash
# apps/web/.env.local
SHOWCASE_SOURCE=api
API_BASE_URL=http://localhost:8000
```

Leave `.env.local` out (or remove `SHOWCASE_SOURCE`) before a bundle mode build, since `next
build` reads it too. Share titles, descriptions, the favicon (`app/icon.svg`) and the Open Graph
images (`app/opengraph-image.tsx` and one per run) are drawn at build time with `next/og`, using
its built-in font, so the build fetches nothing. Image URLs use the production domain from `VERCEL_PROJECT_PRODUCTION_URL`, which Vercel
sets automatically; locally they point at localhost.

Browser tests (Playwright plus axe) run against a production build in bundle mode, with the API
URL pointing at a closed port on purpose:

```bash
npx playwright install chromium          # once; system libraries: sudo npx playwright install-deps
npm run build 2>&1 | tee build-output.txt
npm run check:static                     # the public routes must be static
npx playwright test                      # starts next start on port 3100 and stops it after
```

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

When decisions are off, every page shows a "Replay mode" banner. API requests time out
after 20 seconds; a timeout or connection failure shows "The API is waking up" (the free API host
sleeps when idle). Hosted deployment steps are in docs/deploy.md.

For a production build use `npm run build` and then `npm run start` with the same variables.

3. Open http://localhost:3000/queue (also /evals, /lessons and /about).

The API needs migration 0004 (the corrections table) applied to its records database.

## Environment

| Variable | Where | What |
|---|---|---|
| `API_BASE_URL` | web server | Base URL of the API (default `http://localhost:8000`) |
| `REVIEWER_TOKEN` | web server and API | Shared secret sent as `X-Reviewer-Token` by the decision form's server action. Local only: never set it on a public web or API deployment |
| `WEB_DECISIONS_ENABLED` | web server | `1` turns the decision form on. Local only until a real reviewer login exists. Never set it, or REVIEWER_TOKEN, on a public deployment. |
| `REVIEWER_NAME` | API | Name written to the audit log as `human:<name>` |
| `API_ALLOW_REMOTE_DATABASE` | API | Set to `1` only on a deployed API whose databases are not local |

## Design

Tokens, type and layout follow docs/plans/ui-redesign.md: Archivo (variable width axis, through
next/font) with condensed headings, light and dark schemes from `prefers-color-scheme`, and no
client components (click to expand uses `<details>`).

## Checks

```bash
npm run typecheck && npm run lint && npm run build
```
