"""run_pattern_check: runs one of the rules layer detectors on the transfers around an account.

The detector code is the same pure code that creates alerts, so the answer agrees with the
alerts. This file only reads transfers (through the shared read-only queries) and converts them
to in-memory records. Nothing is written and no alerts are stored.
"""

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, or_, tuple_

from harness.db.models import Transaction
from modules.aml.rules.cycle import cycle
from modules.aml.rules.fan import fan_in, fan_out
from modules.aml.rules.gather_scatter import gather_scatter
from modules.aml.rules.ruleset import RULESET_VERSION, V1, RulesetConfig
from modules.aml.rules.scatter_gather import scatter_gather
from modules.aml.rules.types import AlertDraft, Txn
from tools.aml.caps import MAX_MATCH_TXN_IDS, MAX_PATTERN_MATCHES, MAX_PATTERN_TXNS
from tools.aml.models import AccountRef, PatternCheckInput, PatternCheckResult, PatternMatch
from tools.aml.paging import ToolFailure, fit_items, json_size
from tools.aml.queries import (
    AccountKey,
    _involved,
    _require_account,
    _transfer_select,
    _window,
)

CONFIG: RulesetConfig = V1
CONFIG_VERSION = RULESET_VERSION
NEIGHBOURHOOD_HOPS = 2

Detector = Callable[[Sequence[Txn], RulesetConfig], list[AlertDraft]]
DETECTORS: dict[str, Detector] = {
    "fan-out": fan_out,
    "fan-in": fan_in,
    "cycle": cycle,
    "scatter-gather": scatter_gather,
    "gather-scatter": gather_scatter,
}

NOTE = (
    "This looks only at the account's immediate surroundings: the account's own transfers and "
    "every transfer of the accounts that dealt directly with it. Loops of more than 4 accounts "
    "may be missed, and a clean result does not prove that no longer loop exists. It uses the "
    "same detectors that create alerts, so it is evidence about the surrounding accounts, not "
    "an independent opinion."
)

TOO_LARGE_MESSAGE = (
    f"more than {MAX_PATTERN_TXNS} transfers around this account in the window: "
    "narrow the start and end"
)


def _to_txn(row: Any) -> Txn:
    return Txn(
        txn_id=row.txn_id,
        ts=row.ts,
        from_bank=row.from_bank,
        from_acct=row.from_acct,
        to_bank=row.to_bank,
        to_acct=row.to_acct,
        amt_recv=row.amt_recv,
        cur_recv=row.cur_recv,
        amt_paid=row.amt_paid,
        cur_paid=row.cur_paid,
        fmt=row.fmt,
    )


def _load_neighbourhood(
    conn: Connection, root: AccountKey, start: datetime | None, end: datetime | None
) -> list[Txn]:
    """The account's own transfers plus every transfer of each account that dealt directly with
    it, window applied. A transfer between two accounts that are both two steps away is not
    loaded, so loops of more than 4 accounts can be missed. Self transfers stay in, the
    detectors ignore them. Raises too_large past MAX_PATTERN_TXNS."""
    window = _window(start, end)
    first = conn.execute(
        _transfer_select()
        .where(_involved(*root), *window)
        .order_by(Transaction.ts, Transaction.txn_id)
        .limit(MAX_PATTERN_TXNS + 1)
    ).all()
    if len(first) > MAX_PATTERN_TXNS:
        raise ToolFailure("too_large", TOO_LARGE_MESSAGE)
    rows = {r.txn_id: r for r in first}
    neighbours: set[AccountKey] = set()
    for r in first:
        neighbours.add((r.from_bank, r.from_acct))
        neighbours.add((r.to_bank, r.to_acct))
    neighbours.discard(root)
    if neighbours:
        keys = sorted(neighbours)
        second = conn.execute(
            _transfer_select()
            .where(
                or_(
                    tuple_(Transaction.from_bank, Transaction.from_acct).in_(keys),
                    tuple_(Transaction.to_bank, Transaction.to_acct).in_(keys),
                ),
                *window,
            )
            .order_by(Transaction.ts, Transaction.txn_id)
            .limit(MAX_PATTERN_TXNS + 1)
        ).all()
        if len(second) > MAX_PATTERN_TXNS:
            raise ToolFailure("too_large", TOO_LARGE_MESSAGE)
        rows.update({r.txn_id: r for r in second})
    if len(rows) > MAX_PATTERN_TXNS:
        raise ToolFailure("too_large", TOO_LARGE_MESSAGE)
    return [_to_txn(rows[i]) for i in sorted(rows)]


def _involves(draft: AlertDraft, by_id: dict[int, Txn], root: AccountKey) -> bool:
    return any(root in (by_id[i].sender, by_id[i].receiver) for i in draft.txn_ids if i in by_id)


def _to_match(draft: AlertDraft) -> PatternMatch:
    anchor = AccountRef(bank=draft.anchor[0], acct=draft.anchor[1]) if draft.anchor else None
    return PatternMatch(
        detector=draft.detector,
        anchor=anchor,
        window_start=draft.window_start,
        window_end=draft.window_end,
        txn_ids=list(draft.txn_ids[:MAX_MATCH_TXN_IDS]),
        txn_count=len(draft.txn_ids),
        summary=draft.summary,
        params=dict(draft.params),
    )


def run_pattern_check(conn: Connection, inp: PatternCheckInput) -> PatternCheckResult:
    root: AccountKey = (inp.bank, inp.acct)
    _require_account(conn, *root)
    txns = _load_neighbourhood(conn, root, inp.start, inp.end)
    by_id = {t.txn_id: t for t in txns}
    drafts = DETECTORS[inp.pattern](txns, CONFIG)
    mine = [d for d in drafts if _involves(d, by_id, root)]
    mine.sort(key=lambda d: (d.window_start, d.txn_ids))
    matches = [_to_match(d) for d in mine[:MAX_PATTERN_MATCHES]]
    id_cut = any(m.txn_count > len(m.txn_ids) for m in matches)
    account = AccountRef(bank=inp.bank, acct=inp.acct)

    def build(kept: list[PatternMatch], cut: bool) -> PatternCheckResult:
        return PatternCheckResult(
            pattern=inp.pattern,
            account=account,
            transfers_examined=len(txns),
            neighbourhood_hops=NEIGHBOURHOOD_HOPS,
            matches=kept,
            total_matches=len(mine),
            truncated=cut,
            note=NOTE,
        )

    shown = fit_items(matches, lambda kept: json_size(build(kept, True)))
    return build(shown, id_cut or len(shown) < len(mine))
