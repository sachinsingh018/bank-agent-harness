"""Corrections memory for the AML module: pick a few saved reviewer lessons for a new alert.

This file sits outside the agent (not in modules/aml/agent/) and is the one module file allowed
to read the corrections table (harness/test_read_only_imports.py). It reads corrections through
the records engine and alert detectors through the data engine, each inside a read-only
transaction, and writes nothing. The runner calls it before the loop starts and passes plain
lessons (id and text) to the loop. Plan: docs/plans/corrections-memory.md section 1.

Selection is deterministic, no embeddings:
- never a correction saved on the alert being investigated, nor on any excluded alert (the
  eval runner excludes every held-out alert, so held-out sources never feed any run);
- corrections whose source alert used the same detector as this alert come first, then the
  rest, each group newest first;
- at most MAX_LESSONS lessons and MAX_CHARS characters of lesson text in total (a lesson that
  does not fit is skipped and shorter later ones may still fit).

Rule text with dates is refused when a correction is saved (apps/api/corrections.py, decision
0006), so no date based rule can reach the agent through here.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, select

from harness.db.models import Alert
from harness.db.records_models import Correction
from harness.lessons import Lesson
from tools.aml.db import open_readonly

MODULE = "aml"
MAX_LESSONS = 5
MAX_CHARS = 1500
CANDIDATE_LIMIT = 200  # newest corrections considered per case


@dataclass(frozen=True)
class Candidate:
    id: int
    case_id: str  # the alert the correction was saved on
    rule: str
    created_at: datetime


def select_lessons(
    candidates: Sequence[Candidate],
    *,
    case_id: str,
    detectors: Mapping[str, str],
    excluded: AbstractSet[str] = frozenset(),
    max_lessons: int = MAX_LESSONS,
    max_chars: int = MAX_CHARS,
) -> list[Lesson]:
    """Pure selection (see the module docstring). detectors maps alert id to detector name;
    an alert without an entry simply never counts as the same detector."""
    current = detectors.get(case_id)
    allowed = [c for c in candidates if c.case_id != case_id and c.case_id not in excluded]
    newest = sorted(allowed, key=lambda c: (c.created_at, c.id), reverse=True)
    same = [c for c in newest if current is not None and detectors.get(c.case_id) == current]
    ordered = same + [c for c in newest if c not in same]
    chosen: list[Lesson] = []
    used = 0
    for c in ordered:
        if len(chosen) >= max_lessons:
            break
        if used + len(c.rule) > max_chars:
            continue
        chosen.append(Lesson(id=c.id, text=c.rule))
        used += len(c.rule)
    return chosen


class AmlLessonsProvider:
    """case id -> lessons, for harness.runner.run_case(lessons_provider=...).

    records_engine reads corrections (the records database), data_engine reads alerts (the AML
    data, a guarded tool engine is fine). Both are owned by the caller. sources records the
    source alert of every lesson handed out, for the run manifest and the scorer warning."""

    def __init__(
        self,
        records_engine: Engine,
        data_engine: Engine,
        excluded_case_ids: AbstractSet[str] = frozenset(),
    ) -> None:
        self.records_engine = records_engine
        self.data_engine = data_engine
        self.excluded = frozenset(excluded_case_ids)
        self.sources: dict[int, str] = {}
        self._lock = threading.Lock()

    def _candidates(self) -> list[Candidate]:
        stmt = (
            select(Correction.id, Correction.case_id, Correction.rule, Correction.created_at)
            .where(Correction.module == MODULE)
            .order_by(Correction.created_at.desc(), Correction.id.desc())
            .limit(CANDIDATE_LIMIT)
        )
        with open_readonly(self.records_engine) as conn:
            return [Candidate(r.id, r.case_id, r.rule, r.created_at) for r in conn.execute(stmt)]

    def _detectors(self, alert_ids: set[str]) -> dict[str, str]:
        stmt = select(Alert.alert_id, Alert.detector).where(Alert.alert_id.in_(sorted(alert_ids)))
        with open_readonly(self.data_engine) as conn:
            return {r.alert_id: r.detector for r in conn.execute(stmt)}

    def __call__(self, case_id: str) -> list[Lesson]:
        candidates = self._candidates()
        if not candidates:
            return []
        detectors = self._detectors({case_id, *(c.case_id for c in candidates)})
        lessons = select_lessons(
            candidates, case_id=case_id, detectors=detectors, excluded=self.excluded
        )
        by_id = {c.id: c.case_id for c in candidates}
        with self._lock:
            for lesson in lessons:
                self.sources[lesson.id] = by_id[lesson.id]
        return lessons
