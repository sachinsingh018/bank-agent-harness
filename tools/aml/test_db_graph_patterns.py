"""DB tests for the graph and pattern tools. Local Postgres only (see conftest).

Two kinds. Tests on the fixture alerts (committed under their own ruleset version and removed
again) use real data. Tests on a ledger insert their own rows with high ids inside one write
transaction that is always rolled back, so nothing is committed."""

import re
from collections import Counter
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, insert

from harness.db.models import Account
from modules.aml.rules.registry import DETECTOR_ORDER
from modules.aml.rules.types import AlertDraft
from tools.aml import caps
from tools.aml.conftest import ToolData
from tools.aml.db import StatementRecorder, install_guard, open_readonly
from tools.aml.models import AccountRef, GraphInput, GraphResult, PatternCheckInput
from tools.aml.paging import ToolFailure, json_size
from tools.aml.patterns import NOTE, run_pattern_check
from tools.aml.queries import get_transaction_graph
from tools.aml.query_registry import QUERY_REGISTRY
from tools.aml.test_db_edge_cases import BANK, T0, Ledger

FORBIDDEN_KEYS = {"is_laundering", "laundering", "attempt_id", "attempt_type"}
ALLOWED = {"transactions", "accounts", "alerts", "alert_transactions"}
HUB_NEIGHBOURS = 60
NAMES = (
    ["HUB", "ROOT", "A", "B", "C", "D", "E", "F", "G", "S1", "S2", "LONE", "P"]
    + [f"N{i:02d}" for i in range(HUB_NEIGHBOURS)]
    + [f"R{i}" for i in range(6)]
    + [f"Q{i}" for i in range(6)]
)


def _ref(name: str) -> AccountRef:
    return AccountRef(bank=BANK, acct=name)


def _minutes(i: int) -> datetime:
    return T0 + timedelta(minutes=i)


@pytest.fixture
def ledger(local_engine: Engine) -> Iterator[Ledger]:
    with local_engine.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(insert(Account), [{"bank": BANK, "acct": n} for n in NAMES])
            yield Ledger(conn)
        finally:
            trans.rollback()


def _graph(conn: Connection, name: str, **kwargs: Any) -> GraphResult:
    return get_transaction_graph(conn, GraphInput(bank=BANK, acct=name, **kwargs))


def _node_names(result: GraphResult) -> list[str]:
    return [n.account.acct for n in result.nodes]


