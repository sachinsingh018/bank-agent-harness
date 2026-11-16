# 0005: Who may read the AML labels, and how the agent eval sets are split

**What:**
- **Reader allowlist.** Only four non-test files may name the labels (`TransactionLabel`, `transaction_labels`, `is_laundering`, `attempt_type`, `attempt_id`): `evals/aml_rules_score.py` (rules scorer), `evals/aml_agent_score.py` (agent scorer, step 6b), `evals/make_aml_datasets.py` (dataset builder) and `data/load_aml_slice.py` (the loader, the only writer). `evals/test_label_readers.py` fails if any other non-test file in `evals/` or `data/` names them.
- **Guard scope.** The label words are also banned in `harness/` (except `harness/db/`, which defines the tables), in `modules/aml/agent/` and in the AML prompt files (`modules/aml/test_no_labels.py`). Nothing in `harness/` (except `harness/db/`), `modules/aml/agent/` or `tools/` may import from `evals`, and the prompts may not reference it.
- **Datasets split by schemes.** `evals/make_aml_datasets.py` reuses the scheme split of the rules scorer (seed 42, 4 development schemes per family). An alert goes to `dev` if most of its scheme transfers are in development schemes, otherwise to `heldout`. A tie goes to `heldout`, so we never tune on a doubtful alert. Alerts with no laundering go by the parity of the first hex digit of sha256 of `42:<alert_id>`. The smoke set is 10 dev alerts ordered by sha256 of the alert id, taking the first 3 strict positives first. Smoke v2 (`aml_alerts_smoke_v2.json`, used by CI) replaces that rule because all 3 v1 strict positives were in the late window: in the same order it takes up to 2 early window strict positives, 1 late strict positive and 4 early alerts that are not strict positives, then fills to 10. Smoke v1 stays committed unchanged; dev and heldout stay v1. The files hold ids only. Positive counts are printed, never written. `--check` fails if the committed files no longer match.
- **Scorer as second reader.** The agent scorer reads the labels itself, at scoring time, so the runner and the agent never see them.

**Why:** the agent must never see the answer key, directly or through a shared helper, and prompt tuning must not touch the alerts used for the headline. Splitting by scheme (not by alert) stops two alerts from one laundering scheme landing on both sides.

**Alternatives:**
- Random split by alert id: simpler, but one scheme could sit in both sets and leak.
- Labels inside the dataset files: easier scoring, but the files are public and the runner would carry the answer key.
- A single allowed reader for everything: forces the dataset builder and both scorers into one file.
