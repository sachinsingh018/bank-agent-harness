"""Dataset builder tests. Pure ones use hand-built data. The DB one is local and rolled back."""

import json
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine

from evals import make_aml_datasets as mk
from evals.aml_rules_score import Label, SchemeSplit, ScoredAlert
from modules.aml.load_txns import load_txns_on
from modules.aml.rules.fan import fan_in, fan_out
from modules.aml.store import delete_alerts_for_version_on, write_alerts_on

NORMAL = Label(False, None, None)
SPLIT = SchemeSplit(
    development=frozenset({("FAN-OUT", 1)}),
    held_out=frozenset({("FAN-OUT", 2)}),
)


def _labels() -> dict[int, Label]:
    """Txns 1-4: dev scheme. Txns 5-8: held-out scheme. 10-19 normal."""
    labels: dict[int, Label] = {t: NORMAL for t in range(10, 20)}
    labels.update({t: Label(True, 1, "FAN-OUT") for t in (1, 2, 3, 4)})
    labels.update({t: Label(True, 2, "FAN-OUT") for t in (5, 6, 7, 8)})
    return labels


def _alert(alert_id: str, txns: set[int]) -> ScoredAlert:
    return ScoredAlert(alert_id, "fan-out", frozenset(txns))


def test_majority_dev_goes_to_dev() -> None:
    assert mk.assign_alert(_alert("a", {1, 2, 5, 10}), _labels(), SPLIT) == "dev"


def test_majority_held_out_goes_to_heldout() -> None:
    assert mk.assign_alert(_alert("a", {1, 5, 6, 10}), _labels(), SPLIT) == "heldout"


def test_tie_goes_to_heldout() -> None:
    assert mk.assign_alert(_alert("a", {1, 2, 5, 6}), _labels(), SPLIT) == "heldout"


def test_no_laundering_uses_hash_parity() -> None:
    sides = set()
    for i in range(40):
        alert_id = f"AL-{i}"
        side = mk.assign_alert(_alert(alert_id, {10, 11}), _labels(), SPLIT)
        assert side == mk.parity_side(alert_id)
        sides.add(side)
    assert sides == {"dev", "heldout"}
    # Even first hex digit of sha256("42:<id>") means dev.
    assert mk.parity_side("AL-0") == (
        "dev" if int(mk._hex("42:AL-0")[0], 16) % 2 == 0 else "heldout"
    )


def _ordered(n: int = 30) -> tuple[list[str], list[str]]:
    dev = [f"AL-{i:02d}" for i in range(n)]
    return dev, sorted(dev, key=lambda a: (mk._hex(a), a))


def test_smoke_v2_takes_early_and_late_strict_and_early_negatives() -> None:
    dev, ordered = _ordered()
    # Strict positives last in hash order, so a plain first 10 would miss them.
    early_strict = ordered[-6:-3]
    late_strict = ordered[-3:]
    strict = frozenset(early_strict + late_strict)
    early_negatives = ordered[10:16]
    early = frozenset(early_strict + early_negatives)
    smoke = mk.pick_smoke(dev, strict, early)
    assert len(smoke) == 10 and list(smoke) == sorted(smoke)
    assert set(smoke) & set(early_strict) == set(early_strict[:2])
    assert set(smoke) & set(late_strict) == {late_strict[0]}
    assert set(smoke) & set(early_negatives) == set(early_negatives[:4])
    assert set(ordered[:3]) <= set(smoke)  # the fill, in hash order


def test_smoke_v2_uses_what_exists_when_scarce() -> None:
    dev, ordered = _ordered()
    assert set(mk.pick_smoke(dev, frozenset(), frozenset())) == set(ordered[:10])
    one = frozenset({ordered[20]})
    assert set(mk.pick_smoke(dev, one, one)) == {ordered[20], *ordered[:9]}
    late_only = frozenset({ordered[25], ordered[26]})
    smoke = set(mk.pick_smoke(dev, late_only, frozenset()))
    assert smoke == {ordered[25], *ordered[:9]}
    assert len(mk.pick_smoke(dev[:4], frozenset(), frozenset())) == 4


