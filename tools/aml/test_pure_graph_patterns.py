"""Pure tests for the graph, pattern and detector description tools. No database."""

from dataclasses import asdict, fields
from datetime import datetime
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from modules.aml.rules.registry import DETECTOR_ORDER
from modules.aml.rules.ruleset import V1, RulesetConfig
from tools.aml import caps, patterns
from tools.aml.detector_docs import describe_detector
from tools.aml.models import (
    AccountRef,
    DescribeDetectorInput,
    DetectorInfo,
    GraphInput,
    PatternCheckInput,
    PatternCheckResult,
    PatternMatch,
    PatternName,
)
from tools.aml.paging import ToolFailure
from tools.aml.query_registry import QUERY_REGISTRY, Samples

FORBIDDEN_FIELDS = {"is_laundering", "laundering", "attempt_id", "attempt_type"}


def test_graph_hops_default_and_bounds() -> None:
    assert GraphInput(bank=1, acct="a").hops == caps.DEFAULT_GRAPH_HOPS == 1
    assert GraphInput(bank=1, acct="a", hops=1).hops == 1
    assert GraphInput(bank=1, acct="a", hops=caps.MAX_GRAPH_HOPS).hops == 2


@pytest.mark.parametrize("hops", [0, -1, 3, 10, "2", 1.5, True])
def test_graph_hops_above_the_cap_or_malformed_is_invalid(hops: Any) -> None:
    with pytest.raises(ValidationError):
        GraphInput(bank=1, acct="a", hops=hops)


def test_graph_input_window_and_extra_fields() -> None:
    early, late = datetime(2022, 9, 1), datetime(2022, 9, 2)
    assert GraphInput(bank=1, acct="a", start=early, end=late).end == late
    with pytest.raises(ValidationError):
        GraphInput(bank=1, acct="a", start=late, end=early)
    with pytest.raises(ValidationError):
        GraphInput(bank=1, acct="a", extra=1)  # type: ignore[call-arg]


def test_pattern_names_are_the_five_detectors() -> None:
    names = get_args(PatternName)
    assert names == DETECTOR_ORDER
    assert set(patterns.DETECTORS) == set(names)
    for name in names:
        assert PatternCheckInput(pattern=name, bank=1, acct="a").pattern == name


@pytest.mark.parametrize("pattern", ["", "fan", "Fan-Out", "structuring", "all"])
def test_unknown_pattern_is_invalid(pattern: str) -> None:
    with pytest.raises(ValidationError):
        PatternCheckInput(pattern=pattern, bank=1, acct="a")  # type: ignore[arg-type]


def test_pattern_input_window() -> None:
    early, late = datetime(2022, 9, 1), datetime(2022, 9, 2)
    inp = PatternCheckInput(pattern="cycle", bank=1, acct="a", start=early, end=late)
    assert (inp.start, inp.end) == (early, late)
    assert PatternCheckInput(pattern="cycle", bank=1, acct="a").start is None
    with pytest.raises(ValidationError):
        PatternCheckInput(pattern="cycle", bank=1, acct="a", start=late, end=early)


def _match(n_ids: int) -> dict[str, Any]:
    return {
        "detector": "fan-out",
        "anchor": None,
        "window_start": datetime(2022, 9, 1),
        "window_end": datetime(2022, 9, 2),
        "txn_ids": list(range(1, n_ids + 1)),
        "txn_count": n_ids,
        "summary": "x",
        "params": {},
    }


def test_match_ids_and_match_count_are_capped_by_the_models() -> None:
    assert len(PatternMatch(**_match(caps.MAX_MATCH_TXN_IDS)).txn_ids) == 50
    with pytest.raises(ValidationError):
        PatternMatch(**_match(caps.MAX_MATCH_TXN_IDS + 1))
    one = PatternMatch(**_match(1))
    common: dict[str, Any] = {
        "pattern": "fan-out",
        "account": AccountRef(bank=1, acct="a"),
        "transfers_examined": 1,
        "neighbourhood_hops": 2,
        "total_matches": 11,
        "truncated": True,
        "note": "n",
    }
    assert len(PatternCheckResult(matches=[one] * caps.MAX_PATTERN_MATCHES, **common).matches) == 10
    with pytest.raises(ValidationError):
        PatternCheckResult(matches=[one] * (caps.MAX_PATTERN_MATCHES + 1), **common)


