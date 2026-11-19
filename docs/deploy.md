# Deploy the hosted demo (replay mode)

The hosted site only shows runs that were recorded earlier. Nobody can run the agent or make
decisions there, so no login is needed and nobody can spend model credit. Plan:
docs/plans/mvp-hosting.md.

Three pieces: the database on Neon (prod branch), the API on Render, the web app on Vercel. Do
the steps in order. Never paste a database URL into chat, a file or a commit.

> **Done on 2026-11-16 (Option A):** the demo uses the same Neon database as the Codespace secret `DATABASE_URL` (it already held the slice and the v1 alerts). It was migrated to 0004 and the two real model runs below were recorded there; the audit chain verified (6 rows). Section 1 is only needed again for new runs or a different database. In Render, use that same database URL.

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
5. Nothing to set for share images: Vercel provides `VERCEL_PROJECT_PRODUCTION_URL` itself, and
   the app uses it as the base URL of its Open Graph images.

## 4. Check the site

The public site runs in bundle mode: it reads the recorded runs committed in
`apps/web/content/showcase/v1` and needs no API. `SHOWCASE_SOURCE` picks the data source: unset
(or anything other than `api`) means bundle mode, which is what Vercel uses; `SHOWCASE_SOURCE=api`
is for local development against a running API (set it in `apps/web/.env.local`, which is
gitignored). In bundle mode `/`, `/watch`, every `/watch/<run id>`, `/results` and `/about` are
built as static pages, and the API only pages (`/queue`, `/evals`, `/lessons`) are hidden from
the menu and return "not found". `API_BASE_URL` can stay set or be removed; the public pages never
call it.

1. Open `https://<vercel url>/`. Every page shows the banner "Replay mode: you can browse
   recorded cases, checks and eval results. Decisions are off, so nothing here changes any data."
2. Check the landing preview, the counts headline and the contact links ("Email me" and
   "Message me on LinkedIn").
3. Open `/watch`, then one case: the guided tour plays, Explore steps with the arrow keys, and the
   Case file tab shows the checks and the audit trail without a decision form.
4. Open `/results` and `/about`. Nothing should say "The API is waking up": if it does, the
   deployment is not in bundle mode.
5. Paste the site URL and one `/watch/<run id>` URL into a link preview checker (or a chat
   message) to see the share image and title.
6. Share the Vercel URL.

To refresh the recorded runs (for example after new decisions in the local queue), re-export the
bundle from the local database, never from Neon, then rebuild and check:

```bash
python -m evals.export_showcase --database-url <local database url> \
  --export-date <YYYY-MM-DD> --git-commit <short sha> --out apps/web/content/showcase/v1
cd apps/web && npm run build 2>&1 | tee build-output.txt && npm run check:static && npx playwright test
```

Commit the new bundle (decision 0008 and `data/README.md` describe what it may contain), and
Vercel rebuilds the site on push.
