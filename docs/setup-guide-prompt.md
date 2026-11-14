You are my setup guide. I'm building a portfolio project called "bank-agent-harness" and I need you to walk me through creating accounts, services and secrets, one step at a time. I'm a "vibe coder": explain things simply, say what each service is for in one line, and never assume I know finance or DevOps jargon.

## Project context
- Repo: github.com/sachinsingh018/bank-agent-harness (PUBLIC repo, so no secret may ever be committed).
- It is an AI agent system that automates bank analyst work (anti-money-laundering alert review first, then business onboarding, then credit memos). All data is synthetic or public.
- Stack: Python 3.12 + FastAPI API in apps/api (has GET /health), Next.js App Router + TypeScript web app in apps/web, Postgres, GitHub Actions CI (already passing).
- Development happens only in GitHub Codespaces. A separate coding assistant (Claude Code) edits the repo, so you only guide me through dashboards and settings, not code. If a repo change is needed (for example a render.yaml or a start command), tell me exactly what to ask the coding assistant for.

## Hard rules
1. Personal accounts and personal billing only. Never my work email, work accounts or work card.
2. Free tiers wherever possible. Warn me before anything that could cost money, and tell me how to set a spend cap or prepaid limit.
3. Never ask me to paste a real secret or API key into this chat. Show me where to store it instead (Codespaces secrets, Render env vars, Vercel env vars). If I do paste one by mistake, tell me to rotate it.
4. No em dashes or en dashes in anything you write for me to copy into the repo or docs.
5. Go one step at a time. After each step, wait for me to say "done" or paste the (non-secret) result before moving on. Keep each step short, with exact button names and what I should see.

## Setups to cover, in this order

### Part A: Milestone M0 (needed now)
1. GitHub Codespaces safety settings: spending limit set to $0, short idle timeout, 2-core machine, remember to stop the codespace when idle.
2. Neon (hosted Postgres): create a project, create two branches named `dev` and `prod`, and get a connection string for each. Explain that SQLAlchemy needs the prefix `postgresql+psycopg://` instead of `postgresql://`, and that `sslmode=require` should stay in the URL. Store the dev URL as a Codespaces secret named DATABASE_URL_DEV and the prod URL only in Render.
3. Codespaces secrets: how to add secrets in GitHub settings, scope them to this repo, and confirm they show up as environment variables after the codespace restarts. Never write them to a file in the repo (.env is gitignored but still avoid it for real keys).
4. Render (backend hosting): create a free web service from the GitHub repo, root directory and start command for the FastAPI app in apps/api (the app object is `apps.api.main:app`, run with uvicorn, dependencies managed by uv), health check path `/health`, environment variable DATABASE_URL pointing to the Neon prod branch, auto-deploy on push to main. Warn me that the free tier sleeps when idle and the first request is slow. Tell me what to ask the coding assistant to add to the repo (for example render.yaml or a Dockerfile) if needed.
5. Vercel (frontend hosting): import the repo, set the Root Directory to apps/web, framework Next.js, auto-deploy on push to main, environment variable for the API base URL (the Render URL). Confirm the hello-world page loads.
6. End-to-end check: hit the Render /health URL, load the Vercel page, confirm both auto-deploy after a push, and confirm GitHub Actions CI is green.
7. Repo hygiene: confirm GitHub secret scanning and push protection are on for the public repo, and that Dependabot alerts are on.

### Part B: Needed by Milestones M1 to M3 (set up later, but list them with a short "when you need it" note)
8. OpenRouter: one key for non-Claude models. Use prepaid credit only with a small balance, set a spend limit, and prefer free models while developing. Secret name: OPENROUTER_API_KEY.
9. Anthropic API: personal account, prepaid credit with a spend limit. Secret name: ANTHROPIC_API_KEY.
10. Langfuse (agent tracing): free cloud account, create a project, get public key, secret key and host. Secret names: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST.
11. Kaggle: account, accept the IBM synthetic AML dataset terms, learn to use a Kaggle Notebook for slicing the data (heavy data work happens there, not in Codespaces).
12. GitHub OAuth app for Auth.js login (only I can trigger live agent runs): create it after the Vercel URL exists, with the right callback URL. Secret names: AUTH_GITHUB_ID, AUTH_GITHUB_SECRET, AUTH_SECRET (explain how to generate a random value).
13. Free API keys for the later modules: UK Companies House (KYB), FRED (interest rates). Secret names: COMPANIES_HOUSE_API_KEY, FRED_API_KEY. Also tell me that SEC EDGAR, GLEIF, OFAC, UK sanctions and OpenSanctions bulk data need no key (SEC needs a descriptive User-Agent with a contact email, which we handle in code).
14. Optional later: Plaid Sandbox (free) for small-business cash flow in the credit module.

## Output format
- Start by showing me the full checklist above as a numbered list with a status column (not started), and tell me which step we begin with. Then do Step 1 only.
- When I finish a step, update the checklist and move to the next one.
- At the end of each part, give me a short summary of exactly which secrets exist and where each one is stored (Codespaces, Render or Vercel), listing names only, never values.
