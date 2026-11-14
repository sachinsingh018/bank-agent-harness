# Plan: read-only MCP tools for AML investigation

Plan only. No code is written yet. Needs owner approval before any build starts.

## Goal

Give the agent a small set of read-only tools to investigate one alert: look at the alert, the accounts involved and the transfers around them. The tools run as an MCP server (MCP is a standard way for a model to call tools) written with the MCP Python SDK, as the hard rules require.

## Plain-English background

When an analyst gets an alert, they do not read the whole database. They open the alert, look at the accounts, see who those accounts paid and who paid them, and decide whether it looks like laundering. The tools give the agent the same moves, one at a time, and no others.

Two ground rules shape everything:
- **Read-only.** The agent can look but never change anything.
- **No answer key.** The labels table (`transaction_labels`) says which transfers are really laundering. If the agent could read it, every score would be fake. So no tool may touch it, and tests prove it.

## 1. The tools

Eight tools. Every tool takes validated inputs and returns a typed result (Pydantic models), never free text. Amounts are returned as exact decimal strings with their currency. Totals are never added across currencies, because the data has 15 currencies and no exchange rates.

All list results share one shape: `items`, `total_count`, `returned`, `truncated` (true or false) and `next_cursor` (or none).

Account reference everywhere: `{bank: integer, acct: text}`.

Transfer record everywhere: `txn_id`, `ts`, `from` and `to` (account references), `amount_paid` and `amount_received` (value and currency), and `format` (for example Wire, Cheque).

| Tool | What it returns |
|---|---|
| `get_alert(alert_id)` | The alert: detector, ruleset version, anchor account, window, counts, the one-sentence summary and the measured parameters. Plus the first 25 of its transfers (transfer records with their role in the alert) and the total count. |
| `get_alert_transactions(alert_id, cursor, limit)` | The rest of an alert's transfers, a page at a time, for long alerts. |
| `get_transaction(txn_id)` | One transfer record. The agent uses it to double check a number before citing it. |
| `get_account(bank, acct)` | A summary of one account: first and last activity, number of transfers in and out, number of different counterparties each way, the currencies and formats it uses, and the busiest day. Cheap, so the agent starts here instead of reading history. |
| `get_account_history(bank, acct, start, end, direction, cursor, limit)` | The account's transfers in time order. Direction is `in`, `out` or `both`. |
| `get_counterparties(bank, acct, direction, start, end)` | The accounts this account dealt with, grouped: per counterparty, the number of transfers, first and last date, and totals per currency. Top 20 by number of transfers, with the full count of counterparties. |
| `get_transaction_graph(bank, acct, hops, start, end)` | The neighbourhood around an account: accounts as nodes and transfers as edges, 1 hop by default, at most 2. Capped (section 3). |
| `run_pattern_check(pattern, bank, acct, start, end)` | Runs one of the same detectors the rules layer uses (fan-out, fan-in, cycle, scatter-gather, gather-scatter) on the transfers around one account and returns what it finds, with transfer ids. Reuses the existing pure detector code, so it needs no new logic and agrees with how alerts are made. |

One more tool needs no database: `describe_detector(detector)` returns the plain-English meaning and the thresholds of a detector, so the agent knows why an alert fired. That makes nine entries in total, with eight reading the database.

Choices behind the list:
- Alert-centred. The agent is given an alert id, so `get_alert` is the entry point. There is no tool to list or search alerts. Choosing what to investigate is the harness's job.
- Summary before detail. `get_account` and `get_counterparties` answer most questions in a few lines, so the agent does not need thousands of rows.
- `screen_name` from the brief is left out. It needs sanctions data we have not loaded, and the accounts here have no names. It returns when the sanctions lists arrive.
- `run_pattern_check` reuses the detectors, so asking whether an account shows a cycle gets the same answer the alert gave. It is evidence about the surrounding accounts, not an independent opinion. The agent's prompt will say so.

Errors are small typed objects: `{code, message}` with codes `not_found`, `invalid_argument`, `too_large` and `internal`. A database failure always becomes `internal` with a generic message, never a raw error, so a connection string can never leak (same rule as the loader).

## 2. Keeping the tools away from the labels

This is a promise the tests must prove. Several layers, so no single mistake breaks it.

**Layer 1: a narrow door.** All database access goes through one module, `tools/aml/db.py`. It opens a connection in a read-only transaction (`SET TRANSACTION READ ONLY`), so writes fail at the database. Queries use the SQLAlchemy models `Transaction`, `Account`, `Alert` and `AlertTransaction` only.

**Layer 2: a runtime guard.** `db.py` installs a hook that inspects every statement before it runs and refuses it (raising an error) unless it is a plain read of the four allowed tables. This runs in production, not just in tests, so a future careless query fails loudly instead of leaking. It checks the query object, not SQL text, because text parsing could not be made safe (see docs/decisions/0004-tools-sql-guard.md).

**Layer 3: static tests.** A test reads every non-test file under `tools/` and fails if:
- it mentions `TransactionLabel` or `transaction_labels`
- it imports any model from `harness.db.models` other than the four allowed
- it uses raw SQL (`text(`, `exec_driver_sql`), reflection or `select *`

