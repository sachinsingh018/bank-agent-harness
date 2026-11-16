# Deploy the hosted demo (replay mode)

The hosted site only shows runs that were recorded earlier. Nobody can run the agent or make
decisions there, so no login is needed and nobody can spend model credit. Plan:
docs/plans/mvp-hosting.md.

Three pieces: the database on Neon (prod branch), the API on Render, the web app on Vercel. Do
the steps in order. Never paste a database URL into chat, a file or a commit.

## 1. Neon: prepare the prod database (owner approval needed)

Every command in this section writes to the real hosted database. Run each one only after you
have said yes to it. Run them in the Codespace terminal, from the repo root.

Get the prod branch connection string from the Neon dashboard (project, branch `prod`,
Connect). Change the start from `postgresql://` to `postgresql+psycopg://` and keep
`sslmode=require`. Below, `<prod url>` means that string. Put a space before each command so
the shell does not save it in its history.

1. Add the new tables (records and corrections; existing data is not changed). Owner approval
   needed.

   ```bash
    DATABASE_URL='<prod url>' uv run alembic upgrade head
   ```

2. Copy the real model runs from this Codespace to Neon. These runs live in `evals/runs/`,
   which is not in git, so run this in the Codespace that made them. No model is called.
   Owner approval needed (one approval per run).

   ```bash
    HARNESS_WRITE_DATABASE_URL='<prod url>' uv run python -m evals.record_run \
      --run evals/runs/20261116T014628Z-c9d334 --score --allow-remote
    HARNESS_WRITE_DATABASE_URL='<prod url>' uv run python -m evals.record_run \
      --run evals/runs/20261116T020950Z-beb465 --score --allow-remote
   ```

   | Run id | Model | Prompt |
   |---|---|---|
   | 20261116T014628Z-c9d334 | openrouter-free-dev | v1 |
   | 20261116T020950Z-beb465 | openrouter-free-dev | v2 |

   Each prints `write target: remote database` and then `recorded: eval run ...`. A run that
   is already on Neon is refused, so running a command twice is safe.

3. Check the audit chain (read only). Owner approval needed.

   ```bash
    HARNESS_WRITE_DATABASE_URL='<prod url>' uv run python -m harness.records verify --allow-remote
   ```

   It should print `chain ok, ...`.

## 2. Render: the API

In the Render dashboard, open the service `bank-agent-harness-api`, then Environment.

1. Set these variables (values are never stored in git):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Neon prod URL, with the `postgresql+psycopg://` prefix |
   | `HARNESS_WRITE_DATABASE_URL` | the same Neon prod URL |
   | `API_ALLOW_REMOTE_DATABASE` | `1` |

2. Do NOT set `REVIEWER_TOKEN` or `REVIEWER_NAME`. Without them the API refuses every decision,
   which is what we want on a public site. If either one is there, delete it.
3. Save, then Manual Deploy, Deploy latest commit.
4. When the deploy is live, open in a browser (replace with your Render URL):
   - `https://<render url>/health` should show `{"status":"ok"}`.
   - `https://<render url>/queue?state=all` should show JSON with an `items` list.

   The free tier sleeps when idle, so the first request can take up to a minute.

## 3. Vercel: the web app

In the Vercel dashboard, open the project for this repo.

1. Settings, General: Root Directory is `apps/web` (already set in M0).
2. Settings, Environment Variables: set `API_BASE_URL` to the Render URL, for example
   `https://<render url>` (no slash at the end), for Production.
3. Do NOT set `REVIEWER_TOKEN` or `WEB_DECISIONS_ENABLED`. If either one is there, delete it.
4. Deployments, the latest one, Redeploy (env var changes only apply after a redeploy).

## 4. Check the site

1. Open the Render `/health` URL first to wake the API, and wait until it answers.
2. Open `https://<vercel url>/queue`. You should see the banner "Demo in replay mode: browse
   recorded cases. Decisions are disabled." and the recorded cases under the All tab.
3. Open one case: narrative, claims with cited transfers, verifier results, model calls and the
   audit trail should show. The decision form should say decisions are disabled.
4. Open `/corrections`. An empty list is fine.
5. If a page says "The API is waking up", wait a minute and reload.
6. Share the Vercel URL.
