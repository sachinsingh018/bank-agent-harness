# 0008: Commit the showcase bundle for the public site

**What:** Track `apps/web/content/showcase/v1/` in git: about 0.5 MB of JSON made by `python -m evals.export_showcase` from the local database. It holds 8 curated real runs on development alerts (steps, case file, transfers on graph edges), a manifest and eval results from run 20261118T234719Z-cd5c6c.

**Why:** The public site runs in replay mode only. With the bundle in the repo, Vercel builds static pages with no API, no database and no cold start, and CI can test the site with the API unreachable. Render and Neon are no longer needed by visitors.

**Rule:** This is a second exception under decision 0003: synthetic data under 10 MB, listed in data/README.md. The exporter refuses scripted runs and held-out alerts, never reads the label table, drops Langfuse trace ids, and caps raw step text at 2,000 characters. Outcome labels such as "Caught" or "Missed" are hand-written in the exporter for these development alerts only.

**Alternatives:**
- Keep calling the Render API: slow cold starts, needs Neon at migration 0006 and a backfill.
- Fetch the bundle from object storage at build time: more moving parts for 0.5 MB.

**License:** the transfers come from the IBM synthetic AML dataset under CDLA Sharing 1.0, so the bundle stays under that license with attribution (data/README.md, and on the site in a later slice).