**Layer 4: tests that run every tool.** A registry lists every tool. A test goes through the registry with sample inputs and checks:
- every registered tool has sample inputs, so a new tool cannot skip the check
- the statements each tool ran touch only the four allowed tables (the hook records them)
- the guard really works: a deliberate query on the labels table through the same connection is refused
- an `INSERT` through the tool connection fails (read-only)
- no output contains the keys `is_laundering`, `laundering`, `attempt_id` or `attempt_type`
- with the labels table emptied inside a rolled-back transaction, every tool's output is identical

**Layer 5: the repo-wide guard.** The existing label guard test is extended so `tools/` is scanned, and `evals/aml_rules_score.py` stays the only non-test reader of labels.

Database tests run on the local Postgres only through `TEST_DATABASE_URL` and never read `DATABASE_URL`, as before. CI runs them on its throwaway Postgres.

Not done now: a separate read-only database role for the tools would be stronger still, but it needs a new connection string. It is noted as future hardening.

## 3. Keeping results small

A model that gets thousands of rows burns its budget and loses the thread, so every tool is capped. The numbers live in one config file, so they are easy to change.

| Cap | Value |
|---|---|
| Rows per call, default and maximum | 25 and 50 |
| Characters per response (JSON) | 16,000, about 4,000 tokens |
| Counterparties returned | top 20 by number of transfers |
| Graph hops | at most 2, default 1 |
| Graph size | at most 40 accounts and 120 transfers, busiest first |
| Transfers loaded for `run_pattern_check` | at most 2,000, else `too_large` |

Rules:
- Every list says how many matched in total, how many it returned and whether it was cut short. A cut result is never silent.
- If a response is over the character cap, items are dropped from the end until it fits, and `truncated` is set to true.
- Ordering is fixed (time, then `txn_id`), and the cursor is the last item seen (keyset paging), so pages never skip or repeat rows.
- Windows (`start`, `end`) are optional. With no window, the tool uses the whole range but is still capped.
- The harness also limits how many tool calls one case may make. That is part of the agent loop's step limit, and hitting it sends the case to human review.

## 4. Where things live

```
tools/aml/models.py      Pydantic inputs and outputs
tools/aml/caps.py        the caps above
tools/aml/db.py          read-only connection, runtime guard, generic errors
tools/aml/queries.py     the query functions (take a connection, return models)
tools/aml/query_registry.py  the list of query functions and their sample inputs for tests
tools/aml/server.py      the MCP server (stdio), thin wrapper over the registry
```

The query functions take a connection, like the scorer, so tests can call them inside a rolled-back transaction. The citation verifier (a later step) can reuse `get_transaction`, so "does this id exist and does the amount match" is checked by the same code the agent used.

The `mcp` package is added as a dependency, pinned. The latest is 2.3.0, and I do not know its API well yet, so the exact calls are confirmed against the installed package when we build, not assumed. `tools` is added to the CI mypy command.

## 5. How the agent will call the tools

This plan builds the server and tests it. The agent loop comes later, but the connection works like this:

1. At the start of a run, the harness launches the server as a child process (`uv run python -m tools.aml.server`) and talks to it over stdio, using the MCP client from the same SDK. The child gets only `DATABASE_URL` in its environment, nothing else.
2. The harness asks the server for its tool list and converts each tool's schema into the format the model needs, through the model adapter. Which tools a module offers is part of its ModuleSpec.
3. The model asks to call a tool with arguments. The harness validates them against the schema, calls the tool, and puts the typed result back into the conversation.
4. Each call has a timeout (10 seconds). A failure or timeout comes back as a typed error, counts as a step, and the agent may adjust. Repeated identical calls are detected and flagged to the agent.
5. Every call is logged: tool name, arguments, result size, whether it was truncated, and duration. Later this feeds the traces and the audit record. Accounts here are synthetic, so logging them is fine.
6. The server stays alive for the run and is shut down at the end.

The agent loop, the model adapter and the MCP client get their own plan.

## 6. Build order

Use the builder, tester and reviewer loop. Three commits, all database work on the local Postgres only:

1. Models, caps, the database door with the runtime guard, and the query functions for `get_alert`, `get_alert_transactions`, `get_transaction`, `get_account`, `get_account_history` and `get_counterparties`, with the static and runtime label guard tests.
2. `get_transaction_graph`, `run_pattern_check` and `describe_detector`, with the cap tests.
3. The registry, the MCP server and an end-to-end test that starts the server as a child process and calls every tool over stdio, a schema snapshot test so tool changes are deliberate, and the CI changes.

Nothing runs against Neon during the build. After the build, a read-only smoke test against Neon needs owner approval.

## Open decisions for the owner

1. Approve the nine tools (eight database tools plus `describe_detector`), with `screen_name` deferred.
2. Approve the caps: 25 rows by default and 50 at most, 16,000 characters, top 20 counterparties, 2 hops, 40 accounts and 120 transfers.
3. Approve adding the `mcp` package, pinned, and confirming its API at build time.
4. Approve the three-commit build order.
5. Note the future hardening idea: a separate read-only database role for the tools.
