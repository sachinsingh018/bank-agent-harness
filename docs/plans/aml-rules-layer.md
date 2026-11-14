# Plan: AML rules layer

Plan only. No code is written yet. Needs owner approval before any build starts.

## Goal

Build the deterministic detectors that scan the transactions and raise alerts, store those alerts, and measure how good they are against the answer key. This is the second piece of M1 (Data). The rules-only results become the baseline the agent must beat.

## Plain-English background

A bank cannot read every transfer, so software scans them for shapes that look like money laundering. When a shape matches, it raises an **alert**: a flag saying "a person should look at this". Most alerts turn out to be harmless. These are called **false positives** (a false alarm). The agent we build later reads each alert and decides: close it as harmless, or escalate it for a human to review.

Two numbers describe any detector:
- **Precision:** of the alerts it raised, how many were real laundering. Low precision means many false alarms and wasted analyst time.
- **Recall:** of the real laundering, how much it caught. Low recall means criminals slipped through.

Real banks tune rules to over-alert slightly, because missing real laundering is worse than a false alarm. We do the same.

## What the data looks like

These facts come from the 25,493-row slice and ground the design. I looked at the shape of the answer-key schemes to pick sensible starting values. Later tuning must not repeat that on the held-out schemes (see section 6).

- The slice covers 17 days (2022-09-01 to 2022-09-17).
- There are 64 laundering schemes, 8 in each of 8 families.
- 12 of the 64 schemes are a single transfer: 4 of the fan-out, 1 of the fan-in, 2 of the bipartite and 5 of the random schemes. A single transfer between two accounts has no shape, so no pattern-based rule can tell it from a normal transfer. See the recall ceiling below.
- Most multi-transfer schemes play out within about 4 days (up to about 96 hours). Gather-scatter schemes take longer, about 6 to 8 days.
- Normal traffic is mostly quiet: about 8,400 accounts never send to more than 1 different account within a day. About 67 accounts send to 5 or more different accounts within a day, and about 29 to 8 or more. So a threshold of 6 to 8 separates most normal accounts from bursts.
- Amounts are in 15 currencies, with no exchange rates in the data. 604 rows pay in one currency and are received in another. Adding amounts across currencies means nothing, so the rules use counts, timing and shape, and only compare amounts within one currency.
- In US dollars, the median laundering transfer is about 10,200 and the median normal transfer is about 1,100. Large amounts alone are a weak signal, because most large transfers are normal.

### Recall ceiling

At most 52 of the 64 schemes (about 81 percent) can be caught by pattern rules. Results are reported two ways: all 64 schemes, and the 52 "detectable" schemes with 2 or more transfers. The agent faces the same ceiling unless it finds other evidence, so reporting both keeps the comparison fair.

## 1. Detectors

Each detector has a plain-English meaning and a starting setting. The starting values are guesses, versioned as ruleset `v1`, and tuned on the development schemes only (section 6). Every detector ignores transfers where an account pays itself.

### First wave (5 detectors)

| Detector | What it flags, in plain English | Starting setting (v1) |
|---|---|---|
| fan-out | One account sends money to many different accounts in a short time. Criminals do this to split a large sum into pieces that are harder to follow. | 6 or more different receivers within 96 hours |
| fan-in | Many different accounts send money to one account in a short time. This is the mirror image: scattered money is collected into one place. | 6 or more different senders within 96 hours |
| cycle | Money leaves an account, passes through a few others and comes back to the start, like a loop. Real commerce rarely sends money in a circle, and a loop can make dirty money look like earned income. | A loop of 2 to 6 accounts, each step after the one before, whole loop within 96 hours |
| scatter-gather | A source splits money across several middle accounts, and they all pay one final account. Fan-out followed by fan-in, meeting at the end. | One source, 3 or more middle accounts that each received from it and pay the same final account, within 96 hours |
| gather-scatter | A hub account collects money from several accounts and then pays it out to several others. Fan-in followed by fan-out through one account, which acts as a switchboard. | One hub receiving from 3 or more accounts and paying 3 or more others, with the payouts after the pay-ins, within 192 hours |

