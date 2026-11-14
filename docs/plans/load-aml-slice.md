# Plan: load the AML slice into Postgres

Plan only. No code is written yet. Needs owner approval before any build starts.

## Goal

Load `data/aml_slice_hi_small.csv` (25,493 rows, 522 laundering) into the existing Neon Postgres database, with a migration, a loader that is safe to rerun, and a check that proves the load is correct. This is the first step of M1 (Data).

## Ground rules

- One Neon database exists. There is no separate dev and prod, so anything we run affects the same database the deployed API uses.
- The connection string is the environment variable `DATABASE_URL` (Codespaces secret, also set on Render). It already uses the `postgresql+psycopg://` prefix and ends with `?sslmode=require`.
- Do not create or ask for any new connection strings.
- The agent is read-only. The migration and loader are admin scripts run by a human, outside the agent.

## 1. Tables

Three tables. The answer key (labels) lives in its own table.

Why separate: `laundering`, `attempt_id` and `attempt_type` are the answer key for evals. The agent must never see them. If they sit in `transactions`, one careless `SELECT *` in a tool leaks them. In their own table, the agent's tools simply never query it.

### accounts

- `bank` (integer) and `acct` (text) together form the primary key.
- Filled from every distinct sender and receiver in the file.

### transactions (visible to the agent and the rules layer)

| Column | Type | Notes |
|---|---|---|
| txn_id | integer, primary key | Row position in the file, 1 to 25,493. The file is sorted and tracked in git, so IDs are stable. Agent citations need stable IDs. |
| ts | timestamp without time zone | Parsed from `YYYY/MM/DD HH:MM`. The source gives no time zone, so none is invented. |
| from_bank, from_acct | integer, text | Foreign key to accounts. |
| to_bank, to_acct | integer, text | Foreign key to accounts. |
| amt_recv, amt_paid | numeric | Never float, because this is money. Check the file for the most decimal places before fixing the scale. |
| cur_recv, cur_paid, fmt | text | |
| row_hash | text, unique | SHA-256 of the 10 non-label source columns. Used for rerun safety. The label is left out on purpose, so the hash cannot be used to guess the answer key. |

Indexes: sender account, receiver account, `ts`. The rules layer will need per-account history.

### transaction_labels (the answer key)

| Column | Type | Notes |
|---|---|---|
| txn_id | integer, primary key | Foreign key to transactions. |
| is_laundering | boolean | Converted from 0 and 1. |
| attempt_id | integer, nullable | The CSV stores it as `101.0`. Convert to `101`. |
| attempt_type | text, nullable | Limited to the 8 scheme families. |

Blank handling: a blank `attempt_id` or `attempt_type` becomes a real `NULL`, never an empty string and never 0.

A check constraint requires both to be `NULL` when `is_laundering` is false, and both to be set when it is true.

## 2. Alembic migration

- `alembic.ini` and a `migrations/` folder at the repo root. SQLAlchemy models and a small database helper go in `harness/db/`, shared by the API, tools and loader.
- `alembic.ini` contains no URL. `migrations/env.py` reads `os.environ["DATABASE_URL"]` and nothing else. If it is missing, it fails with "DATABASE_URL is not set" and no value.
- Migration 0001 only creates the three tables and indexes. It drops nothing. Its downgrade drops only those three tables.
- Migrations are run by hand from Codespaces. To confirm before relying on it: the Render free tier has no pre-deploy step.
- Because there is a single database, check Neon's restore window in the Neon console before the first run.

## 3. Loader (safe to rerun)

Location: `data/load_aml_slice.py`. `.gitignore` currently ignores every file in `data/`, so add `!data/*.py`. Also add `data` to the pytest `testpaths` and to the CI mypy command.

Steps:

1. Validate first, before connecting. Parse and check the whole CSV: 13 columns, no missing required fields, valid numbers and timestamps, no duplicate `row_hash` values, and the label rules hold. Any failure stops the run. Nothing is silently dropped.
2. Load in one transaction, all or nothing. Insert accounts, then transactions, then labels, in batches of about 1,000 with `INSERT ... ON CONFLICT DO NOTHING`. A crash halfway leaves no partial data.
3. Rerun safety. Read the existing `txn_id` and `row_hash` pairs. If a `txn_id` exists with a different hash, the file has changed since the last load, so stop and refuse to mix versions. Otherwise a second run inserts 0 rows. The hash leaves out the label, so a change to a label alone is not caught here. `verify` catches it by comparing the database with the CSV.
4. `--dry-run` does step 1 only, with no connection at all.
5. Neon free pauses idle databases, so the first connection can be slow. Retry the connect a few times.

## 4. Secret handling rules

- The loader, migration and any helper read `DATABASE_URL` from the environment only. It is never an argument, never in a file and never in a log.
- Neither the migration nor the loader prints the URL. At most they say the target is "remote database" or "local database".
- Database errors are caught and replaced with a generic message, raised without the original error attached, because connection errors can include a host or user name.
- No SQL echo or debug logging. Alembic logging stays at the default level.
- The loader refuses to run if the URL does not use the `postgresql+psycopg` prefix, and does not print the URL when it refuses.
- Tests never touch Neon, and CI has no database secret. Unit tests cover CSV parsing, hashing and validation with no database. A test against a real database is optional later and needs owner approval.

## 5. Verification

A separate read-only `verify` command reads through a fresh connection after the load and exits with an error on any mismatch. It checks:

- 25,493 rows in `transactions` and 25,493 in `transaction_labels`.
- 522 laundering rows and 24,971 normal rows.
- 8 schemes in each of the 8 families (64 in total), with transfer counts per family of BIPARTITE 41, CYCLE 47, FAN-IN 59, FAN-OUT 54, GATHER-SCATTER 116, RANDOM 27, SCATTER-GATHER 114, STACK 64.
- Earliest and latest `ts` are 2022/09/01 00:00 and 2022/09/17 19:40.
- The sums of `amt_paid` and `amt_recv` in the database equal the sums computed from the CSV, which proves no money values were rounded.
- Zero orphaned accounts or labels, and all constraints hold.
- A second run of the loader changes no counts.

## 6. Free tier limits

- Neon: the data is small. The CSV is 2.3 MB, so the three tables with indexes should be roughly 10 to 20 MB. As far as recalled, the free plan allows about 0.5 GB of storage per project, but limits change, so check the Neon console. The practical effect is that the database pauses when idle and wakes slowly.
- Render: does not matter for the load, because the loader runs from Codespaces. It matters later, since the free service sleeps when idle and its first request is slow. Keep the API's connection pool small for Neon.

## 7. Build order

Use the builder, tester and reviewer loop. Two commits:

1. Models, database helper and the Alembic migration.
2. Loader, `verify` command and unit tests.

Run the migration and the load against Neon only after the owner approves.

## Open decisions for the owner

1. Approve this plan or request changes.
2. Labels: a separate `transaction_labels` table (recommended) or label columns inside `transactions`.
3. Allow the `.gitignore` change `!data/*.py`.
