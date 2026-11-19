TASK: BUILD, PLAN FIRST. This prompt replaces the drafts in docs/notes/new prompt.md and docs/notes/newestprompt.md. Follow this one only.

CONTEXT
bank-agent-harness is my portfolio project: an AI analyst that investigates anti-money-laundering alerts with read-only tools, cited evidence, deterministic verifiers, a reviewer agent and human approval. The public site https://bank-agent-harness.vercel.app runs in replay mode only: visitors watch recorded runs, with no login, no live model calls and no uploads. I will not run the agent live for visitors.

Read these first: docs/session-log.md ("Current state"), CLAUDE.md, docs/notes/ui-facts.md and docs/notes/ui-architecture-check.md (your round 2 review; its findings are built into this prompt). Use them as your map so you don't redo that research, but trust the code over them. If anything here is unclear or seems to contradict the code, you are free to read any part of the codebase to understand it, and read the relevant code before changing anything. Read what you need, not everything. If the code still leaves a real decision open, ask me one short question instead of guessing.

STEP 0: PROTECT MY WORK (before any change)
- Run git fetch, then git status. Local main should be about 8 commits ahead of origin. Show me anything uncommitted and ask before committing it. Never discard anything.
- Ask me what to do with each untracked note: docs/notes/new prompt.md, docs/notes/newestprompt.md and docs/notes/ui-architecture-check.md.
- Leave the existing tag checkpoint-2026-11-19-replay-showcase and branch wip/edits-corrections untouched.
- Create a local tag named pre-ui-showpiece on the current commit.
- Ask me whether to push a backup branch named backup/pre-ui-showpiece. It must not touch main. It will trigger a harmless Vercel preview and a CI run.
- Create a local branch named ui-showpiece from main and do all work there.
- Never rewrite history: no rebase of pushed commits, no reset --hard, no force push, no deleting branches or tags, no git clean or checkout that discards changes.

SCOPE
This is the web UI plus one new read-only exporter. Nothing else.
- You may change: apps/web, a new evals/export_showcase.py with its own new tests, the new folder apps/web/content/showcase/, the CI workflow (only to add web test steps), and docs (plans, decisions, glossary, credits, session log, deploy notes, and a data/README.md entry).
- Protected, do not edit: the agent loop, model adapter, prompt files, tools, rules and detectors, the API (apps/api), database models, migrations, the eval runner and scorer, loaders, evals/test_label_readers.py, existing tests outside the web app, render.yaml and the devcontainer.
- The exporter may import and call existing functions but must not modify them.
- If a change to a protected area seems necessary, stop and ask me first, explaining why.

GOAL
Make the public site a portfolio showpiece that a recruiter understands in 60 seconds and an engineer can probe for 20 minutes. The AI agent is the hero. Upgrade the existing UI, do not rebuild it. Every number and outcome on screen must come from stored data, nothing invented. If data for a section is missing, hide the section. Never overclaim what the system did.

UI RULES
- Existing routes keep working unchanged in API mode.
- Keep the CSS variables, design tokens, Archivo and the banknote engraving visual language in app/globals.css. Do not restructure globals.css. New components use CSS Modules built on the existing tokens.
- No Tailwind, no shadcn/ui, no React Flow. Keep hand-written SVG. Animate with CSS and the Web Animations API.

