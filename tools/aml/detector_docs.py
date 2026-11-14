"""describe_detector: plain-English meaning, thresholds and roles of each detector.

No database. The thresholds are read from the same v1 ruleset the detectors run with, so the
text and the numbers cannot drift apart.
"""

from dataclasses import asdict

from tools.aml.models import DescribeDetectorInput, DetectorInfo
from tools.aml.paging import ToolFailure
from tools.aml.patterns import CONFIG, CONFIG_VERSION

# Which RulesetConfig fields belong to which detector, by field name prefix.
_PREFIX = {
    "fan-out": "fan_out_",
    "fan-in": "fan_in_",
    "cycle": "cycle_",
    "scatter-gather": "scatter_gather_",
    "gather-scatter": "gather_scatter_",
}

_MEANING = {
    "fan-out": (
        "One account sends money to at least the minimum number of different accounts, all "
        "inside one time window. Banks watch for this because it can be a way to split funds "
        "up so each piece looks small. Overlapping windows are merged into one burst."
    ),
    "fan-in": (
        "At least the minimum number of different accounts send money to one account, all "
        "inside one time window. It can mean funds are being collected into one place. "
        "Overlapping windows are merged into one burst."
    ),
    "cycle": (
        "Money leaves an account, may pass through other accounts and comes back to where it "
        "started. Each transfer is at or after the one before it, the loop has no more "
        "accounts than the maximum length, and it finishes within the window that starts at "
        "its first transfer. It is a loop with no clear business reason."
    ),
    "scatter-gather": (
        "One source account sends money to at least the minimum number of middle accounts, "
        "and each of those middle accounts pays the same final account, counting only "
        "payments to the final account made at or after the middle account first received "
        "from the source. All of it is inside one time window. The money is spread out and "
        "then brought back together."
    ),
    "gather-scatter": (
        "A hub account receives from at least the minimum number of different accounts and "
        "pays at least the minimum number of other accounts, counting only payouts made at "
        "or after the first pay-in. All of it is inside one time window that starts at the "
        "first pay-in. A payout can come before some of the later pay-ins."
    ),
}

_ROLES = {
    "fan-out": {"out": "a transfer sent by the anchor account"},
    "fan-in": {"in": "a transfer received by the anchor account"},
    "cycle": {"hop": "one step of the loop"},
    "scatter-gather": {
        "out": "a transfer from the source to a middle account",
        "gather": "a transfer from a middle account to the final account",
    },
    "gather-scatter": {
        "hub_in": "a transfer received by the hub",
        "hub_out": "a transfer paid out by the hub",
    },
}

_NOTES = {
    "fan-out": "The anchor is the sending account. Self transfers are ignored.",
    "fan-in": "The anchor is the receiving account. Self transfers are ignored.",
    "cycle": "The anchor is the lowest numbered account in the loop. Self transfers are ignored.",
    "scatter-gather": "The anchor is the source account. Self transfers are ignored.",
    "gather-scatter": "The anchor is the hub. Self transfers are ignored.",
}


def describe_detector(inp: DescribeDetectorInput) -> DetectorInfo:
    name = inp.detector
    if name not in _PREFIX:
        raise ToolFailure("invalid_argument", "unknown detector: use one of " + ", ".join(_PREFIX))
    prefix = _PREFIX[name]
    thresholds = {k: v for k, v in asdict(CONFIG).items() if k.startswith(prefix)}
    return DetectorInfo(
        detector=name,
        meaning=_MEANING[name],
        thresholds=thresholds,
        roles=dict(_ROLES[name]),
        notes=f"{_NOTES[name]} Thresholds are from ruleset {CONFIG_VERSION}.",
    )