Grouping rule: when one burst triggers a detector, it makes one alert covering the whole burst, not one alert per sliding window. Otherwise a single fan-out would create dozens of near-copies.

### Second wave (later, after the first wave is measured)

| Detector | Plain English |
|---|---|
| stack | Money arrives in an account and leaves again quickly, in a chain of 3 or more steps with similar amounts in the same currency. Layers of pass-through accounts hide where the money started. |
| bipartite | Several senders each pay several of the same receivers, a many-to-many web between two groups. |
| structuring | Several transfers just under a reporting limit (US 10,000) from one account within a day. Banks must report large cash movements, so criminals sometimes stay just below the limit. US dollars only. |
| volume spike | An account suddenly moves far more than its own usual level. The slice covers only 17 days, so "usual" is thin. |

Notes on the second wave:
- Structuring and volume spike are in the brief, but the answer key has no family with those names. Their alerts would all count as false alarms in scoring, which is unfair. We evaluate them separately and say so in the report.
- Structuring will be noisy here. In the slice, 8 laundering and 80 normal US dollar transfers fall between 9,000 and 9,999.
- The random family has no detector. Most random schemes are a single transfer.

## 2. Alert record and where alerts are stored

Alerts are derived data stored in Postgres, in two new tables created by Alembic migration 0002, in the same way as the earlier tables.

### alerts

| Column | Notes |
|---|---|
| alert_id | Text primary key. Deterministic: a short hash of the ruleset version, detector and sorted transaction ids. Rerunning the same rules gives the same ids, so reruns are safe. |
| ruleset_version | Text, for example `v1`. Changing a threshold means a new version, and old alerts stay for comparison. |
| detector | Text, limited to the detector names. |
| anchor_bank, anchor_acct | Nullable. The main account: the hub for a fan, a canonical account for a cycle. |
| window_start, window_end | Timestamps of the first and last transfer in the alert. |
| txn_count, account_count | Integers. |
| params | JSON with the thresholds used and the values measured, for example `{"distinct_receivers": 12, "window_hours": 96}`. |
| summary | One plain-English sentence built from params, such as "Account X sent to 12 different accounts within 61 hours". |
| created_at | Timestamp. |

### alert_transactions

| Column | Notes |
|---|---|
| alert_id | Foreign key to alerts. |
| txn_id | Foreign key to transactions. |
| role | Text, such as `out`, `in`, `hop`, `hub_in`, `hub_out`. |

Primary key is (alert_id, txn_id). A transfer can belong to several alerts.

Alerts hold no label information of any kind, and no "is suspicious" flag.

A script writes alerts: `uv run python -m modules.aml.run_rules --ruleset v1`, run by a human like the loader. It uses `ON CONFLICT DO NOTHING`, validates before writing, and has a `--dry-run` that prints only counts. A `--replace` option deletes and rebuilds only the alerts of the named ruleset version. This is allowed because alerts are derived data, not the audit log. The agent never runs this script.

## 3. Keeping labels away from the rules

The rules must never see `transaction_labels`. The design makes that structural, not a matter of discipline.

- **Pure rule functions.** The rules in `modules/aml/rules/` take a list of plain in-memory transaction records and return alert drafts. They contain no database code and import nothing from SQLAlchemy.
- **One transaction reader.** `modules/aml/load_txns.py` is the only code that reads the database for the rules. It selects named columns from `transactions` only, never `SELECT *`, and never imports the labels model.
- **One alert writer.** `modules/aml/store.py` writes `alerts` and `alert_transactions` only.
- **One scorer.** `evals/aml_rules_score.py` is the only code that reads labels. It reads, never writes, and only after alerts exist.

Tests that enforce this:
1. A code-scanning test fails if any file in the rules package or the transaction reader mentions `TransactionLabel` or `transaction_labels`.
2. A label-shuffle test runs the rules on the local devcontainer Postgres twice, once with the real labels and once with the labels randomly shuffled. The alert ids must be identical. This test never runs against Neon.
3. The rule functions are unit-tested on small hand-made transaction lists. Each detector gets a case it must flag and a near-miss it must not.

A separate read-only Postgres role for the rules would be stronger, but it needs a new connection string, so it is out of scope. The code and tests carry the guarantee for now.

