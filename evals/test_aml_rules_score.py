"""Scorer tests. The pure ones use small hand-built data. The DB one is local and rolled back."""

import re
from collections import Counter
from pathlib import Path

from sqlalchemy import Engine, func, select

from evals import aml_rules_score as scorer
from evals.aml_rules_score import Label, ScoredAlert
from harness.db.models import Alert, TransactionLabel
from modules.aml.load_txns import load_txns_on
from modules.aml.rules.fan import fan_in, fan_out
from modules.aml.store import delete_alerts_for_version_on, write_alerts_on

NORMAL = Label(False, None, None)


def _laundering(attempt_id: int, family: str = "FAN-OUT") -> Label:
    return Label(True, attempt_id, family)


def _alert(alert_id: str, detector: str, txns: set[int]) -> ScoredAlert:
    return ScoredAlert(alert_id, detector, frozenset(txns))


def _labels() -> dict[int, Label]:
    """Scheme A (FAN-OUT 1): txns 1-4. Scheme B (CYCLE 2): txns 5-6. Scheme C (RANDOM 3): txn 7.
    Normal: 10-19."""
    labels: dict[int, Label] = {t: NORMAL for t in range(10, 20)}
    labels.update({t: _laundering(1) for t in (1, 2, 3, 4)})
    labels.update({t: _laundering(2, "CYCLE") for t in (5, 6)})
    labels[7] = _laundering(3, "RANDOM")
    return labels


def test_exactly_half_is_true_and_one_fewer_is_false() -> None:
    labels = _labels()
    half = _alert("a", "fan-out", {1, 2, 10, 11})
    fewer = _alert("b", "fan-out", {1, 10, 11, 12})
    assert scorer.classify_alert(half, labels) == (True, True)
    assert scorer.classify_alert(fewer, labels) == (False, True)
    assert scorer.classify_alert(_alert("c", "fan-in", {10, 11}), labels) == (False, False)


def test_loose_versus_strict_counts() -> None:
    labels = _labels()
    alerts = [
        _alert("a", "fan-out", {1, 2, 10, 11}),
        _alert("b", "fan-out", {1, 10, 11, 12}),
        _alert("c", "fan-in", {10, 11}),
    ]
    block = scorer.score(alerts, labels)[0]["overall"]["alerts"]
    assert block["raised"] == 3
    assert block["true_strict"] == 1
    assert block["true_loose"] == 2
    assert block["false_strict"] == 2
    assert block["precision_strict"] == round(1 / 3, 6)
    assert block["precision_loose"] == round(2 / 3, 6)


def test_scheme_caught_at_exactly_half_and_not_below() -> None:
    schemes = scorer.build_schemes(_labels())
    two_of_four = [_alert("a", "fan-out", {1, 2, 10})]
    one_of_four = [_alert("a", "fan-out", {1, 10})]
    only_a = {k: v for k, v in schemes.items() if k == ("FAN-OUT", 1)}
    assert scorer.recall_metrics(two_of_four, only_a)["caught"] == 1
    below = scorer.recall_metrics(one_of_four, only_a)
    assert below["caught"] == 0
    assert below["touched"] == 1


def test_touched_versus_caught_needs_one_single_alert() -> None:
    schemes = scorer.build_schemes(_labels())
    only_a = {k: v for k, v in schemes.items() if k == ("FAN-OUT", 1)}
    # Two alerts each hold one transfer: together half, but no single alert covers half.
    alerts = [_alert("a", "fan-out", {1}), _alert("b", "fan-in", {2})]
    result = scorer.recall_metrics(alerts, only_a)
    assert result["caught"] == 0
    assert result["touched"] == 1
    assert result["transfer_recall"] == 0.5


