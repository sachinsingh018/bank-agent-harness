"""Ruleset versions and thresholds. Changing a value means a new version."""

from dataclasses import dataclass

RULESET_VERSION = "v1"


@dataclass(frozen=True)
class RulesetConfig:
    fan_out_min_receivers: int = 6
    fan_out_window_hours: int = 96
    fan_in_min_senders: int = 6
    fan_in_window_hours: int = 96
    cycle_max_length: int = 6
    cycle_window_hours: int = 96
    scatter_gather_min_middle: int = 3
    scatter_gather_window_hours: int = 96
    gather_scatter_min_in: int = 3
    gather_scatter_min_out: int = 3
    gather_scatter_window_hours: int = 192


V1 = RulesetConfig()