ARCHITECTURE
A. Exporter: evals/export_showcase.py, run as python -m evals.export_showcase --database-url <local url> --export-date <date> --git-commit <sha> --out apps/web/content/showcase/v1
- Calls existing functions directly, without modifying them: apps/api/watch.py list_showcase(engine, limit) and get_run_steps(engine, run_id), and apps/api/queue.py get_run(run_id, engine, tool_engine) with a tool engine from tools.aml.db.build_tool_engine(url). Catch HTTPException and report clearly.
- Builds a plain engine from the explicit local URL, refuses non-local hosts using harness.db.session.is_local_url, and runs inside a read-only transaction. Never uses apps.api.deps.get_records_engine.
- get_run needs the gitignored evals/runs/*/results.jsonl, which exist locally. Fail with a clear message if they are missing.
- The curated cases live in the exporter as a small hard-coded table: run id, hand-written outcome label, short caption, gallery order and story group. The exporter never reads transaction_labels.
- Refuses scripted runs (agent_runs.profile or provider marks them as scripted), and any case whose alert id is not in evals/datasets/aml_alerts_dev_v1.json or is in evals/datasets/aml_alerts_heldout_v1.json.
- Writes allowlisted fields only, without dropping any field the TypeScript types require. Drops provider request ids and Langfuse trace ids. Caps raw step text at 2,000 characters with a truncated flag. Targets: each run under 120 KB, the whole bundle under 1.5 MB.
- Adds an optional transfers list to each graph edge, as {txn_id, ts, amount, currency}, looked up by txn_id in transactions (not via alert_transactions, because many edge transfers are outside the alert).
- Adds provenance to each run file: prompt version from RunDetail.prompt.version, ruleset version from alerts.ruleset_version, model name and recorded date.
- Adds per-step effort: map model calls to tool_call steps by order only when the counts match, otherwise include run totals only. Tool durations come from tool_calls.duration_ms.
- Includes recorded human decisions through get_run (decision and events), and reviewer agent rows only where they exist. Never includes scripted reviews.
- results.json comes from evals/runs/20261118T234719Z-cd5c6c/score.json, strict truth, with cases sent to a person counted as escalated:
  - headline: split early (alerts ending on or before 2022-09-10): 78 alerts, 71 false, 7 real; the agent would close 29 of the 71 false and 4 of the 7 real;
  - secondary: split all: 98 alerts, 82 false, 16 real; closes 35 of 82 false and 8 of 16 real;
  - use each split's own same-population rules baseline from score.json, and keep evals/baselines/aml_rules_v1.json (171 alerts) only as separately labelled context. Never mix populations in one number.
- manifest.json: bundle format version, the passed-in export date and git commit, model names, prompt versions, ruleset version, eval run id, featured run, gallery order, story groups, captions and outcome labels.
- Deterministic output with sorted keys. Two exports with the same inputs must be byte-identical. Safe to rerun. Passes CI ruff, format and mypy.
- New Python tests insert their own fixture rows into the test database and write a temporary results.jsonl. They never depend on local eval folders. Test that the allowlist holds, refusals work (scripted runs, held-out alerts, non-local URLs), and output is deterministic.

B. Data switch
- lib/data.ts with two implementations: the existing lib/api.ts and a new lib/bundle.ts that reads the committed JSON.
- The server-side env var SHOWCASE_SOURCE picks one: "bundle" by default, "api" for local development through a gitignored .env.local.
- New TypeScript types for manifest.json and results.json, an optional transfers field on FlowEdge, and the provenance and effort fields.
- /results always reads results.json from the bundle, in both modes.

C. Static pages in bundle mode: /, /watch, /watch/[runId], a new /results, and /about
- Route all data access through lib/data.ts, including the landing.
- Delete force-dynamic from /about.
- Move ?step=N into the Replay client component, using useSearchParams inside a Suspense boundary.
- Move the /watch pattern and outcome filters to the client, or remove them in bundle mode.
- generateStaticParams returns the manifest ids in bundle mode. Keep dynamicParams at its default so API mode is unchanged, and call notFound() when the bundle has no such id.
- Add a CI check that fails unless each of these routes shows as static (○) or prerendered (●) in the next build output.

D. Routes in bundle mode
- The nav shows only the public pages. Queue, Evals and Lessons links render only when SHOWCASE_SOURCE=api.
- /queue, /queue/[runId], /lessons and /corrections call notFound() in bundle mode, and /evals redirects to /results.
- All of them keep working unchanged in API mode.

E. Case file
- Move the pure section components out of app/queue/[runId]/page.tsx (Verdict, Strip, ClaimItem, ChecksSection, ReviewerSection, RunSection, LessonsSection, AuditSection) into app/_ui/case-file.tsx and import them back.
- Leave DecisionForm, DecisionSection and the server action where they are.
- Render the case file on the server and pass it into the Replay client component as a ReactNode prop.
- Take before and after screenshots of /queue/[runId] in API mode locally to prove the queue is unchanged.

PRESENTATION
1. Curated cases (8 runs, all verified as real runs on development alerts):
   - Story "One alert, four prompt versions" on alert AL-0679023ed0bc (a real laundering case):
     - f7eb0d02-e996-4921-9278-1762e0e5ed03: v1 caught it.
     - 823c8309-3f1d-465e-8b70-5ffb4fe19909: v2 caught it faster.
     - 29c00e8b-abe7-4fd9-80a1-1f920c492ab0: v3 missed it.
     - e75bcdb8-6779-4261-afed-3e70f759a2ff: v5 still closed it, the reviewer agent timed out, and the case went to a person.
     The ending reflects my recorded decision from the owner review step. The caption may note, factually, that v4 also closed it.
   - Catches on other detectors: 029a419f-6e72-4ef8-85da-25a6fe1d5e8e (scatter-gather, escalated at 0.9) and 20be587f-cbd0-4b2c-ad74-fbee69513bb5 (fan-in, escalated at 0.75).
   - False alert correctly closed: 70353513-1255-4b6f-aba7-267c3402dd6e (AL-01cf1e794cc7).
   - Verifiers sent it to a person: 61e56e79-9dd9-4115-8172-36e7775ddc24.
   - Featured case for the landing and guided tour: f7eb0d02 unless you show a better choice in the plan.
2. Landing: one sentence on what it is, the existing preview upgraded into a short looping mini-replay of the featured case, one primary button "Watch an investigation", and the early-split headline from results.json: false alarms the agent would close, shown next to the real cases it would also close, plus one line saying that is why a person approves every close. Never show the reduction without the misses beside it.
3. Replay (/watch/[runId]): two modes.
   - Guided: a 60-second auto-play with captions for first-time visitors.
   - Explore: free stepping.
   - Keep the existing step kinds and ?step=N.
   - The narration is code-written summaries of the agent's tool calls. Label it "What the agent did" and never present it as the model's own words.
   - "Agent notes" appear only when a thinking step exists. Otherwise hide that section.
   - A "Technical details" toggle per step (tool arguments, tool duration, and tokens and latency where the per-step mapping is valid), and "Download raw run".
   - Evidence chips cross-highlight graph nodes and transfers.
   - A "Case file" tab from part E.
   - Provenance badge: "Recorded run, replayed step by step", recorded date, model, prompt version, ruleset version.
   - One line saying the answer key was hidden from the agent.
   - Show cost honestly as $0 on a free model, with tokens and latency.
   - Show the reviewer agent only where a review row exists, and say plainly when it did not finish. Never show a scripted review.
4. Graph: upgrade the existing SVG flow. Reveal accounts as the agent finds them, animate money moving along edges in time order using the transfers lists, and show amounts with currency on focus. Respect prefers-reduced-motion. Provide a text alternative (the step narration list).
5. Results (/results): the early-split headline in clear counts and plain words, the all-alerts numbers as secondary, each split's same-population rules baseline, and the 171-alert rules baseline as separate labelled context. State: strict truth, cases sent to a person count as escalated, development alerts only, no held-out run yet, free model. Explain the early and late split and the date leak in plain English. No prompt-version comparison view in this build.
6. How it works (/about): one clear SVG diagram of the harness (alert, read-only tools, agent loop, verifiers, reviewer agent, human approval, audit trail, tracing, evals) with short captions. Say honestly that the reviewer agent is built but has not completed a review on the free models so far, because they hit time and model limits.
7. Data attribution on /about and /results: the transfers come from the IBM synthetic anti-money-laundering dataset under CDLA Sharing 1.0.
8. Contact call to action "Want to try it with your own data?": an email link to sachinsingh018@gmail.com with the subject "Bank agent harness demo request", and a link to https://www.linkedin.com/in/sac108.
9. Sharing: page titles, descriptions, favicon, and Open Graph images made with next/og, which is built in.

DATA RULE
Committing the bundle adds data to the repo. Add a decision note in docs/decisions/ and an entry in data/README.md describing the bundle, its source, licence and size, consistent with decision 0003.

OWNER REVIEW STEP (local only)
- Set me up to review the curated runs that await a decision in the local queue. Start the API with both HARNESS_WRITE_DATABASE_URL and DATABASE_URL set to the local database URL (the shell DATABASE_URL is Neon, so override it), plus REVIEWER_TOKEN and REVIEWER_NAME. Start the web with WEB_DECISIONS_ENABLED=1 and the same token.
- You may generate a random local reviewer token yourself. Keep these values only in gitignored local env files, never in Vercel, Render or the repo.
- The public reviewer name is: Sachin Singh.
- List which curated runs can take a decision and what each one's status is.
- Decisions are permanent: one per run, append-only. I make every decision myself in the browser. Never submit a decision for me. Do not attach corrections, since that saves a lesson.
- After I finish, re-export the bundle so it includes my decisions.

QUALITY AND TESTS
- Responsive from 390px wide. Keyboard: arrow keys step, space plays or pauses. Visible focus, AA contrast, prefers-reduced-motion respected.
- No em or en dashes in UI copy, docs, comments or commits.
- Dev dependencies allowed: @playwright/test and @axe-core/playwright. Anything else: ask first, with the reason, licence, size and last publish date. Pin exact versions published at least 14 days ago.
- CI web job additions: npx playwright install --with-deps chromium, npm run build, then next start with SHOWCASE_SOURCE=bundle and API_BASE_URL=http://127.0.0.1:9 (unreachable on purpose). Tests: landing, gallery, one replay in guided and explore modes, results and about all render, plus an axe check on each. Keep the existing typecheck, lint and build, and keep all existing Python tests passing.
- Locally, browsers need sudo npx playwright install-deps once per Codespace. Do not change the devcontainer. If local browser tests are impractical, rely on CI.
- Before calling a page done, take Playwright screenshots at 390, 768 and 1280 widths. Run Lighthouse only if Chrome is already available, without adding a dependency; otherwise skip it and say so. At most two design iterations per page.

INSPIRATION
You may look at up to 5 public sources (for example trace viewers such as Langfuse for timeline patterns) and summarise the ideas in about 10 bullets in the plan. Copy code only if its licence allows, and record the source, licence and what was adapted in docs/credits.md.

PLAN FIRST
Write docs/plans/ui-showpiece.md and wait for my approval. Keep it short: confirm the protected paths, the 8 curated runs and their statuses, the bundle format and field allowlist, how the existing functions are reused, the routes in each mode, a short design direction that refines the existing tokens, the test plan, and these slices:
- S1a: exporter and its tests, plus the first bundle (without decisions yet).
- S1b: data switch, static pages, route handling, case file extraction with no visual change, CI tests and the static route check. After S1b, pushing is safe.
- S2: owner review step and re-export with my decisions (needs me).
- S3: replay upgrade (guided, explore, case file tab).
- S4: graph upgrade.
- S5: landing, results and about.
- S6: contact, Open Graph, mobile and accessibility fixes, and docs (decision note, data/README.md, glossary entries, the fix to docs/deploy.md section 4).

DEPLOY RULES
- Do not push main. After S1b is done and I approve, push the ui-showpiece branch for a Vercel preview, check the preview with the API unreachable, and merge to main only when I say so.
- Pushing main also makes Render auto-deploy the API against Neon at migration 0005. That is harmless because the public site will not call it.
- Neon stays untouched. Migration 0006 and the backfill on Neon are not needed for the public site.
- When the public site is verified in bundle mode, give me the steps to suspend the Render service and remove its DATABASE_URL. Do not touch Render yourself.
- DATABASE_URL in this shell is remote Neon. Always use the local database explicitly.
- Ask before every push and before anything that touches Neon.
- I am watching my Claude usage: one slice per commit, builder, tester and reviewer loop as CLAUDE.md says, subagents only when needed, and short updates.
- Update docs/session-log.md at the end of each slice. Never ask me for keys or connection strings.