## 4. Measuring the rules against the answer key

The scorer builds the truth from the labels afterward. For each alert, it looks at its transfers and counts how many are laundering.

- **True alert (strict):** at least half of its transfers are laundering. This avoids crediting a 20-transfer alert for containing one real transfer.
- **True alert (loose):** at least one transfer is laundering. Reported alongside, for context.
- **False alert:** everything else.

Metrics, reported overall, per detector and per scheme family:
- Alerts raised, true alerts and false alerts (strict and loose).
- **Precision (strict):** true alerts divided by all alerts.
- **Transfer recall:** the share of the 522 laundering transfers that appear in at least one alert.
- **Scheme recall:** the share of schemes where one alert covers at least half of the scheme's transfers ("caught"). Also reported: schemes that any alert touches.
- Both recall numbers on all 64 schemes and on the 52 detectable schemes.
- **False alerts per caught scheme:** how much noise it costs to catch one scheme.

Results are written to `evals/baselines/aml_rules_<ruleset_version>.json`, a small file with counts only, committed to git and versioned. Later the eval runner (M2) also stores results in the `eval_runs` and `eval_results` tables.

This is the baseline for the project's main claim. The agent is measured on the same alerts: how many false alerts it correctly closes, and how many true alerts it wrongly closes. Closing a true alert is the worst mistake.

The numbers apply to this slice only. The slice is 17 days with sampled normal traffic, so thresholds and results will not carry over to the full dataset.

## 5. How the agent receives an alert

The agent gets an alert id, not the whole database.

1. A case is created for each alert (the cases table comes in M2 and M3). The case holds the `alert_id`.
2. The harness gives the agent a short brief: alert id, detector name, ruleset version, anchor account, time window, counts, the one-sentence summary and the transfer ids. For long alerts it lists the first 50 and the total. The detector name is a rule output, so it is allowed. A real analyst sees it too. Nothing label-derived is included.
3. The agent investigates with read-only MCP tools: `get_alert` (the alert record and its transfers), `get_account_history`, `get_counterparties`, `get_transaction_graph` and `run_pattern_check`. Every tool reads only `transactions`, `accounts`, `alerts` and `alert_transactions`. The label-scanning test from section 3 also covers the tools.
4. The agent must answer with a schema-valid result: close or escalate, suspected typology, confidence, and a narrative where every claim cites transfer or account ids.
5. Deterministic verifiers check that every cited id exists and that any cited amount matches the stored amount. One retry, then human review.
6. An escalation needs human approval (four-eyes). The agent never writes anything itself.
7. For evals, the scorer compares each decision with the label-derived truth, using a frozen dataset manifest: alert ids with their truth, for one ruleset version. Changing the ruleset makes a new manifest.

## 6. Tuning without cheating

If we tune thresholds against all 64 schemes and then report the same 64, the results look better than they are. So:

- Split the 64 schemes with seed 42: 4 per family for development, 4 per family held out.
- Tune thresholds using only the development schemes plus all normal traffic.
- Report recall on the held-out schemes only (32 schemes). Precision uses all alerts and is labelled as such.
- A ruleset version is frozen before the held-out numbers are read. Any further change makes a new version and a new held-out reading, noted in the report.

## 7. Build order

Use the builder, tester and reviewer loop. Three commits:

1. Migration 0002 (the two alert tables), the transaction reader, the alert writer, the scanning and shuffle tests, and the fan-out and fan-in detectors with their tests.
2. Cycle, scatter-gather and gather-scatter detectors, and the `run_rules` script.
3. The scorer, the dev and held-out split, and the first baseline report.

All database tests run on the local devcontainer Postgres only. Running anything against Neon needs owner approval, as for the load.

## Open decisions for the owner

1. Approve the first-wave list of 5 detectors, with stack, bipartite, structuring and volume spike later.
2. Approve the strict definition of a true alert: at least half of its transfers are laundering.
3. Approve the dev and held-out split of the schemes.
4. Accept the recall ceiling: report recall on all 64 schemes and on the 52 detectable ones.
5. Approve storing alerts in two new Postgres tables, and the baseline report as a committed JSON file.