def _check_pattern(conn: Connection, pattern: str, name: str, **kwargs: Any) -> Any:
    return run_pattern_check(
        conn,
        PatternCheckInput(pattern=pattern, bank=BANK, acct=name, **kwargs),  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------- graph on real data


def test_graph_on_a_fixture_alert_account(tool_engine: Engine, tool_data: ToolData) -> None:
    s = tool_data.samples
    a = s.account
    with open_readonly(tool_engine) as conn:
        one = get_transaction_graph(conn, GraphInput(bank=a.bank, acct=a.acct))
        two = get_transaction_graph(conn, GraphInput(bank=a.bank, acct=a.acct, hops=2))
        windowed = get_transaction_graph(
            conn, GraphInput(bank=a.bank, acct=a.acct, hops=2, start=s.start, end=s.end)
        )
    for result in (one, two, windowed):
        assert result.root == a
        root_key = (a.bank, a.acct)
        accounts = [(n.account.bank, n.account.acct) for n in result.nodes]
        assert accounts[0] == root_key
        assert accounts.count(root_key) == 1
        assert len(set(accounts)) == len(accounts) <= caps.MAX_GRAPH_NODES
        assert len(result.edges) <= caps.MAX_GRAPH_EDGES
        assert result.total_nodes_found >= len(result.nodes)
        assert result.total_edges_found >= len(result.edges)
        cut = result.total_nodes_found > len(result.nodes) or result.total_edges_found > len(
            result.edges
        )
        assert result.truncated or not cut
        if cut:
            assert result.truncated
        ids = [e.txn_id for e in result.edges]
        assert len(set(ids)) == len(ids)
        keys = [(e.ts, e.txn_id) for e in result.edges]
        assert keys == sorted(keys)
        known = set(accounts)
        for e in result.edges:
            ends = [(x.bank, x.acct) for x in (e.from_account, e.to_account)]
            assert ends[0] != ends[1]
            assert ends[0] in known and ends[1] in known
        assert json_size(result) <= caps.MAX_RESPONSE_CHARS
    assert one.hops == 1 and two.hops == 2
    assert two.total_nodes_found >= one.total_nodes_found
    assert (windowed.window_start, windowed.window_end) == (s.start, s.end)
    assert one.window_start is None
    assert all(s.start <= e.ts <= s.end for e in windowed.edges)
    assert windowed.total_edges_found <= two.total_edges_found


def test_graph_unknown_account_is_not_found(tool_engine: Engine, tool_data: ToolData) -> None:
    with open_readonly(tool_engine) as conn:
        with pytest.raises(ToolFailure) as info:
            get_transaction_graph(conn, GraphInput(bank=987654, acct="NOPE"))
    assert info.value.code == "not_found"


# ------------------------------------------------------------------ graph on a ledger


def test_hub_with_many_neighbours_is_truncated_and_busiest_come_first(
    ledger: Ledger,
) -> None:
    for i in range(HUB_NEIGHBOURS):
        ledger.add("HUB", f"N{i:02d}", _minutes(i))
    for i in range(5):  # the last five neighbours are the busiest
        for j in range(3):
            ledger.add(f"N{HUB_NEIGHBOURS - 1 - i:02d}", "HUB", _minutes(100 + 10 * i + j))
    install_guard(ledger.conn)
    result = _graph(ledger.conn, "HUB")
    names = _node_names(result)
    assert names[0] == "HUB"
    assert len(names) == len(set(names)) == caps.MAX_GRAPH_NODES == 40
    assert result.total_nodes_found == HUB_NEIGHBOURS + 1
    assert result.truncated
    busiest = {f"N{HUB_NEIGHBOURS - 1 - i:02d}" for i in range(5)}
    assert busiest <= set(names)
    # the remaining places go to accounts with the same count, in account order
    assert names[6:] == [f"N{i:02d}" for i in range(34)]
    hub = result.nodes[0]
    assert hub.transfers_out == HUB_NEIGHBOURS
    assert hub.transfers_in == 15
    assert result.total_edges_found == (len(names) - 1) + 15
    assert all(r.from_account != r.to_account for r in result.edges)


def test_edge_cap_and_size_cap(ledger: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(130):
        ledger.add("HUB", "P", _minutes(i))
    install_guard(ledger.conn)
    # With the response size cap out of the way the edge cap alone applies.
    monkeypatch.setattr("tools.aml.paging.MAX_RESPONSE_CHARS", 10**9)
    result = _graph(ledger.conn, "HUB")
    assert len(result.edges) == caps.MAX_GRAPH_EDGES == 120
    assert result.total_edges_found == 130
    assert result.truncated
    assert result.total_nodes_found == 2
    monkeypatch.undo()
    # With the real response size cap, fewer edges fit and the result says so.
    sized = _graph(ledger.conn, "HUB")
    assert 0 < len(sized.edges) < 120
    assert sized.total_edges_found == 130
    assert sized.truncated
    assert json_size(sized) <= caps.MAX_RESPONSE_CHARS


def test_small_caps_truncate_with_exact_counts(
    ledger: Ledger, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = {"A": 3, "B": 2, "C": 1, "D": 1}
    minute = 0
    for name, count in plan.items():
        for _ in range(count):
            ledger.add("HUB", name, _minutes(minute))
            minute += 1
    install_guard(ledger.conn)
    monkeypatch.setattr("tools.aml.queries.MAX_GRAPH_NODES", 4)
    monkeypatch.setattr("tools.aml.queries.MAX_GRAPH_EDGES", 3)
    result = _graph(ledger.conn, "HUB")
    assert _node_names(result) == ["HUB", "A", "B", "C"]
    assert result.total_nodes_found == 5
    assert result.total_edges_found == 6
    assert [e.txn_id for e in result.edges] == [
        e.txn_id for e in sorted(result.edges, key=lambda e: (e.ts, e.txn_id))
    ]
    assert len(result.edges) == 3
    assert {e.to_account.acct for e in result.edges} == {"A"}
    assert result.truncated
    counts = {n.account.acct: (n.transfers_in, n.transfers_out) for n in result.nodes}
    assert counts == {"HUB": (0, 7), "A": (3, 0), "B": (2, 0), "C": (1, 0)}


def test_nothing_cut_means_not_truncated(ledger: Ledger) -> None:
    ledger.add("HUB", "A", _minutes(0))
    ledger.add("A", "HUB", _minutes(1))
    ledger.add("HUB", "B", _minutes(2))
    install_guard(ledger.conn)
    result = _graph(ledger.conn, "HUB")
    assert _node_names(result) == ["HUB", "A", "B"]
    assert (result.total_nodes_found, result.total_edges_found) == (3, 3)
    assert not result.truncated
    assert len(result.edges) == 3


def test_self_transfers_are_excluded(ledger: Ledger) -> None:
    ledger.add("HUB", "HUB", _minutes(0))
    ledger.add("HUB", "HUB", _minutes(1))
    ledger.add("HUB", "A", _minutes(2))
    ledger.add("A", "A", _minutes(3))
    install_guard(ledger.conn)
    result = _graph(ledger.conn, "HUB", hops=2)
    assert _node_names(result) == ["HUB", "A"]
    assert result.total_edges_found == 1
    assert len(result.edges) == 1
    assert result.nodes[0].transfers_in == 0 and result.nodes[0].transfers_out == 1
    assert result.nodes[1].transfers_in == 1 and result.nodes[1].transfers_out == 0
    assert not result.truncated


def test_hops_window_and_a_root_with_no_transfers(ledger: Ledger) -> None:
    ledger.add("ROOT", "A", _minutes(0))
    ledger.add("A", "B", _minutes(10))
    ledger.add("B", "C", _minutes(20))
    ledger.add("ROOT", "B", _minutes(30))
    ledger.add("B", "ROOT", _minutes(31))
    install_guard(ledger.conn)
    conn = ledger.conn
    one = _graph(conn, "ROOT")
    assert _node_names(one) == ["ROOT", "B", "A"]  # B first: two transfers with the root
    two = _graph(conn, "ROOT", hops=2)
    assert sorted(_node_names(two)) == ["A", "B", "C", "ROOT"]
    assert _node_names(two).count("ROOT") == 1
    assert two.total_nodes_found == 4
    assert not two.truncated
    assert two.total_edges_found == 5
    assert two.hops == 2
    no_c = _graph(conn, "ROOT", hops=2, end=_minutes(15))
    assert "C" not in _node_names(no_c)
    assert no_c.total_edges_found == 2
    assert no_c.window_end == _minutes(15)
    late = _graph(conn, "ROOT", hops=2, start=_minutes(25))
    assert sorted(_node_names(late)) == ["B", "ROOT"]
    lone = _graph(conn, "LONE", hops=2)
    assert _node_names(lone) == ["LONE"]
    assert (lone.total_nodes_found, lone.total_edges_found, lone.truncated) == (1, 0, False)


def test_two_hops_grow_only_from_the_busiest_neighbours(ledger: Ledger) -> None:
    minute = 0
    names = ["A", "B", "C", "D", "E", "F"]
    for i, name in enumerate(names):
        for _ in range(len(names) - i):  # A is the busiest, F the quietest
            ledger.add("ROOT", name, _minutes(minute))
            minute += 1
    ledger.add("F", "G", _minutes(minute))  # F is not among the five busiest
    ledger.add("A", "P", _minutes(minute + 1))
    install_guard(ledger.conn)
    result = _graph(ledger.conn, "ROOT", hops=2)
    found = set(_node_names(result))
    assert "P" in found and "G" not in found
    assert result.truncated  # six neighbours but only five were expanded
    assert result.total_nodes_found == 1 + 6 + 1


# ------------------------------------------------------------- pattern check on a ledger


def test_fan_out_check_on_a_ledger_matches_the_alert_shape(ledger: Ledger) -> None:
    ids = [ledger.add("HUB", f"R{i}", _minutes(i)) for i in range(6)]
    install_guard(ledger.conn)
    result = _check_pattern(ledger.conn, "fan-out", "HUB")
    assert result.pattern == "fan-out"
    assert result.total_matches == 1 and not result.truncated
    match = result.matches[0]
    assert match.txn_ids == sorted(ids) and match.txn_count == 6
    assert match.anchor == _ref("HUB")
    assert match.params["distinct_counterparties"] == 6
    assert result.neighbourhood_hops == 2
    assert result.note == NOTE and "not an independent opinion" in result.note
    other = _check_pattern(ledger.conn, "fan-in", "HUB")
    assert other.total_matches == 0


def test_only_matches_involving_the_account_are_returned(ledger: Ledger) -> None:
    ledger.add("ROOT", "S1", _minutes(0))
    ledger.add("ROOT", "S2", _minutes(1))
    s1 = [ledger.add("S1", f"R{i}", _minutes(10 + i)) for i in range(6)]
    for i in range(6):
        ledger.add("S2", f"Q{i}", _minutes(20 + i))
    install_guard(ledger.conn)
    root = _check_pattern(ledger.conn, "fan-out", "ROOT")
    assert root.total_matches == 0 and root.matches == []
    assert root.transfers_examined == 2 + 6 + 6
    s1_result = _check_pattern(ledger.conn, "fan-out", "S1")
    assert s1_result.total_matches == 1
    assert s1_result.matches[0].txn_ids == sorted(s1)
    assert s1_result.matches[0].anchor == _ref("S1")


def test_account_with_nothing_returns_zero_matches(ledger: Ledger) -> None:
    ledger.add("A", "B", _minutes(0))
    install_guard(ledger.conn)
    for pattern in ("fan-out", "fan-in", "cycle", "scatter-gather", "gather-scatter"):
        quiet = _check_pattern(ledger.conn, pattern, "LONE")
        assert quiet.total_matches == 0 and quiet.matches == [] and not quiet.truncated
        assert quiet.transfers_examined == 0
        assert quiet.note
        busy = _check_pattern(ledger.conn, pattern, "A")
        assert busy.total_matches == 0 and busy.transfers_examined == 1


def test_short_cycle_is_found_and_unknown_account_is_not_found(ledger: Ledger) -> None:
    ids = [
        ledger.add("A", "B", _minutes(0)),
        ledger.add("B", "C", _minutes(1)),
        ledger.add("C", "A", _minutes(2)),
    ]
    ledger.add("A", "A", _minutes(3))
    install_guard(ledger.conn)
    result = _check_pattern(ledger.conn, "cycle", "B")
    assert result.total_matches == 1
    assert result.matches[0].txn_ids == sorted(ids)
    with pytest.raises(ToolFailure) as info:
        run_pattern_check(ledger.conn, PatternCheckInput(pattern="cycle", bank=987654, acct="NOPE"))
    assert info.value.code == "not_found"


def test_window_limits_what_is_examined(ledger: Ledger) -> None:
    for i in range(6):
        ledger.add("HUB", f"R{i}", _minutes(i))
    install_guard(ledger.conn)
    narrow = _check_pattern(ledger.conn, "fan-out", "HUB", start=_minutes(0), end=_minutes(2))
    assert narrow.transfers_examined == 3 and narrow.total_matches == 0


def test_too_large_when_the_cap_is_small(ledger: Ledger, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(6):
        ledger.add("HUB", f"R{i}", _minutes(i))
    ledger.add("R0", "R1", _minutes(10))
    install_guard(ledger.conn)
    for cap in (3, 6):  # the account's own transfers, then the whole neighbourhood
        monkeypatch.setattr("tools.aml.patterns.MAX_PATTERN_TXNS", cap)
        with pytest.raises(ToolFailure) as info:
            _check_pattern(ledger.conn, "fan-out", "HUB")
        assert info.value.code == "too_large"
        assert "narrow" in info.value.message
    monkeypatch.setattr("tools.aml.patterns.MAX_PATTERN_TXNS", 7)
    assert _check_pattern(ledger.conn, "fan-out", "HUB").transfers_examined == 7


def test_match_ids_and_match_count_are_capped(ledger: Ledger) -> None:
    for i in range(6):
        for j in range(10):  # 60 transfers in one burst
            ledger.add("HUB", f"R{i}", _minutes(i * 10 + j))
    install_guard(ledger.conn)
    result = _check_pattern(ledger.conn, "fan-out", "HUB")
    match = result.matches[0]
    assert match.txn_count == 60
    assert len(match.txn_ids) == caps.MAX_MATCH_TXN_IDS
    assert match.txn_ids == sorted(match.txn_ids)
    assert result.truncated


def test_fan_in_check_on_a_ledger_finds_the_exact_transfers(ledger: Ledger) -> None:
    ids = [ledger.add(f"R{i}", "HUB", _minutes(i)) for i in range(6)]
    install_guard(ledger.conn)
    result = _check_pattern(ledger.conn, "fan-in", "HUB")
    assert result.total_matches == 1 and not result.truncated
    match = result.matches[0]
    assert match.detector == "fan-in"
    assert match.txn_ids == sorted(ids) and match.txn_count == 6
    assert match.anchor == _ref("HUB")
    assert match.params["distinct_counterparties"] == 6


def test_scatter_gather_check_on_a_ledger_finds_the_exact_transfers(ledger: Ledger) -> None:
    ids = [ledger.add("S1", m, _minutes(i)) for i, m in enumerate(["A", "B", "C"])]
    ids += [ledger.add(m, "D", _minutes(10 + i)) for i, m in enumerate(["A", "B", "C"])]
    install_guard(ledger.conn)
    result = _check_pattern(ledger.conn, "scatter-gather", "S1")
    assert result.total_matches == 1 and not result.truncated
    match = result.matches[0]
    assert match.detector == "scatter-gather"
    assert match.txn_ids == sorted(ids) and match.txn_count == 6
    assert match.anchor == _ref("S1")
    assert match.params["distinct_middles"] == 3
    # a middle account is part of the match, so its check finds the same transfers
    middle = _check_pattern(ledger.conn, "scatter-gather", "A")
    assert [m.txn_ids for m in middle.matches] == [sorted(ids)]


def test_gather_scatter_check_on_a_ledger_finds_the_exact_transfers(ledger: Ledger) -> None:
    ids = [ledger.add(f"R{i}", "HUB", _minutes(i)) for i in range(3)]
    ids += [ledger.add("HUB", f"Q{i}", _minutes(10 + i)) for i in range(3)]
    install_guard(ledger.conn)
    result = _check_pattern(ledger.conn, "gather-scatter", "HUB")
    assert result.total_matches == 1 and not result.truncated
    match = result.matches[0]
    assert match.detector == "gather-scatter"
    assert match.txn_ids == sorted(ids) and match.txn_count == 6
    assert match.anchor == _ref("HUB")
    assert match.params["distinct_in"] == 3 and match.params["distinct_out"] == 3


def test_a_four_account_loop_is_found_and_a_five_account_loop_is_missed(
    ledger: Ledger,
) -> None:
    # ROOT -> A -> B -> C -> ROOT: every transfer touches ROOT or an account next to ROOT
    four = [
        ledger.add("ROOT", "A", _minutes(0)),
        ledger.add("A", "B", _minutes(1)),
        ledger.add("B", "C", _minutes(2)),
        ledger.add("C", "ROOT", _minutes(3)),
    ]
    install_guard(ledger.conn)
    found = _check_pattern(ledger.conn, "cycle", "ROOT")
    assert found.total_matches == 1
    assert found.matches[0].txn_ids == sorted(four)
    assert "more than 4 accounts may be missed" in found.note


def test_a_five_account_loop_through_the_account_is_not_reported(ledger: Ledger) -> None:
    # ROOT -> A -> B -> C -> D -> ROOT: C -> D is between two accounts that are two steps away
    # from ROOT, so it is never loaded and the loop is missed. This pins that known limit.
    for i, (src, dst) in enumerate(
        [("ROOT", "A"), ("A", "B"), ("B", "C"), ("C", "D"), ("D", "ROOT")]
    ):
        ledger.add(src, dst, _minutes(i))
    install_guard(ledger.conn)
    result = _check_pattern(ledger.conn, "cycle", "ROOT")
    assert result.total_matches == 0 and result.matches == []
    assert result.transfers_examined == 4  # the C to D transfer was not loaded
    assert "more than 4 accounts may be missed" in result.note
    assert "does not prove" in result.note


# ---------------------------------------------------- agreement with the rules layer


# Fixture alerts whose neighbourhood is over the cap, as (detector, txn_ids). Empty on purpose:
# a too_large result fails the agreement test unless the exact case is listed here.
EXPECTED_TOO_LARGE: set[tuple[str, tuple[int, ...]]] = set()


def _check_agreement(conn: Connection, draft: AlertDraft) -> None:
    """The check must reproduce the alert. too_large fails unless the case is listed above."""
    assert draft.anchor is not None
    inp = PatternCheckInput(
        pattern=draft.detector,  # type: ignore[arg-type]
        bank=draft.anchor[0],
        acct=draft.anchor[1],
        start=draft.window_start,
        end=draft.window_end,
    )
    try:
        result = run_pattern_check(conn, inp)
    except ToolFailure as failure:
        assert failure.code == "too_large"
        assert (
            (draft.detector, draft.txn_ids) in EXPECTED_TOO_LARGE
        ), f"unexpected too_large for {draft.detector} alert on {draft.anchor}"
        return
    assert (draft.detector, draft.txn_ids) not in EXPECTED_TOO_LARGE, "listed case now works"
    expected = list(draft.txn_ids)
    same = [m for m in result.matches if m.txn_count == len(expected)]
    found = [m for m in same if m.txn_ids == expected[: caps.MAX_MATCH_TXN_IDS]]
    assert found, f"{draft.detector} alert on {draft.anchor} not reproduced"
    assert found[0].detector == draft.detector
    assert found[0].window_start == draft.window_start
    assert found[0].window_end == draft.window_end
    assert found[0].summary == draft.summary
    assert not FORBIDDEN_KEYS & _keys(result.model_dump(mode="json"))


def _spread(drafts: list[AlertDraft]) -> list[AlertDraft]:
    """The smallest 2 and the largest 2 alerts by transfer count, in a fixed order."""
    ordered = sorted(drafts, key=lambda d: (len(d.txn_ids), d.txn_ids))
    picked = ordered[:2] + ordered[-2:]
    return [d for i, d in enumerate(picked) if d not in picked[:i]]


def _keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            found.add(str(k))
            found |= _keys(v)
    elif isinstance(value, list):
        for v in value:
            found |= _keys(v)
    return found


def test_pattern_check_agrees_with_the_fixture_alerts(
    tool_engine: Engine, tool_data: ToolData
) -> None:
    by_detector: dict[str, list[AlertDraft]] = {}
    for d in tool_data.drafts:
        by_detector.setdefault(d.detector, []).append(d)
    assert by_detector, "no alerts to compare with"
    # Detectors with no fixture alert are covered by the hand-built ledger tests above (fan-out
    # and fan-in, cycle, scatter-gather, gather-scatter). Anything else missing fails loudly.
    hand_built = {"fan-out", "fan-in", "cycle", "scatter-gather", "gather-scatter"}
    without_alerts = [d for d in DETECTOR_ORDER if d not in by_detector]
    assert set(without_alerts) <= hand_built, f"detectors with no coverage: {without_alerts}"
    assert set(by_detector) <= set(DETECTOR_ORDER)
    assert sorted(by_detector) == sorted(
        d for d in DETECTOR_ORDER if d not in without_alerts
    ), f"detectors with fixture alerts: {sorted(by_detector)}"
    checked: Counter[str] = Counter()
    with open_readonly(tool_engine) as conn:
        for detector in DETECTOR_ORDER:
            for draft in _spread(by_detector.get(detector, [])):
                _check_agreement(conn, draft)
                checked[detector] += 1
    # every detector that has fixture alerts was checked, none skipped
    for detector in by_detector:
        assert checked[detector] >= 1, detector


def test_pattern_check_response_is_small(tool_engine: Engine, tool_data: ToolData) -> None:
    s = tool_data.samples
    with open_readonly(tool_engine) as conn:
        for pattern in ("fan-out", "fan-in", "cycle", "scatter-gather", "gather-scatter"):
            try:
                result = run_pattern_check(
                    conn,
                    PatternCheckInput(
                        pattern=pattern,
                        bank=s.account.bank,
                        acct=s.account.acct,
                        start=s.start,
                        end=s.end,
                    ),
                )
            except ToolFailure as failure:
                assert failure.code == "too_large"
                continue
            assert json_size(result) <= caps.MAX_RESPONSE_CHARS
            assert len(result.matches) <= caps.MAX_PATTERN_MATCHES
            assert all(len(m.txn_ids) <= caps.MAX_MATCH_TXN_IDS for m in result.matches)
            assert result.total_matches >= len(result.matches)


# -------------------------------------------------- statements touch only four tables


def test_every_tool_touches_only_the_four_allowed_tables(
    tool_engine: Engine, tool_data: ToolData
) -> None:
    for entry in QUERY_REGISTRY:
        recorder = StatementRecorder()
        recorder.install(tool_engine)
        with open_readonly(tool_engine) as conn:
            for args in entry.build_args(tool_data.samples):
                try:
                    entry.call(conn, args)
                except ToolFailure as failure:
                    assert failure.code == "too_large", entry.name
        statements = [s for s in recorder.statements if s.lower().startswith("select")]
        if entry.needs_connection:
            assert statements, entry.name
        else:
            assert not statements, entry.name
        for sql in statements:
            tables = set(re.findall(r"\b(?:from|join)\s+([a-z_]+)", sql.lower()))
            assert tables, (entry.name, sql)
            assert tables <= ALLOWED, (entry.name, tables)
        assert not any("transaction_" + "labels" in s.lower() for s in recorder.statements)
