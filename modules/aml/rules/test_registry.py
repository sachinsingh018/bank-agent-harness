from modules.aml.rules.helpers import txn
from modules.aml.rules.registry import DETECTOR_ORDER, run_all, run_all_with_stats


def test_runs_all_detectors_in_fixed_order() -> None:
    txns = [txn(1, 0, "A", "B"), txn(2, 1, "B", "C"), txn(3, 2, "C", "A")]
    txns += [txn(10 + k, 0, "S", f"R{k}") for k in range(6)]
    txns += [txn(30 + k, 0, f"P{k}", "H") for k in range(3)]
    txns += [txn(40 + k, 5, "H", f"Q{k}") for k in range(3)]
    drafts = run_all(txns)
    names = [d.detector for d in drafts]
    assert names == sorted(names, key=DETECTOR_ORDER.index)
    assert {"fan-out", "cycle", "gather-scatter"} <= set(names)
    assert run_all(txns) == drafts
    assert run_all_with_stats(txns).capped_starts == 0


def test_empty_input() -> None:
    assert run_all([]) == []
