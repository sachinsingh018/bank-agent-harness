from modules.aml.rules.helpers import acct, txn
from modules.aml.rules.scatter_gather import scatter_gather
from modules.aml.rules.types import Txn


def burst(middles: int, start_id: int = 1, t0: float = 0, gap: float = 10) -> list[Txn]:
    txns: list[Txn] = []
    for k in range(middles):
        txns.append(txn(start_id + 2 * k, t0, "S", f"M{k}"))
        txns.append(txn(start_id + 2 * k + 1, t0 + gap, f"M{k}", "F"))
    return txns


def test_flags_three_middles() -> None:
    drafts = scatter_gather(burst(3))
    assert len(drafts) == 1
    d = drafts[0]
    assert d.detector == "scatter-gather"
    assert d.anchor == acct("S")
    assert d.txn_ids == (1, 2, 3, 4, 5, 6)
    assert sorted(set(d.roles.values())) == ["gather", "out"]
    assert d.roles[1] == "out" and d.roles[2] == "gather"
    assert d.params == {"distinct_middles": 3, "min_middles": 3, "window_hours": 96}
    assert d.account_count == 5
    assert "launder" not in d.summary.lower()


def test_two_middles_refused() -> None:
    assert scatter_gather(burst(2)) == []


def test_payment_before_receipt_refused() -> None:
    txns = burst(2) + [txn(20, 10, "S", "M9"), txn(21, 5, "M9", "F")]
    assert scatter_gather(txns) == []


def test_different_final_accounts_refused() -> None:
    txns = burst(2) + [txn(20, 0, "S", "M9"), txn(21, 10, "M9", "G")]
    assert scatter_gather(txns) == []


def test_final_equal_to_source_refused() -> None:
    txns = [txn(1 + 2 * k, 0, "S", f"M{k}") for k in range(3)]
    txns += [txn(10 + k, 5, f"M{k}", "S") for k in range(3)]
    assert scatter_gather(txns) == []


def test_window_boundary() -> None:
    assert len(scatter_gather(burst(3, gap=96))) == 1
    assert scatter_gather(burst(3, gap=96.01)) == []


def test_self_transfers_ignored() -> None:
    txns = burst(2) + [txn(20, 0, "S", "S"), txn(21, 5, "S", "F")]
    assert scatter_gather(txns) == []


def test_overlapping_bursts_merge() -> None:
    txns = burst(3) + burst(3, start_id=100, t0=20)
    drafts = scatter_gather(txns)
    assert len(drafts) == 1
    assert len(drafts[0].txn_ids) == 12


def test_far_apart_bursts_stay_separate() -> None:
    drafts = scatter_gather(burst(3) + burst(3, start_id=100, t0=500))
    assert len(drafts) == 2


def test_deterministic() -> None:
    txns = burst(4)
    assert scatter_gather(txns) == scatter_gather(list(reversed(txns)))


def test_early_gather_excluded_and_not_counted() -> None:
    # M2 pays F at hour 5 but only receives at hour 10, so M2 does not count.
    txns = burst(2) + [txn(20, 5, "M2", "F"), txn(21, 10, "S", "M2")]
    assert scatter_gather(txns) == []


def test_one_of_three_middles_with_only_early_payment_gives_no_alert() -> None:
    txns = burst(2) + [txn(20, 10, "S", "M2"), txn(21, 5, "M2", "F")]
    assert scatter_gather(txns) == []


def test_valid_pattern_with_early_extra_gather_drops_early_transfer() -> None:
    txns = [
        txn(1, 20, "S", "M0"),
        txn(2, 5, "M0", "F"),
        txn(3, 30, "M0", "F"),
        txn(4, 0, "S", "M1"),
        txn(5, 10, "M1", "F"),
        txn(6, 0, "S", "M2"),
        txn(7, 10, "M2", "F"),
    ]
    drafts = scatter_gather(txns)
    assert len(drafts) == 1
    assert drafts[0].txn_ids == (1, 3, 4, 5, 6, 7)
    assert 2 not in drafts[0].roles


def test_receipt_and_payment_at_same_time_count() -> None:
    assert len(scatter_gather(burst(3, gap=0))) == 1