def test_new_caps() -> None:
    assert caps.MAX_PATTERN_MATCHES == 10
    assert caps.MAX_MATCH_TXN_IDS == 50
    assert caps.GRAPH_EXPANSION_FANOUT >= 1


def test_note_states_the_limits() -> None:
    assert "immediate surroundings" in patterns.NOTE
    assert "more than 4 accounts may be missed" in patterns.NOTE
    assert "does not prove" in patterns.NOTE
    assert "same detectors" in patterns.NOTE
    assert "not an independent opinion" in patterns.NOTE
    assert str(caps.MAX_PATTERN_TXNS) in patterns.TOO_LARGE_MESSAGE
    assert "narrow" in patterns.TOO_LARGE_MESSAGE


# ------------------------------------------------------------- describe_detector

PREFIXES = {
    "fan-out": "fan_out_",
    "fan-in": "fan_in_",
    "cycle": "cycle_",
    "scatter-gather": "scatter_gather_",
    "gather-scatter": "gather_scatter_",
}


@pytest.mark.parametrize("name", get_args(PatternName))
def test_detector_thresholds_equal_the_ruleset(name: str) -> None:
    info = describe_detector(DescribeDetectorInput(detector=name))
    assert isinstance(info, DetectorInfo)
    expected = {k: v for k, v in asdict(V1).items() if k.startswith(PREFIXES[name])}
    assert expected
    assert info.thresholds == expected
    assert info.detector == name
    assert info.meaning and info.roles and info.notes


def test_every_ruleset_field_is_described_exactly_once() -> None:
    seen: list[str] = []
    for name in get_args(PatternName):
        seen.extend(describe_detector(DescribeDetectorInput(detector=name)).thresholds)
    assert sorted(seen) == sorted(f.name for f in fields(RulesetConfig))


def test_detector_docs_follow_the_ruleset_not_a_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    changed = RulesetConfig(fan_out_min_receivers=99)
    monkeypatch.setattr("tools.aml.detector_docs.CONFIG", changed)
    info = describe_detector(DescribeDetectorInput(detector="fan-out"))
    assert info.thresholds["fan_out_min_receivers"] == 99


def test_detector_roles() -> None:
    def roles(name: str) -> set[str]:
        return set(describe_detector(DescribeDetectorInput(detector=name)).roles)

    assert roles("fan-out") == {"out"}
    assert roles("fan-in") == {"in"}
    assert roles("cycle") == {"hop"}
    assert roles("scatter-gather") == {"out", "gather"}
    assert roles("gather-scatter") == {"hub_in", "hub_out"}


@pytest.mark.parametrize("name", ["", "fan", "structuring", "FAN-OUT", "all"])
def test_unknown_detector_is_invalid_argument(name: str) -> None:
    if not name:
        with pytest.raises(ValidationError):
            DescribeDetectorInput(detector=name)
        return
    with pytest.raises(ToolFailure) as info:
        describe_detector(DescribeDetectorInput(detector=name))
    assert info.value.code == "invalid_argument"


def test_detector_output_has_no_label_like_keys() -> None:
    for name in get_args(PatternName):
        dumped = describe_detector(DescribeDetectorInput(detector=name)).model_dump()
        assert not FORBIDDEN_FIELDS & set(dumped)
        assert "laundering" not in str(dumped).lower()


def test_registry_handles_the_tool_without_a_connection() -> None:
    samples = Samples(
        alert_id="AL-0123456789ab",
        account=AccountRef(bank=1, acct="a"),
        txn_id=1,
        start=datetime(2022, 9, 1),
        end=datetime(2022, 9, 2),
    )
    by_name = {e.name: e for e in QUERY_REGISTRY}
    entry = by_name["describe_detector"]
    assert not entry.needs_connection
    args = entry.build_args(samples)
    assert len(args) == 5
    for a in args:
        assert entry.call(None, a).model_dump()["detector"] in get_args(PatternName)
    assert len(by_name["run_pattern_check"].build_args(samples)) == 5
    assert len(by_name["get_transaction_graph"].build_args(samples)) >= 2
