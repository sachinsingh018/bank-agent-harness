from modules.aml.rules.gather_scatter import gather_scatter
from modules.aml.rules.helpers import acct, txn
from modules.aml.rules.types import Txn


def hub_case(
    n_in: int = 3, n_out: int = 3, in_at: float = 0, out_at: float = 50, start_id: int = 1
) -> list[Txn]:
    txns = [txn(start_id + k, in_at, f"P{k}", "H") for k in range(n_in)]
    txns += [txn(start_id + 100 + k, out_at, "H", f"Q{k}") for k in range(n_out)]
    return txns


def test_flags_valid_hub() -> None:
    drafts = gather_scatter(hub_case())
    assert len(drafts) == 1
    d = drafts[0]
    assert d.detector == "gather-scatter"
    assert d.anchor == acct("H")
    assert sorted(set(d.roles.values())) == ["hub_in", "hub_out"]
    assert d.params == {
        "distinct_in": 3,
        "distinct_out": 3,
        "min_in": 3,
        "min_out": 3,
        "window_hours": 192,
    }
    assert d.account_count == 7
    assert "launder" not in d.summary.lower()


def test_two_payers_refused() -> None:
    assert gather_scatter(hub_case(n_in=2)) == []


def test_two_payees_refused() -> None:
    assert gather_scatter(hub_case(n_out=2)) == []


def test_payouts_before_payins_refused() -> None:
    assert gather_scatter(hub_case(in_at=50, out_at=0)) == []


def test_window_boundary() -> None:
    assert len(gather_scatter(hub_case(out_at=192))) == 1
    assert gather_scatter(hub_case(out_at=192.01)) == []


def test_same_payer_repeated_does_not_count() -> None:
    txns = [txn(k, k, "P0", "H") for k in range(1, 4)]
    txns += [txn(10 + k, 10 + k, "H", f"Q{k}") for k in range(3)]
    assert gather_scatter(txns) == []


def test_self_transfers_ignored() -> None:
    txns = hub_case(n_in=2) + [txn(50, 0, "H", "H"), txn(51, 1, "H", "H")]
    assert gather_scatter(txns) == []


def test_overlapping_windows_merge() -> None:
    txns = hub_case() + hub_case(in_at=60, out_at=100, start_id=200)
    drafts = gather_scatter(txns)
    assert len(drafts) == 1
    assert len(drafts[0].txn_ids) == 12


def test_far_apart_stay_separate() -> None:
    txns = hub_case() + hub_case(in_at=1000, out_at=1050, start_id=200)
    assert len(gather_scatter(txns)) == 2


def test_deterministic() -> None:
    txns = hub_case(4, 4)
    assert gather_scatter(txns) == gather_scatter(list(reversed(txns)))
