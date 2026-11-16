"""All size and time caps for the AML tools, in one place."""

DEFAULT_ROWS = 25
MAX_ROWS = 50
MAX_RESPONSE_CHARS = 16000
MAX_COUNTERPARTIES = 20
MAX_GRAPH_HOPS = 2
DEFAULT_GRAPH_HOPS = 1
MAX_GRAPH_NODES = 40
MAX_GRAPH_EDGES = 120
GRAPH_EXPANSION_FANOUT = 5  # hop 2 grows only from this many of the busiest hop 1 accounts
MAX_PATTERN_TXNS = 2000
MAX_PATTERN_MATCHES = 10
MAX_MATCH_TXN_IDS = 50
MAX_SUM_TXN_IDS = 50  # transfer ids per sum_transfers call
TOOL_TIMEOUT_SECONDS = 10
# Database side limit for one statement, set per transaction with SET LOCAL in open_readonly.
# Below the 10 s tool timeout so a slow query is cancelled by Postgres and frees its connection
# before the client gives up on the call.
STATEMENT_TIMEOUT_MS = 5000
