"""Scatter-gather detector. A source splits money across middles that pay one final account."""

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from modules.aml.rules.ruleset import V1, RulesetConfig
from modules.aml.rules.types import Account, AlertDraft, Txn


def _qualifying(
    middles: dict[Account, tuple[list[Txn], list[Txn]]],
    start: datetime,
    end: datetime,
) -> dict[Account, tuple[list[Txn], list[Txn]]]:
    """Middles with an out transfer and a gather transfer not before it, inside [start, end].

    Per middle, only gather transfers at or after its earliest counted out transfer are kept.
    """
    result: dict[Account, tuple[list[Txn], list[Txn]]] = {}
    for middle, (outs, gathers) in middles.items():
        o = [t for t in outs if start <= t.ts <= end]
        g = [t for t in gathers if start <= t.ts <= end]
        if not o:
            continue
        received = min(t.ts for t in o)
        g = [t for t in g if t.ts >= received]
        if g:
            result[middle] = (o, g)
    return result


def scatter_gather(txns: Sequence[Txn], config: RulesetConfig = V1) -> list[AlertDraft]:
    """One source, 3 or more middles that each received from it and pay the same final account."""
    min_middles = config.scatter_gather_min_middle
    window_hours = config.scatter_gather_window_hours
    window = timedelta(hours=window_hours)

    usable = [t for t in txns if t.sender != t.receiver]
    outgoing: dict[Account, list[Txn]] = defaultdict(list)
    for t in usable:
        outgoing[t.sender].append(t)

    # (source, final) -> middle -> (source to middle, middle to final)
    pairs: dict[tuple[Account, Account], dict[Account, tuple[list[Txn], list[Txn]]]] = defaultdict(
        dict
    )
    for source, first_hops in outgoing.items():
        by_middle: dict[Account, list[Txn]] = defaultdict(list)
        for t in first_hops:
            by_middle[t.receiver].append(t)
        for middle, outs in by_middle.items():
            if middle == source:
                continue
            by_final: dict[Account, list[Txn]] = defaultdict(list)
            for t in outgoing.get(middle, []):
                if t.receiver != source:
                    by_final[t.receiver].append(t)
            for final, gathers in by_final.items():
                pairs[(source, final)][middle] = (outs, gathers)

    drafts: list[AlertDraft] = []
    for (source, final), middles in pairs.items():
        if len(middles) < min_middles:
            continue
        starts = sorted({t.ts for outs, _ in middles.values() for t in outs})
        spans: list[tuple[datetime, datetime]] = []
        for s in starts:
            ok = _qualifying(middles, s, s + window)
            if len(ok) >= min_middles:
                last = max(t.ts for o, g in ok.values() for t in (*o, *g))
                spans.append((s, last))
        bursts: list[tuple[datetime, datetime]] = []
        for start, end in spans:
            if bursts and start <= bursts[-1][1]:
                bursts[-1] = (bursts[-1][0], max(bursts[-1][1], end))
            else:
                bursts.append((start, end))
        for start, end in bursts:
            ok = _qualifying(middles, start, end)
            roles: dict[int, str] = {}
            for o, g in ok.values():
                roles.update({t.txn_id: "out" for t in o})
                roles.update({t.txn_id: "gather" for t in g})
            txn_ids = tuple(sorted(roles))
            drafts.append(
                AlertDraft(
                    detector="scatter-gather",
                    anchor=source,
                    window_start=start,
                    window_end=end,
                    txn_ids=txn_ids,
                    roles=roles,
                    account_count=len(ok) + 2,
                    params={
                        "distinct_middles": len(ok),
                        "min_middles": min_middles,
                        "window_hours": window_hours,
                    },
                    summary=(
                        f"Account {source[0]}/{source[1]} sent money through {len(ok)} middle "
                        f"accounts that all paid account {final[0]}/{final[1]}, in "
                        f"{len(txn_ids)} transfers between {start:%Y-%m-%d %H:%M} "
                        f"and {end:%Y-%m-%d %H:%M}."
                    ),
                )
            )
    drafts.sort(key=lambda d: (d.anchor or (0, ""), d.window_start, d.txn_ids))
    return drafts
