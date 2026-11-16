# Plan: hosted MVP (replay mode demo)

Status: drafted 2026-11-16. The owner wants an MVP hosted before any more hardening. Brief section 10 calls this the public demo in "replay mode".

## Goal

A public URL where anyone can browse recorded AML cases: the queue, each case's narrative, claims with the real cited transfers, verifier results, model calls and the audit trail, plus saved corrections. Nobody can run the agent or make decisions on the hosted site, so no login is needed yet and nobody can spend model credit.

## Plain-English background

- **Replay mode.** The site only shows runs that were recorded earlier. It never starts a new run.
- **Cold start.** Render's free tier puts the API to sleep when idle; the first request takes up to about a minute to wake it.

## 1. What goes where

| Piece | Host | Settings |
|---|---|---|
| API (`apps/api`) | Render free web service (render.yaml, already set up in M0) | `DATABASE_URL` and `HARNESS_WRITE_DATABASE_URL` = Neon prod URL, `API_ALLOW_REMOTE_DATABASE=1`. **No** `REVIEWER_TOKEN` or `REVIEWER_NAME`, so the decision endpoint answers 403. |
| Web (`apps/web`) | Vercel free (root directory `apps/web`, already set up in M0) | `API_BASE_URL` = the Render URL. **No** `REVIEWER_TOKEN`, **no** `WEB_DECISIONS_ENABLED`. |
| Database | Neon prod branch | The slice and v1 alerts are already there (M1). Needs migrations 0003 and 0004 and the recorded runs. |

## 2. Code changes (small)

1. **Record from files.** `python -m evals.record_run --run evals/runs/<run_id> [--allow-remote]` records an existing run's results (results.jsonl and manifest.json) into a database through the existing `record_case_result`, `record_eval_run` and `record_eval_result` paths. No model calls. This copies the real-model runs made locally to Neon without spending requests. Refuses a run that is already recorded there.
2. **Demo banner and cold start.** The web layout shows a short banner when decisions are disabled: "Demo in replay mode: browse recorded cases. Decisions are disabled." API fetches get a timeout and a friendly "The API is waking up, try again in a minute" message instead of a generic error.
3. **Render config.** `render.yaml` lists the new env vars (`HARNESS_WRITE_DATABASE_URL`, `API_ALLOW_REMOTE_DATABASE`) with `sync: false` so values are set in the dashboard, never in git.
4. **Deploy notes** in `docs/deploy.md`: the exact dashboard steps and env vars for Render and Vercel, and the "never set the reviewer variables on a public deployment" warning.

## 3. Steps that touch Neon (owner approval each time)

1. `alembic upgrade head` against Neon prod (adds the records and corrections tables; no existing data changes).
2. `evals.record_run --allow-remote` for the real-model runs (prompt v1 and v2 runs, and later v3).
3. Read-only check: open the hosted queue and one case page.

## 4. Steps the owner does in dashboards

1. Render: set the env vars above on the service, redeploy.
2. Vercel: set `API_BASE_URL`, redeploy.
3. Share the Vercel URL.

## 5. Not in the MVP

Login, live runs from the site, decisions on the hosted site, eval dashboards and benchmark pages (M4), any hardening.
