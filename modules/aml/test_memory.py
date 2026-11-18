"""Lesson selection for the AML module (pure, no database)."""

from datetime import UTC, datetime, timedelta

from modules.aml.memory import MAX_CHARS, MAX_LESSONS, Candidate, select_lessons

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _c(id_: int, case_id: str, minutes: int, rule: str | None = None) -> Candidate:
    return Candidate(id_, case_id, rule or f"Lesson number {id_}.", T0 + timedelta(minutes=minutes))


DETECTORS = {"AL-now": "fan_out", "AL-a": "fan_out", "AL-b": "cycle", "AL-c": "fan_out"}


def _ids(
    candidates: list[Candidate],
    case_id: str = "AL-now",
    excluded: frozenset[str] = frozenset(),
) -> list[int]:
    lessons = select_lessons(candidates, case_id=case_id, detectors=DETECTORS, excluded=excluded)
    return [lesson.id for lesson in lessons]


def test_the_current_alert_is_never_a_source() -> None:
    assert _ids([_c(1, "AL-now", 9), _c(2, "AL-b", 1)]) == [2]


def test_excluded_sources_are_skipped() -> None:
    cands = [_c(1, "AL-a", 1), _c(2, "AL-b", 2)]
    assert _ids(cands, excluded=frozenset({"AL-a"})) == [2]


def test_same_detector_first_then_the_rest_each_newest_first() -> None:
    cands = [_c(1, "AL-b", 50), _c(2, "AL-a", 10), _c(3, "AL-c", 20), _c(4, "AL-x", 60)]
    assert _ids(cands) == [3, 2, 4, 1]


def test_unknown_current_detector_is_newest_first() -> None:
    cands = [_c(1, "AL-a", 1), _c(2, "AL-b", 3), _c(3, "AL-c", 2)]
    assert _ids(cands, case_id="AL-unknown") == [2, 3, 1]


def test_count_cap() -> None:
    cands = [_c(i, "AL-b", i) for i in range(1, 10)]
    assert _ids(cands) == [9, 8, 7, 6, 5]
    assert len(_ids(cands)) == MAX_LESSONS


def test_character_cap_skips_what_does_not_fit() -> None:
    long = "x" * 500
    cands = [
        _c(1, "AL-b", 5, long),
        _c(2, "AL-b", 4, long),
        _c(3, "AL-b", 3, long + "y"),
        _c(4, "AL-b", 2, long),
        _c(5, "AL-b", 1, "short lesson."),
    ]
    picked = select_lessons(cands, case_id="AL-now", detectors=DETECTORS)
    assert [p.id for p in picked] == [1, 2, 4]
    assert sum(len(p.text) for p in picked) <= MAX_CHARS
    small = select_lessons(cands, case_id="AL-now", detectors=DETECTORS, max_chars=600)
    assert [p.id for p in small] == [1, 5]


def test_ties_on_time_use_the_higher_id() -> None:
    assert _ids([_c(1, "AL-b", 1), _c(2, "AL-b", 1)]) == [2, 1]
