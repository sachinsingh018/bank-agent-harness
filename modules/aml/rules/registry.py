"""Runs all five detectors in a fixed order. Pure, no database code."""

from collections.abc import Sequence
from dataclasses import dataclass

from modules.aml.rules.cycle import find_cycles
from modules.aml.rules.fan import fan_in, fan_out
from modules.aml.rules.gather_scatter import gather_scatter
from modules.aml.rules.ruleset import V1, RulesetConfig
from modules.aml.rules.scatter_gather import scatter_gather
from modules.aml.rules.types import AlertDraft, Txn

DETECTOR_ORDER: tuple[str, ...] = (
    "fan-out",
    "fan-in",
    "cycle",
    "scatter-gather",
    "gather-scatter",
)


@dataclass(frozen=True)
class RulesOutcome:
    drafts: list[AlertDraft]
    capped_starts: int


def run_all_with_stats(txns: Sequence[Txn], config: RulesetConfig = V1) -> RulesOutcome:
    cycles = find_cycles(txns, config)
    drafts = [
        *fan_out(txns, config),
        *fan_in(txns, config),
        *cycles.drafts,
        *scatter_gather(txns, config),
        *gather_scatter(txns, config),
    ]
    return RulesOutcome(drafts=drafts, capped_starts=cycles.capped_starts)


def run_all(txns: Sequence[Txn], config: RulesetConfig = V1) -> list[AlertDraft]:
    return run_all_with_stats(txns, config).drafts