def test_only_smoke_version_is_bumped() -> None:
    assert mk.DATASET_VERSIONS == {"dev": "v1", "heldout": "v1", "smoke": "v2"}
    assert mk.dataset_path("smoke").name == "aml_alerts_smoke_v2.json"
    assert mk.dataset_path("dev").name == "aml_alerts_dev_v1.json"
    assert json.loads(mk.render_dataset("smoke", ["a"], "v1"))["version"] == "v2"


def test_build_is_deterministic_and_a_partition() -> None:
    labels = _labels()
    alerts = [
        _alert("x1", {1, 2, 3}),
        _alert("x2", {5, 6, 7}),
        _alert("x3", {10, 11}),
        _alert("x4", {12}),
        _alert("x5", {4, 8}),
    ]
    ends = {a.alert_id: datetime(2022, 9, 1) for a in alerts}
    one = mk.build_datasets(alerts, labels, ends)
    two = mk.build_datasets(list(reversed(alerts)), dict(labels), dict(ends))
    assert one == two
    assert set(one.dev) | set(one.heldout) == {a.alert_id for a in alerts}
    assert not set(one.dev) & set(one.heldout)
    assert set(one.smoke) <= set(one.dev)
    assert mk.render_all(one, "v1") == mk.render_all(two, "v1")


def test_rendered_file_has_only_ids_and_metadata() -> None:
    body = json.loads(mk.render_dataset("dev", ["b", "a"], "v1"))
    assert body == {
        "dataset": "dev",
        "version": "v1",
        "ruleset_version": "v1",
        "alert_ids": ["a", "b"],
        "created_by": "evals/make_aml_datasets.py",
        "split_seed": 42,
    }


def test_summary_warns_on_few_strict_positives() -> None:
    labels = _labels()
    alerts = [_alert("x1", {1, 2}), _alert("x2", {5, 6})]
    ds = mk.Datasets(dev=("x1",), heldout=("x2",), smoke=("x1",))
    ends = {a.alert_id: datetime(2022, 9, 11, 1) for a in alerts}
    lines = mk.summarise(ds, alerts, labels, ends)
    assert any(line.startswith("warning: dev") for line in lines)
    assert any(line.startswith("warning: heldout") for line in lines)
    assert any(line.startswith("warning: smoke has 0 early") for line in lines)
    assert "  dev: 1, 1, 1, 1, 0" in lines


def test_module_has_no_write_statements_or_url_printing() -> None:
    text = Path(mk.__file__).read_text(encoding="utf-8")
    for word in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"):
        assert re.search(rf"\b{word}\b", text) is None, word
    assert "insert(" not in text and "delete(" not in text
    assert "print(get_database_url" not in text
    assert "target_label(get_database_url())" in text


def test_builder_on_test_database_partitions_alerts(local_engine: Engine) -> None:
    version = "datasets-test"
    with local_engine.connect() as conn:
        trans = conn.begin()
        try:
            txns = load_txns_on(conn)
            drafts = fan_out(txns) + fan_in(txns)
            assert drafts
            delete_alerts_for_version_on(conn, version)
            write_alerts_on(conn, drafts, version)
            loaded = mk.load_from_connection(conn, version)
            ds = mk.build_datasets(loaded.alerts, loaded.labels, loaded.window_ends)
            ids = {a.alert_id for a in loaded.alerts}
            assert len(ids) == len(drafts)
            assert set(ds.dev) | set(ds.heldout) == ids
            assert not set(ds.dev) & set(ds.heldout)
            assert set(ds.smoke) <= set(ds.dev)
            assert set(loaded.window_ends) == ids
        finally:
            trans.rollback()
