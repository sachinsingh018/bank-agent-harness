"""Corrections: short lessons a reviewer saves with a decision, for future cases.

A correction is written only by the human decision path (apps/api/queue.py), in the same
transaction as its decision event, and the table is append-only. GET /corrections lives in
queue.py with the other read endpoints. The agent never reads corrections itself: with
--lessons on, the runner asks modules/aml/memory.py for a few of them and passes them to the
loop as plain lessons (docs/plans/corrections-memory.md). Rule text must not mention calendar
dates, month names or years (decision 0006).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import Connection, insert

from harness.db.records_models import Correction

CorrectionKind = Literal["recommendation", "typology", "confidence", "evidence", "other"]
MIN_RULE = 10
MAX_RULE = 500
DATE_REFUSED = (
    "the rule must not mention calendar dates, month names or years (decision 0006: a lesson "
    "must never encode the late date artifact)"
)

_MONTHS = (
    "january|february|march|april|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
DATE_PATTERNS = (
    re.compile(r"\b(?:19|20)\d{2}\b"),  # a 4 digit year
    re.compile(rf"\b(?:{_MONTHS})\b", re.IGNORECASE),
    # "may" is also an everyday word, so only May next to a number counts.
    re.compile(r"\bmay\b\s*\d|\d\s*\bmay\b", re.IGNORECASE),
    re.compile(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b"),  # 2022-09-11, 11/09/2022
)


def mentions_a_date(text: str) -> bool:
    return any(p.search(text) for p in DATE_PATTERNS)


class CorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: CorrectionKind
    rule: str

    @field_validator("rule")
    @classmethod
    def _rule_rules(cls, value: str) -> str:
        value = value.strip()
        if not MIN_RULE <= len(value) <= MAX_RULE:
            raise ValueError(f"the rule must be {MIN_RULE} to {MAX_RULE} characters")
        if mentions_a_date(value):
            raise ValueError(DATE_REFUSED)
        return value


def insert_correction(
    conn: Connection,
    correction: CorrectionIn,
    *,
    module: str,
    case_id: str,
    run_id: str,
    decision_event_id: int,
    author: str,
) -> int:
    """Insert one correction on the caller's open transaction and return its id."""
    return int(
        conn.execute(
            insert(Correction)
            .values(
                module=module,
                case_id=case_id,
                run_id=run_id,
                decision_event_id=decision_event_id,
                kind=correction.kind,
                rule=correction.rule,
                author=author,
            )
            .returning(Correction.id)
        ).scalar_one()
    )