def test_detectable_filter_and_scopes() -> None:
    labels = _labels()
    report = scorer.build_report("t", [_alert("a", "fan-out", {1, 2})], labels)
    assert report["dataset"]["schemes"] == 3
    assert report["dataset"]["detectable_schemes"] == 2
    recall = report["metrics"]["overall"]["recall"]
    assert recall["all"]["schemes"] == 3
    assert recall["detectable"]["schemes"] == 2
    assert recall["all"]["laundering_transfers"] == 7
    for scope in ("all", "detectable", "development", "held_out", "held_out_detectable"):
        assert scope in recall
    # With so few schemes, all are development, none held out.
    assert recall["held_out"]["schemes"] == 0
    assert recall["held_out"]["scheme_recall_caught"] is None
    assert recall["held_out"]["transfer_recall"] is None


def test_per_detector_and_per_family_numbers() -> None:
    labels = _labels()
    alerts = [
        _alert("a", "fan-out", {1, 2, 3}),
        _alert("b", "fan-out", {10, 11}),
        _alert("c", "cycle", {5, 6}),
    ]
    metrics = scorer.score(alerts, labels)[0]
    fo = metrics["per_detector"]["fan-out"]
    assert fo["alerts"]["raised"] == 2
    assert fo["alerts"]["true_strict"] == 1
    assert fo["recall"]["all"]["caught"] == 1
    assert fo["false_alerts_per_caught_scheme"] == 1.0
    cy = metrics["per_detector"]["cycle"]
    assert cy["alerts"]["precision_strict"] == 1.0
    assert cy["recall"]["all"]["caught"] == 1
    fam = metrics["per_family"]
    assert sorted(fam) == ["CYCLE", "FAN-OUT", "RANDOM"]
    assert fam["FAN-OUT"]["attributed_alerts"]["raised"] == 1
    assert fam["FAN-OUT"]["recall"]["all"]["caught"] == 1
    assert fam["CYCLE"]["recall"]["all"]["caught"] == 1
    assert fam["RANDOM"]["attributed_alerts"]["raised"] == 0
    for blk in fam.values():
        assert "precision_loose" not in blk and "false_alerts_per_caught_scheme" not in blk
        assert "precision_loose" not in blk["attributed_alerts"]
        assert "alerts" not in blk


def test_notes_state_attribution_rule() -> None:
    report = scorer.build_report("t", [_alert("a", "fan-out", {1, 2})], _labels())
    text = " ".join(report["notes"])
    assert "at least one laundering transfer" in text
    assert "ties alphabetical" in text
    assert "appear in no family" in text
    assert "do not describe false alarms" in text
    assert "Per-family recall uses all alerts" in text


def test_none_for_empty_denominators() -> None:
    labels = _labels()
    metrics = scorer.score([], labels)[0]["overall"]
    assert metrics["alerts"]["raised"] == 0
    assert metrics["alerts"]["precision_strict"] is None
    assert metrics["alerts"]["precision_loose"] is None
    assert metrics["false_alerts_per_caught_scheme"] is None
    assert metrics["recall"]["all"]["caught"] == 0
    empty = scorer.score([_alert("a", "fan-out", {10})], {10: NORMAL})[0]["overall"]
    assert empty["recall"]["all"]["scheme_recall_caught"] is None
    assert empty["recall"]["all"]["transfer_recall"] is None


def test_false_alerts_per_caught_scheme_none_when_no_caught() -> None:
    metrics = scorer.score([_alert("a", "fan-out", {10, 11})], _labels())[0]["overall"]
    assert metrics["alerts"]["false_strict"] == 1
    assert metrics["false_alerts_per_caught_scheme"] is None


def _full_schemes(ids: list[int] | None = None) -> list[scorer.Scheme]:
    out: list[scorer.Scheme] = []
    for family in ("BIPARTITE", "CYCLE", "FAN-IN", "FAN-OUT"):
        for a in ids if ids is not None else range(8):
            out.append((family, a))
    return out


