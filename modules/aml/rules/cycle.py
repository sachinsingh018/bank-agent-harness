"""Cycle detector. Money leaves an account, passes through others and returns to the start."""

from bisect import bisect_left
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from modules.aml.rules.ruleset import V1, RulesetConfig
from modules.aml.rules.types import Account, AlertDraft, Txn

# Safety cap on explored hops per first transfer. Hitting it is counted, never silent.
MAX_STEPS_PER_START = 50_000


@dataclass(frozen=True)
class CycleResult:
    drafts: list[AlertDraft]
    capped_starts: int


@dataclass(frozen=True)
class _Found:
    start: datetime
    end: datetime
    txn_ids: tuple[int, ...]


class _Search:
    """Bounded depth-first search for loops that start with one given transfer."""

    def __init__(
        self,
        first: Txn,
        out_by: dict[Account, list[Txn]],
        times: dict[Account, list[datetime]],
        window: timedelta,
        max_length: int,
    ) -> None:
        self.first = first
        self.origin = first.sender
        self.limit = first.ts + window
        self.out_by = out_by
        self.times = times
        self.max_length = max_length
        self.steps = 0
        self.capped = False
        self.path: list[Txn] = [first]
        self.visited: set[Account] = {first.sender, first.receiver}
        self.cycles: list[tuple[frozenset[Account], _Found]] = []

    def walk(self, current: Account, last_ts: datetime) -> None:
        options = self.out_by.get(current)
        if options is None:
            return
        for i in range(bisect_left(self.times[current], last_ts), len(options)):
            t = options[i]
            if t.ts > self.limit or self.capped:
                return
            self.steps += 1
            if self.steps > MAX_STEPS_PER_START:
                self.capped = True
                return
            nxt = t.receiver
            if nxt == self.origin:
                hops = [*self.path, t]
                item = _Found(
                    start=self.first.ts,
                    end=max(h.ts for h in hops),
                    txn_ids=tuple(sorted(h.txn_id for h in hops)),
                )
                self.cycles.append((frozenset(self.visited), item))
            elif nxt not in self.visited and len(self.path) + 1 < self.max_length:
                self.path.append(t)
                self.visited.add(nxt)
                self.walk(nxt, t.ts)
                self.visited.discard(nxt)
                self.path.pop()


def find_cycles(txns: Sequence[Txn], config: RulesetConfig = V1) -> CycleResult:
    window = timedelta(hours=config.cycle_window_hours)
    max_length = config.cycle_max_length
    usable = sorted((t for t in txns if t.sender != t.receiver), key=lambda t: (t.ts, t.txn_id))
    out_by: dict[Account, list[Txn]] = defaultdict(list)
    for t in usable:
        out_by[t.sender].append(t)
    times: dict[Account, list[datetime]] = {a: [t.ts for t in ts] for a, ts in out_by.items()}

    found: dict[frozenset[Account], list[_Found]] = defaultdict(list)
    capped_starts = 0
    for first in usable:
        search = _Search(first, out_by, times, window, max_length)
        search.walk(first.receiver, first.ts)
        for accounts, item in search.cycles:
            found[accounts].append(item)
        if search.capped:
            capped_starts += 1

    drafts: list[AlertDraft] = []
    for accounts, items in found.items():
        items.sort(key=lambda f: (f.start, f.end, f.txn_ids))
        merged: list[tuple[datetime, datetime, set[int]]] = []
        for f in items:
            if merged and f.start <= merged[-1][1]:
                cur = merged[-1]
                merged[-1] = (cur[0], max(cur[1], f.end), cur[2] | set(f.txn_ids))
            else:
                merged.append((f.start, f.end, set(f.txn_ids)))
        anchor = min(accounts)
        for start, end, ids in merged:
            txn_ids = tuple(sorted(ids))
            drafts.append(
                AlertDraft(
                    detector="cycle",
                    anchor=anchor,
                    window_start=start,
                    window_end=end,
                    txn_ids=txn_ids,
                    roles={i: "hop" for i in txn_ids},
                    account_count=len(accounts),
                    params={
                        "cycle_length": len(accounts),
                        "max_length": max_length,
                        "window_hours": config.cycle_window_hours,
                    },
                    summary=(
                        f"Money went around a loop of {len(accounts)} accounts that includes "
                        f"{anchor[0]}/{anchor[1]} in {len(txn_ids)} transfers between "
                        f"{start:%Y-%m-%d %H:%M} and {end:%Y-%m-%d %H:%M}."
                    ),
                )
            )
    drafts.sort(key=lambda d: (d.anchor or (0, ""), d.window_start, d.txn_ids))
    return CycleResult(drafts=drafts, capped_starts=capped_starts)


def cycle(txns: Sequence[Txn], config: RulesetConfig = V1) -> list[AlertDraft]:
    """Loop of 2 to cycle_max_length accounts, in time order, inside the window."""
    return find_cycles(txns, config).drafts
