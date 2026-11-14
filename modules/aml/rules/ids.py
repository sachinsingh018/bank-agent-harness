"""Deterministic alert ids."""

import hashlib
from collections.abc import Iterable

SEPARATOR = "\x1f"


def make_alert_id(ruleset_version: str, detector: str, txn_ids: Iterable[int]) -> str:
    parts = [ruleset_version, detector, *(str(t) for t in sorted(txn_ids))]
    digest = hashlib.sha256(SEPARATOR.join(parts).encode("utf-8")).hexdigest()
    return f"AL-{digest[:12]}"
