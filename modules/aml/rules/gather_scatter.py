"""Gather-scatter detector. A hub collects from several accounts, then pays several others."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from modules.aml.rules.ruleset import V1, RulesetConfig
from modules.aml.rules.types import Account, AlertDraft, Txn


def _counted(
    ins: Sequence[Txn], outs: Sequence[Txn], start: datetime, end: datetime
) -> tuple[list[Txn], list[Txn]]:
    """Pay-ins in [start, end] and payouts in [start, end] (never before the window start)."""
    return (
        [t for t in ins if start <= t.ts <= end],
        [t for t in outs if start <= t.ts <= end],
    )


def gather_scatter(txns: Sequence[Txn], config: RulesetConfig = V1) -> list[AlertDraft]:
    """A hub receives from 3 or more accounts and pays 3 or more others, payouts after pay-ins."""
    min_in = config.gather_scatter_min_in
    min_out = config.gather_scatter_min_out
    window_hours = config.gather_scatter_window_hours
    window = timedelta(hours=window_hours)

    incoming: dict[Account, list[Txn]] = defaultdict(list)
    outgoing: dict[Account, list[Txn]] = defaultdict(list)
    for t in txns:
        if t.sender == t.receiver:
            continue
        incoming[t.receiver].append(t)
        outgoing[t.sender].append(t)

    drafts: list[AlertDraft] = []
    for hub in sorted(incoming):
        ins = sorted(incoming[hub], key=lambda t: (t.ts, t.txn_id))
        outs = sorted(outgoing.get(hub, []), key=lambda t: (t.ts, t.txn_id))
        if len({t.sender for t in ins}) < min_in or len({t.receiver for t in outs}) < min_out:
            continue
        spans: list[tuple[datetime, datetime]] = []
        for s in sorted({t.ts for t in ins}):
            in_w, out_w = _counted(ins, outs, s, s + window)
            if (
                len({t.sender for t in in_w}) >= min_in
                and len({t.receiver for t in out_w}) >= min_out
            ):
                spans.append((s, max(t.ts for t in (*in_w, *out_w))))
        bursts: list[tuple[datetime, datetime]] = []
        for start, end in spans:
            if bursts and start <= bursts[-1][1]:
                bursts[-1] = (bursts[-1][0], max(bursts[-1][1], end))
            else:
                bursts.append((start, end))
        for start, end in bursts:
            in_w, out_w = _counted(ins, outs, start, end)
            roles = {t.txn_id: "hub_in" for t in in_w}
            roles.update({t.txn_id: "hub_out" for t in out_w})
            txn_ids = tuple(sorted(roles))
            senders = {t.sender for t in in_w}
            receivers = {t.receiver for t in out_w}
            drafts.append(
                AlertDraft(
                    detector="gather-scatter",
                    anchor=hub,
                    window_start=start,
                    window_end=end,
                    txn_ids=txn_ids,
                    roles=roles,
                    account_count=len(senders | receivers | {hub}),
                    params={
                        "distinct_in": len(senders),
                        "distinct_out": len(receivers),
                        "min_in": min_in,
                        "min_out": min_out,
                        "window_hours": window_hours,
                    },
                    summary=(
                        f"Account {hub[0]}/{hub[1]} received from {len(senders)} different "
                        f"accounts and paid {len(receivers)} different accounts in "
                        f"{len(txn_ids)} transfers between {start:%Y-%m-%d %H:%M} "
                        f"and {end:%Y-%m-%d %H:%M}."
                    ),
                )
            )
    drafts.sort(key=lambda d: (d.anchor or (0, ""), d.window_start, d.txn_ids))
    return drafts
