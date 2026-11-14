import pytest

import modules.aml.rules.cycle as cycle_module
from modules.aml.rules.cycle import MAX_STEPS_PER_START, cycle, find_cycles
from modules.aml.rules.helpers import acct, txn
from modules.aml.rules.types import Txn


def loop(names: list[str], step: float, start_id: int = 1, t0: float = 0) -> list[Txn]:
    return [
        txn(start_id + k, t0 + k * step, names[k], names[(k + 1) % len(names)])
        for k in range(len(names))
    ]


def test_flags_a_three_account_loop() -> None:
    drafts = cycle(loop(["C", "A", "B"], 10))
    assert len(drafts) == 1
    d = drafts[0]
    assert d.detector == "cycle"
    assert d.anchor == acct("A")
    assert d.txn_ids == (1, 2, 3)
    assert set(d.roles.values()) == {"hop"}
    assert d.params == {"cycle_length": 3, "max_length": 6, "window_hours": 96}
    assert d.account_count == 3
    assert "launder" not in d.summary.lower()


def test_flags_two_account_loop() -> None:
    assert len(cycle(loop(["A", "B"], 5))) == 1


def test_six_accounts_ok_seven_refused() -> None:
    assert len(cycle(loop(list("ABCDEF"), 5))) == 1
    assert cycle(loop(list("ABCDEFG"), 5)) == []


def test_window_boundary() -> None:
    assert len(cycle(loop(["A", "B", "C"], 48))) == 1  # span exactly 96h
    assert cycle(loop(["A", "B", "C"], 48.01)) == []


def test_hops_out_of_time_order_refused() -> None:
    txns = [txn(1, 0, "A", "B"), txn(2, 20, "C", "A"), txn(3, 30, "B", "C")]
    assert cycle(txns) == []


def test_equal_timestamps_allowed() -> None:
    assert len(cycle(loop(["A", "B", "C"], 0))) == 1


def test_not_a_loop_refused() -> None:
    assert cycle([txn(1, 0, "A", "B"), txn(2, 1, "B", "C"), txn(3, 2, "C", "D")]) == []


def test_self_transfers_ignored() -> None:
    assert cycle([txn(1, 0, "A", "A"), txn(2, 1, "A", "A")]) == []


def test_overlapping_loops_over_same_accounts_merge() -> None:
    txns = loop(["A", "B", "C"], 10) + loop(["A", "B", "C"], 10, start_id=10, t0=5)
    drafts = cycle(txns)
    assert len(drafts) == 1
    assert drafts[0].txn_ids == (1, 2, 3, 10, 11, 12)


def test_separate_loops_stay_separate() -> None:
    txns = loop(["A", "B", "C"], 1) + loop(["A", "B", "C"], 1, start_id=10, t0=500)
    assert len(cycle(txns)) == 2


def test_deterministic() -> None:
    txns = loop(["A", "B", "C"], 10) + loop(["D", "E"], 3, start_id=20)
    assert cycle(txns) == cycle(list(reversed(txns)))


def test_cap_not_hit_on_tiny_loop() -> None:
    assert MAX_STEPS_PER_START > 0
    assert find_cycles(loop(["A", "B", "C"], 1)).capped_starts == 0


def test_cap_is_counted_and_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cycle_module, "MAX_STEPS_PER_START", 2)
    txns = loop(list("ABCDEF"), 1)
    first = find_cycles(txns)
    assert first.capped_starts > 0
    assert find_cycles(txns) == first
    assert find_cycles(list(reversed(txns))) == first
