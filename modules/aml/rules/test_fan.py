from datetime import datetime, timedelta
from decimal import Decimal

from modules.aml.rules.fan import fan_in, fan_out
from modules.aml.rules.ids import make_alert_id
from modules.aml.rules.types import Txn

T0 = datetime(2022, 9, 1, 0, 0)


def txn(i: int, hours: float, src: tuple[int, str], dst: tuple[int, str]) -> Txn:
    return Txn(
        txn_id=i,
        ts=T0 + timedelta(hours=hours),
        from_bank=src[0],
        from_acct=src[1],
        to_bank=dst[0],
        to_acct=dst[1],
        amt_recv=Decimal("10"),
        cur_recv="USD",
        amt_paid=Decimal("10"),
        cur_paid="USD",
        fmt="ACH",
    )


HUB = (1, "HUB")


def spread(n: int, step_hours: float, start_id: int = 1) -> list[Txn]:
    """Hub sends to n different receivers, step_hours apart."""
    return [txn(start_id + k, k * step_hours, HUB, (2, f"R{k}")) for k in range(n)]


def test_fan_out_flags_six_in_96h() -> None:
    drafts = fan_out(spread(6, 19))  # span 95h
    assert len(drafts) == 1
    d = drafts[0]
    assert d.detector == "fan-out"
    assert d.anchor == HUB
    assert d.txn_ids == (1, 2, 3, 4, 5, 6)
    assert set(d.roles.values()) == {"out"}
    assert d.params == {
        "distinct_counterparties": 6,
        "min_counterparties": 6,
        "window_hours": 96,
    }
    assert d.account_count == 7
    assert "launder" not in d.summary.lower()


def test_fan_out_ignores_five() -> None:
    assert fan_out(spread(5, 10)) == []


def test_fan_out_ignores_six_over_ten_days() -> None:
    assert fan_out(spread(6, 48)) == []


def test_fan_out_ignores_self_transfers() -> None:
    txns = spread(5, 1) + [txn(99, 6, HUB, HUB)]
    assert fan_out(txns) == []


def test_fan_out_needs_distinct_receivers() -> None:
    txns = [txn(k + 1, k, HUB, (2, "SAME")) for k in range(10)]
    assert fan_out(txns) == []


def test_fan_out_one_burst_one_alert() -> None:
    drafts = fan_out(spread(20, 2))  # 38h, many overlapping windows
    assert len(drafts) == 1
    assert len(drafts[0].txn_ids) == 20


def test_fan_out_repeat_receivers_inside_burst_are_included() -> None:
    txns = spread(6, 1) + [txn(50, 2.5, HUB, (2, "R0"))]
    drafts = fan_out(txns)
    assert len(drafts) == 1
    assert 50 in drafts[0].txn_ids
    assert drafts[0].params["distinct_counterparties"] == 6


def test_fan_out_chained_windows_merge() -> None:
    # 12 receivers 24h apart: each 96h window holds only 5, so nothing fires.
    assert fan_out(spread(12, 24)) == []
    # 12 receivers 16h apart: windows hold 7 and chain into one burst of 12.
    drafts = fan_out(spread(12, 16))
    assert len(drafts) == 1
    assert len(drafts[0].txn_ids) == 12


def test_fan_out_two_separate_bursts() -> None:
    txns = spread(6, 1) + spread(6, 1, start_id=100)
    shifted = [
        txn(t.txn_id, 500 + (t.ts - T0).total_seconds() / 3600, HUB, t.receiver) for t in txns[6:]
    ]
    drafts = fan_out(txns[:6] + shifted)
    assert len(drafts) == 2
    assert drafts[0].window_start < drafts[1].window_start


def test_fan_in_flags_six_senders() -> None:
    txns = [txn(k + 1, k * 19, (2, f"S{k}"), HUB) for k in range(6)]
    drafts = fan_in(txns)
    assert len(drafts) == 1
    assert drafts[0].detector == "fan-in"
    assert drafts[0].anchor == HUB
    assert set(drafts[0].roles.values()) == {"in"}
    assert drafts[0].params["distinct_counterparties"] == 6


def test_fan_in_near_misses() -> None:
    five = [txn(k + 1, k, (2, f"S{k}"), HUB) for k in range(5)]
    slow = [txn(k + 1, k * 48, (2, f"S{k}"), HUB) for k in range(6)]
    selfs = five + [txn(99, 6, HUB, HUB)]
    assert fan_in(five) == []
    assert fan_in(slow) == []
    assert fan_in(selfs) == []


def test_fan_detectors_do_not_cross() -> None:
    assert fan_in(spread(8, 1)) == []


def test_deterministic_and_stable_ids() -> None:
    txns = spread(8, 3) + [txn(100 + k, k * 2, (3, "H2"), (4, f"Q{k}")) for k in range(7)]
    first = fan_out(txns)
    assert first == fan_out(list(reversed(txns)))
    assert [d.anchor for d in first] == [(1, "HUB"), (3, "H2")]
    ids = [make_alert_id("v1", d.detector, d.txn_ids) for d in first]
    assert ids == [make_alert_id("v1", d.detector, d.txn_ids) for d in fan_out(txns)]
    assert len(set(ids)) == 2


def _at(hours: list[float]) -> list[Txn]:
    return [txn(k + 1, h, HUB, (2, f"R{k}")) for k, h in enumerate(hours)]


def test_fan_out_flags_exactly_96h_span() -> None:
    drafts = fan_out(_at([0, 20, 40, 60, 80, 96]))
    assert len(drafts) == 1
    assert drafts[0].txn_ids == (1, 2, 3, 4, 5, 6)


def test_fan_out_ignores_span_just_over_96h() -> None:
    assert fan_out(_at([0, 20, 40, 60, 80, 96 + 1 / 60])) == []


def test_fan_out_tied_timestamps_make_one_alert() -> None:
    drafts = fan_out(_at([5, 5, 5, 5, 5, 5]))
    assert len(drafts) == 1
    assert drafts[0].txn_ids == (1, 2, 3, 4, 5, 6)
    assert drafts[0].params["distinct_counterparties"] == 6


def test_fan_in_flags_exactly_96h_span() -> None:
    rows = [txn(k + 1, h, (2, f"S{k}"), HUB) for k, h in enumerate([0, 20, 40, 60, 80, 96])]
    drafts = fan_in(rows)
    assert len(drafts) == 1
    assert drafts[0].detector == "fan-in"
    rows[-1] = txn(6, 96 + 1 / 60, (2, "S5"), HUB)
    assert fan_in(rows) == []
