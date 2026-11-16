# 0004: How the AML tools are kept away from the labels table

**What:** Tools in `tools/aml/` read only four tables (`transactions`, `accounts`, `alerts`, `alert_transactions`) through one door, `tools/aml/db.py`. The door has these layers:
1. Every connection is a read-only transaction that is always rolled back.
2. A guard on every executed statement inspects the SQLAlchemy statement object, not SQL text. It accepts only a plain `Select` made of known node types, the four real table objects (matched by identity), the functions `count`, `sum`, `min` and `max`, a short list of standard operators, and a cast to `Date`. Anything it does not recognise is refused. Raw SQL strings are refused except the exact `SET TRANSACTION READ ONLY`. Compiled text containing a semicolon, a comment or non-ASCII is refused. The labels table name appears once in the code, as a deny constant.
3. Static tests scan `tools/`: an import allowlist, bans on raw SQL, `text()`, `literal_column`, driver access (`raw_connection`, `.cursor(`, `.connection`), creating engines, removing event hooks and custom operators. Only `db.py` is exempt, and only for the deny constant.
4. Tests run every tool with sample inputs and check the statements it ran, the outputs for label-like keys, and that results are identical with the labels table emptied.

The only other raw statement allowed is the exact `SET LOCAL statement_timeout = 5000`, sent by `open_readonly` right after the read-only switch and built only from the `STATEMENT_TIMEOUT_MS` constant in `caps.py`: it lets Postgres cancel a slow read (and free its connection) before the 10 s tool timeout, and `SET LOCAL` lasts only for the transaction, so it works through Neon poolers that reject startup options.

**Why a tree walk and not text matching:** the first version parsed SQL text. Review found a new way past it each round (string and comment tricks, parenthesised table lists, functions that run their own query, `table x` forms). Text parsing cannot be made safe. The statement object can be checked exactly.

**Difference from docs/plans/mcp-tools.md:** the plan described a text-matching runtime guard and a file called `registry.py`. The code uses the tree-walking guard above and the file is `query_registry.py`.

**Accepted residual risks** (all need deliberate, unusual effort, and statements in `tools/` are written only by our own code with agent inputs as bound parameters):
- Someone edits `db.py` itself (guard, allowlists, housekeeping set) or the test allowlists.
- Someone adds a node class, function or operator to the allowlists. That is a deliberate change and needs the exact-set tests updated.
- The guard uses SQLAlchemy 2.x internals. A 2.x upgrade could break it. It fails closed (loud, not a leak). A 3.x upgrade is forced to be reviewed by a test.
- Runtime monkeypatching of SQLAlchemy or the event system, and dynamic attribute access, are not caught by name-based scans.
- Network or file modules from the standard library are still importable in tools code. None are database modules.
- `open_readonly` does not check that its engine has the guard. Only `make_tool_engine` installs it, and other tools code cannot create engines.
- Calling `commit()` or `rollback()` in tools code ends the read-only transaction. Writes still cannot happen, because the guard passes only reads.
- `literal_execute` parameters would render values inline. Our code never uses them.

**Not done:** a separate read-only database role for the tools. It needs a new connection string and is deferred (see the session log).

**Alternatives:** relying on the database role alone (needs a new secret), or a SQL parsing library (adds a dependency and the same fail-open risk).
