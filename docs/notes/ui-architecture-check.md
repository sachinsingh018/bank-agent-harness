# UI architecture check, round 2 (2026-11-19)

Review of `docs/notes/newestprompt.md` against the code and the LOCAL database (host `db`, database `harness`, read-only). Nothing was built. Round 1 findings are folded into that prompt correctly; this note covers what is still open. All numbers below were read from data, not estimated, unless marked.

## Confirmed as written
- **Reuse of serializers.** `list_showcase`, `get_run_steps` and `get_run` run outside the API with plain engines (called them again for 8 runs).
- **Curated runs.** All five exist with traces, real model, dev alerts. Full id `70353513-1255-4b6f-aba7-267c3402dd6e`.
- **Results numbers in the prompt are right.** `score.json` of cd5c6c, split `all`, strict truth, human review counted as escalated: 98 alerts, 82 false, 16 real; tn 35, fn 8. The same file has a same-population rules baseline (`splits.all.baseline`: raised 98, false 82), so `aml_rules_v1.json` (171 alerts) is only needed as context.
- **Sizes.** Largest run (steps plus case file, before capping) is 92 KB (61e56e79). Every run is under 120 KB even uncapped. Bundle of 8 runs about 0.6 MB.
- **Git.** After `git fetch`: main is ahead of origin/main by 8. Untracked: `docs/notes/new prompt.md`, `docs/notes/newestprompt.md`, `docs/notes/ui-architecture-check.md`. Nothing else uncommitted.
- **Env files.** `.gitignore` ignores `.env` and `.env.*`, so `apps/web/.env.local` and a local API env file stay out of git.
- **Reviewer name.** "Sachin Singh" passes `REVIEWER_NAME_PATTERN` in `apps/api/deps.py`.

## New findings (need a change to the prompt)

1. **The reviewer agent never finished on a real run.** All 15 real reviews in `agent_reviews` failed (6 model_error, 8 limit_time, 1 limit_steps). The 10 approvals are all scripted runs. None of the five curated runs has a review at all. The site must not imply the reviewer works. Suggested wording: "Show the reviewer only where a review row exists, and say plainly when it did not finish. Never show a scripted review."
2. **No curated run has any "Agent notes".** None of the 8 runs I checked has a `thinking` step; the free model returned only tool calls. Narration comes from the code-written step explanations. Suggested: "Agent notes appear only when a thinking step exists; otherwise the section is hidden."
3. **Story ending exists.** Later real runs on AL-0679023ed0bc: v3 cd5c6c `3b48961a` close, v4 `edd7abfd` close, v5 `defb6af8` close, then three v5 runs that went to a person because the reviewer failed. Latest: `e75bcdb8-6779-4261-afed-3e70f759a2ff` (v5, closed at 0.15 and sent to a person because the reviewer timed out, 27 min wall time). The alert is a true positive (8 of 8 transfers laundering), so the honest ending is: "v5 still closes it; a person would have caught it." Add it as the fourth story run.
4. **Variety picks (real, dev, early window, traced, different detectors from the story):**
   - `029a419f-6e72-4ef8-85da-25a6fe1d5e8e`: scatter-gather AL-833c795ec18e, escalated at 0.9, 16 of 18 transfers laundering (catch), cd5c6c, 7 tool calls.
   - `20be587f-cbd0-4b2c-ad74-fbee69513bb5`: fan-in AL-986d0280c6ee, escalated at 0.75, 10 of 10 laundering (catch), cd5c6c.
   - `1afe1ec3-a642-44b4-8996-6e7a0e7b39f9` is a correct close (0 of 30 laundering) but it is fan-out, the same detector as the story, so I would skip it.
   Truth was read once by me from `transaction_labels` for this proposal only; the exporter must still use the hand-written table.
5. **Headline population conflicts with the scorer.** The scorer's own note says early alerts (end on or before 2022-09-10) are the headline, because of the late date leak (decision 0006). Early: 78 alerts, 71 false, the agent closes 29; 7 real, it closes 4. The prompt uses `all` (98). Either is defensible, but you should choose. Suggested: "Headline uses split all, strict truth, with one line on the date leak and a link to the early numbers," or switch to early.
6. **Determinism vs manifest.** "Deterministic output" conflicts with "export date" and "git commit" in `manifest.json`. Suggested: "Export date and git commit are passed in or taken from the data; the determinism test compares two exports with the same inputs."
7. **Static route check symbols.** `/watch/[runId]` with `generateStaticParams` shows as ● (SSG), not ○, in the `next build` table. Suggested: "Fail unless each listed route is ○ or ●."
8. **Per-step tokens and latency.** `ReplayStep` has no tokens or latency. `RunDetail.model_calls` is per call and, in all 8 runs, its count equals the number of tool_call steps, so mapping by order works today. Suggested: "Map model calls to steps by order only when the counts match; otherwise show run totals. Tool durations come from tool_calls.duration_ms."
9. **Prompt and ruleset version on the provenance badge.** `RunSteps` has neither. Prompt version comes from `get_run` (`RunDetail.prompt.version`); ruleset version from `alerts.ruleset_version` (only `v1` exists). The exporter must add both to each run file or the manifest.
10. **Prompt-version view on /results is not supported.** Only cd5c6c (v3) is scored on the dev set. The v1 and v2 runs are 3-case smoke runs, and the v4 and v5 dev runs are unscored. Suggested: "No prompt-version view in this build."
11. **Exporter tests in CI.** The CI database holds only scripted runs and has no `evals/runs`. Suggested: "Tests insert their own fixture rows into the test database and write a temporary results.jsonl; they never depend on local eval folders."
12. **Lighthouse is not an allowed dependency.** `npx lighthouse` downloads a package. Suggested: "Run Lighthouse only if Chrome is already available, without adding a dependency; otherwise skip and say so."
13. **/results in API mode.** Unspecified. Suggested: "/results always reads results.json from the bundle, in both modes."
14. **Untracked notes.** Step 0 asks only about `new prompt.md`. Ask about all three untracked notes.

## Top 3 risks
1. **Overclaiming the reviewer.** The hero story names a reviewer agent that never completed a real review. Reduce: finding 1 wording, and say why (free models timed out) on /about.
2. **Headline read as better than it is.** 35 of 82 false alarms closed comes with 8 of 16 real cases closed. Reduce: always show both counts side by side (the prompt already requires it), say "strict truth", and add the date leak line.
3. **Static build quietly turns dynamic.** Reduce: the route check (○ or ●) plus Playwright with the API unreachable, both in CI.

## Sizes
S1 large (exporter, tests, data switch, static pages, route handling, extraction, CI). S2 small (needs you). S3 large. S4 medium. S5 medium. S6 medium. Consider splitting S1 into S1a (exporter and bundle) and S1b (web switch, routes, CI) to keep commits small.

## Verdict
**Ready with the listed changes.** Nothing forces a protected edit. Before building, decide finding 5 (headline population), accept or swap the extra runs in findings 3 and 4, and add the wording for findings 1, 2, 6 and 7.
