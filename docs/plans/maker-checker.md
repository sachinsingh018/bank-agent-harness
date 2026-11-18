# Plan: maker-checker reviewer agent (M3)

Status: drafted 2026-11-18, owner asked to build it next. Built and tested with the scripted model only; measuring it needs real model runs later.

## Goal

After the investigating agent (the maker) produces findings that pass the deterministic verifiers, a second agent (the checker) with a different prompt and checklist reviews them and approves or rejects with reasons. A rejection never closes anything: the case goes to human review. Brief section 4, maker-checker.

## Plain-English background

- **Maker-checker.** One person prepares, another checks before anything goes forward. Banks use it to catch mistakes and stop one person acting alone.

## 1. Flow

```
maker run -> verifiers pass -> checker run (read-only tools, own prompt, smaller budget)
  checker approves  -> proposed (with the review attached)
  checker rejects   -> human_review, reason reviewer_rejected (with the review attached)
  checker fails     -> human_review, reason reviewer_failed (limit, schema, model error)
maker did not reach proposed -> no checker run (nothing to check)
```

## 2. Harness (module agnostic)

- `ReviewerSpec` on `ModuleSpec` (optional): prompt ref, output model, submit tool spec, verifiers, `build_review_message(case_id, findings) -> str` from a versioned template file.
- The checker reuses `run_agent` with its own spec and limits (`[reviewer]` section in config/harness.toml: max 6 tool steps, smaller token budget). Same tool session, so the same read-only tools. Same single retry rule inside the checker run.
- The checker's verdict model must include `verdict: approve | reject`. The harness reads only that field to decide the outcome; everything else is module data.
- `CaseResult` gets `review: ReviewOutcome | None` (verdict, the validated review dumped, the checker's own trace summary: model calls, usage, cost, tool steps). Totals and cost include the checker.
- New reasons `reviewer_rejected`, `reviewer_failed`. Never auto-close.
- Profile: the checker uses the maker's profile unless `--reviewer-profile` is given (the brief wants model split benchmarks later).
- Opt out for ablations: runner flag `--no-review`, recorded in the manifest.

## 3. AML checker

- `modules/aml/prompts/reviewer_v1.md`: a senior analyst checklist: is the recommendation supported by the cited evidence, is an ordinary explanation ruled out, does the typology match the structure, is the confidence consistent with the evidence, are any claims overstated. Reject with specific reasons; approve only if the findings would hold up.
- `AmlReview` model: verdict, reasons (1 to 5 short strings), concerns (0 to 5, each with optional claim_index and issue), confidence 0 to 1. extra forbid.
- Verifier: claim_index values must exist in the maker's findings.
- Review template `modules/aml/prompts/review_message_v1.md` shows the alert id and the maker's findings as JSON.

## 4. Records, API and UI

- Reviews go in a new append-only table `agent_reviews` (migration 0005): run_id (FK, unique), verdict, review JSONB, usage JSONB, cost, tool_steps, profile, model, prompt version and sha256, created_at. A separate table because the run digest covers every agent_runs column, and adding a column would break the digests of runs already recorded (local and Neon).
- The review is recorded in the same transaction as its run. The case event payload gets `review_digest` (sha256 of the review row), and `harness.records verify` checks it when present. Runs without a review verify as before.
- API run detail includes the review; the queue shows reviewer verdict. The case page gets a "Reviewer" section.
- `evals/record_run.py` copies reviews too.

## 5. Evals

- Scorer adds: reviewer reject rate, and how the review changed precision and recall (escalations the checker rejected that were false positives versus real ones). Indicative only on small sets.
- Scripted policy for CI: a scripted checker that approves, so the fast eval exercises the whole path with no API key.

## 6. Cost note

The checker adds model calls to every proposed case, roughly half again as many requests. Real runs stay small until there is more request budget.

## 7. Tests

Scripted maker plus scripted checker: approve, reject (human_review reviewer_rejected), checker limit or schema failure (reviewer_failed), maker not proposed (no checker run), --no-review, records (agent_reviews row, review_digest verified, tampering detected), existing recorded runs still verify, API detail and web build.