def test_split_is_4_and_4_per_family_disjoint() -> None:
    split = scorer.split_schemes(_full_schemes())
    assert split.development.isdisjoint(split.held_out)
    assert len(split.development) == 16
    assert len(split.held_out) == 16
    for family in ("BIPARTITE", "CYCLE", "FAN-IN", "FAN-OUT"):
        assert sum(1 for f, _ in split.development if f == family) == 4
        assert sum(1 for f, _ in split.held_out if f == family) == 4


def test_split_is_deterministic_and_order_independent() -> None:
    schemes = _full_schemes()
    first = scorer.split_schemes(schemes)
    assert scorer.split_schemes(schemes) == first
    assert scorer.split_schemes(list(reversed(schemes))) == first
    shuffled = schemes[1::2] + schemes[0::2]
    assert scorer.split_schemes(shuffled) == first


def test_split_of_64_schemes_gives_32_and_32() -> None:
    families = [
        "BIPARTITE",
        "CYCLE",
        "FAN-IN",
        "FAN-OUT",
        "GATHER-SCATTER",
        "RANDOM",
        "SCATTER-GATHER",
        "STACK",
    ]
    schemes = [(f, 100 * i + a) for i, f in enumerate(families) for a in range(8)]
    split = scorer.split_schemes(schemes)
    assert split.counts() == {"development": 32, "held_out": 32}
    per_family = Counter(f for f, _ in split.development)
    assert set(per_family.values()) == {4}


def test_json_is_byte_identical_and_well_formed() -> None:
    labels = _labels()
    alerts = [_alert("a", "fan-out", {1, 2, 3}), _alert("c", "cycle", {5, 6})]
    one = scorer.render_json(scorer.build_report("v1", alerts, labels))
    two = scorer.render_json(scorer.build_report("v1", list(reversed(alerts)), dict(labels)))
    assert one == two
    assert one.endswith("}\n")
    assert '\n  "alert_set_hash"' in one
    assert chr(0x2014) not in one and chr(0x2013) not in one
    report = scorer.build_report("v1", alerts, labels)
    assert report["split"]["seed"] == 42
    assert any("not tuned" in n for n in report["notes"])
    assert any("Structuring and volume spike" in n for n in report["notes"])


def test_module_has_no_write_statements() -> None:
    text = Path(scorer.__file__).read_text(encoding="utf-8")
    for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"):
        assert re.search(rf"\b{word}\b", text) is None, word
    assert "insert(" not in text and "delete(" not in text
    assert ".execute(" in text and text.count("conn.execute(") == 3


def test_score_from_connection_rolled_back(local_engine: Engine) -> None:
    with local_engine.connect() as conn:
        trans = conn.begin()
        try:
            txns = load_txns_on(conn)
            drafts = fan_out(txns) + fan_in(txns)
            assert drafts
            delete_alerts_for_version_on(conn, "score-test")
            write_alerts_on(conn, drafts, "score-test")
            labelled = conn.execute(select(func.count()).select_from(TransactionLabel)).scalar()
            report = scorer.score_from_connection(conn, "score-test")
            assert report["alert_total"] == len(drafts)
            assert report["dataset"]["transactions"] == labelled
            overall = report["metrics"]["overall"]
            assert overall["alerts"]["raised"] == len(drafts)
            prec = overall["alerts"]["precision_strict"]
            assert prec is None or 0.0 <= prec <= 1.0
            assert overall["recall"]["all"]["schemes"] == report["dataset"]["schemes"]
            assert (
                report["split"]["development"] + report["split"]["held_out"]
                == (report["dataset"]["schemes"])
            )
            # Scoring itself wrote nothing: the alert count is unchanged.
            count = conn.execute(
                select(func.count()).select_from(Alert).where(Alert.ruleset_version == "score-test")
            ).scalar()
            assert count == len(drafts)
        finally:
            trans.rollback()
    with local_engine.connect() as conn:
        left = conn.execute(
            select(func.count()).select_from(Alert).where(Alert.ruleset_version == "score-test")
        ).scalar()
        assert left == 0
