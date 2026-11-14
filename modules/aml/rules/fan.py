"""Fan-out and fan-in detectors. Pure functions on in-memory transfers."""

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta

from modules.aml.rules.ruleset import V1, RulesetConfig
from modules.aml.rules.types import Account, AlertDraft, Txn


def _fan(
    txns: Sequence[Txn],
    *,
    detector: str,
    role: str,
    anchor_of: Callable[[Txn], Account],
    other_of: Callable[[Txn], Account],
    min_counterparties: int,
    window_hours: int,
    verb: str,
) -> list[AlertDraft]:
    by_anchor: dict[Account, list[Txn]] = defaultdict(list)
    for t in txns:
        if t.sender == t.receiver:
            continue
        by_anchor[anchor_of(t)].append(t)

    window = timedelta(hours=window_hours)
    drafts: list[AlertDraft] = []
    for anchor, group in by_anchor.items():
        if len(group) < min_counterparties:
            continue
        group.sort(key=lambda t: (t.ts, t.txn_id))
        # Every qualifying window, as a (start, end) pair of timestamps.
        spans: list[tuple[datetime, datetime]] = []
        for left, first in enumerate(group):
            last_index = left
            while last_index + 1 < len(group) and group[last_index + 1].ts - first.ts <= window:
                last_index += 1
            distinct = {other_of(t) for t in group[left : last_index + 1]}
            if len(distinct) >= min_counterparties:
                spans.append((first.ts, group[last_index].ts))
        # Union overlapping windows into bursts (spans are already ordered by start).
        bursts: list[tuple[datetime, datetime]] = []
        for start, end in spans:
            if bursts and start <= bursts[-1][1]:
                bursts[-1] = (bursts[-1][0], max(bursts[-1][1], end))
            else:
                bursts.append((start, end))
        for start, end in bursts:
            members = [t for t in group if start <= t.ts <= end]
            counterparties = {other_of(t) for t in members}
            txn_ids = tuple(sorted(t.txn_id for t in members))
            summary = (
                f"Account {anchor[0]}/{anchor[1]} {verb} {len(counterparties)} different accounts "
                f"in {len(members)} transfers between {start:%Y-%m-%d %H:%M} "
                f"and {end:%Y-%m-%d %H:%M}."
            )
            drafts.append(
                AlertDraft(
                    detector=detector,
                    anchor=anchor,
                    window_start=start,
                    window_end=end,
                    txn_ids=txn_ids,
                    roles={i: role for i in txn_ids},
                    account_count=len(counterparties) + 1,
                    params={
                        "distinct_counterparties": len(counterparties),
                        "min_counterparties": min_counterparties,
                        "window_hours": window_hours,
                    },
                    summary=summary,
                )
            )
    drafts.sort(key=lambda d: (d.anchor or (0, ""), d.window_start, d.txn_ids))
    return drafts


def fan_out(txns: Sequence[Txn], config: RulesetConfig = V1) -> list[AlertDraft]:
    """One account sends to many different accounts in a short time."""
    return _fan(
        txns,
        detector="fan-out",
        role="out",
        anchor_of=lambda t: t.sender,
        other_of=lambda t: t.receiver,
        min_counterparties=config.fan_out_min_receivers,
        window_hours=config.fan_out_window_hours,
        verb="sent to",
    )


def fan_in(txns: Sequence[Txn], config: RulesetConfig = V1) -> list[AlertDraft]:
    """Many different accounts send to one account in a short time."""
    return _fan(
        txns,
        detector="fan-in",
        role="in",
        anchor_of=lambda t: t.receiver,
        other_of=lambda t: t.sender,
        min_counterparties=config.fan_in_min_senders,
        window_hours=config.fan_in_window_hours,
        verb="received from",
    )
