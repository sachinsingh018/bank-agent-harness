# Plan: edited decisions and corrections storage (M3)

Status: drafted 2026-11-16, owner asked to build what needs no model requests. Builds on docs/plans/approval-queue.md.

## Goal

1. A reviewer can fix the agent's answer before approving it ("approve with edits"), and the fixed answer still passes the same deterministic checks.
2. A reviewer can save a lesson ("correction") from any decision, in plain words, for future cases. Storing and listing corrections is in scope; feeding them into the agent's prompt is not, because that changes agent behaviour and must be evaluated with model runs first.

## Plain-English background

- **Correction.** A short rule a human writes after seeing the agent get something wrong, for example "Regular payroll from one employer to many staff accounts is not fan-out laundering." Later (not in this plan) relevant corrections are shown to the agent on new cases, and the eval runner measures whether they help.

## 1. Approve with edits

- Decision values become `approve`, `reject` and `approve_with_edits`.
- `approve_with_edits` carries a findings object with the reviewer's changes. Editable in this slice: recommendation, confidence, typology, narrative. Claims and figures stay as the agent wrote them (editing citations in a form is error-prone; deferred).
- The API builds the edited findings, validates them with `AmlFindings` (so NONE only with close and the 0.5 thresholds still hold) and runs the module's deterministic verifiers on them, with the same read-only lookup. A failure returns 422 with the errors; nothing is written.
- On success it appends one `edited` event whose payload holds the edited fields, the original values of those fields, the note and the verifier results. An `edited` event counts as a decision (the run moves to Decided, a second decision is 409), and it is reported as "approved with edits".
- The note is required for `approve_with_edits`, as for `reject`.
- The four-eyes rules and the token gates from the approval queue plan apply unchanged.

## 2. Corrections storage

- New table `corrections` (migration 0004), append-only like `case_events` (trigger refuses UPDATE, DELETE, TRUNCATE): id, created_at, module, case_id, run_id, decision_event_id (the decision it came from), kind (`recommendation`, `typology`, `confidence`, `evidence`, `other`), rule text (10 to 500 characters), author (`human:<name>`).
- A correction is saved in the same transaction as its decision, from an optional "save a lesson" part of the decision form (kind and rule text).
- Rule text must not contain calendar dates, month names or years (decision 0006: corrections must never encode the late date artifact). The check is a validator with a clear message; the decision is not written if the correction is refused.
- Retiring a correction later is a new event, not an edit (deferred).
- `GET /corrections?module=aml&limit&cursor` lists them, newest first. A `/corrections` web page shows them, linked to their cases.
- Nothing in the agent, the loop or the prompts reads corrections in this slice. A static test enforces that `harness/` (except `harness/db/`) and `modules/aml/agent/` never import or query the corrections table.

## 3. Tests

- API: approve_with_edits valid (event written, chain verifies, run decided), invalid edits (NONE with escalate, confidence 0.4 with escalate, a verifier failure) give 422 and write nothing, note required, correction saved with its decision in one transaction, correction with a date refused and nothing written, corrections list, append-only trigger on corrections, the static guard.
- Web: typecheck, lint, build; the form shows the edit fields only for approve_with_edits; friendly messages for 422 errors.

## 4. Deferred

Editing claims and figures, retiring corrections, choosing relevant corrections and injecting them into the prompt (needs an eval with and without corrections, brief section 4), real login.
