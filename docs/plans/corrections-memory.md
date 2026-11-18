# Plan: corrections memory, feeding lessons into new cases (M3)

Status: drafted 2026-11-18, owner asked to build B.7. Built and tested with the scripted model; whether it helps is measured later with real runs (with and without, brief section 4).

Wiring as built: the provider is not a ModuleSpec field, because modules/aml/agent/spec.py is agent code and must not reach modules/aml/memory.py. The ModuleSpec only names the template (`lessons_template`); the eval runner builds `AmlLessonsProvider` and passes it to `run_case(lessons_provider=...)`. Lesson ids are in the trace and in the run's case event payload (`lesson_ids`), not in agent_runs, so run_digest is unchanged.

## Goal

When the agent starts a case, it is shown a few relevant lessons that a human saved earlier (the `corrections` table), so past mistakes are less likely to repeat.

## Plain-English background

- **Corrections memory.** The saved lessons, for example "Regular payroll from one employer to many staff accounts is not fan-out laundering." The agent reads them as guidance, never as facts about the current case.

## 1. Selection (deterministic, no embeddings)

- Candidates: corrections for the module, newest first.
- **Never** a correction whose source case is the alert being investigated. That would hand the agent a human's verdict on this very case and make any score meaningless.
- Relevance: corrections whose source alert used the same detector as the current alert come first, then the rest. At most 5 corrections and 1,500 characters in total.
- The selection code lives outside the agent: `modules/aml/memory.py`, which reads `corrections` and `alerts` read-only through the records connection. The agent packages (`harness/` loop code and `modules/aml/agent/`) still never touch the corrections table (existing static guard).

## 2. Injection

- The runner (outside the agent) asks the module's memory provider for lessons and passes them to the loop as plain text notes. The loop adds them to the first user message through a versioned template file, `modules/aml/prompts/lessons_block_v1.md`, which says they are past reviewer lessons, general guidance, not evidence about this case.
- The trace records the correction ids that were shown. The run record and case page show "Lessons shown to the agent".
- Opt in: runner flag `--lessons on|off`, default **off** until an eval shows it helps. The manifest records it. CI runs with it off, plus one scripted test with it on.

## 3. Evals

- The scorer reports how many cases got lessons and the ids used.
- For eval honesty: a lesson whose source alert is in the held-out set is never used in any run (lessons may only come from dev or other cases). The scorer warns if a run used a lesson from the same dataset it is scoring.

## 4. Tests

Selection (same alert excluded, held-out sources excluded, detector priority, caps, newest first), injection text from the template, trace ids, `--lessons off` changes nothing, static guards still pass, API and case page show the lessons.
