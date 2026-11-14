"""Unit tests for the AML slice loader. No database is touched."""

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from data import load_aml_slice as m

SECRET_URL = "postgresql+psycopg://sekretuser:sekretpass@sekrethost.example/db"

GOOD = {
    "ts": "2022/09/01 00:00",
    "from_bank": "10",
    "from_acct": "AAA",
    "to_bank": "20",
    "to_acct": "BBB",
    "amt_recv": "12.50",
    "cur_recv": "US Dollar",
    "amt_paid": "12.50",
    "cur_paid": "US Dollar",
    "fmt": "Cheque",
    "laundering": "0",
    "attempt_id": "",
    "attempt_type": "",
}


def _write(
    path: Path, records: list[dict[str, str]], header: tuple[str, ...] = m.CSV_COLUMNS
) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(header), extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    return path


def _launder(**over: str) -> dict[str, str]:
    return {**GOOD, "laundering": "1", "attempt_id": "101.0", "attempt_type": "CYCLE", **over}


def test_parse_normal_and_laundering() -> None:
    normal = m.parse_row(1, GOOD)
    assert normal.attempt_id is None and normal.attempt_type is None
    assert normal.is_laundering is False
    assert normal.amt_paid == Decimal("12.50")
    bad = m.parse_row(2, _launder())
    assert bad.attempt_id == 101 and bad.attempt_type == "CYCLE" and bad.is_laundering
    assert bad.txn_id == 2


def test_hash_stable_and_ignores_labels() -> None:
    assert m.compute_row_hash(GOOD) == m.compute_row_hash(dict(GOOD))
    assert len(m.compute_row_hash(GOOD)) == 64
    assert m.compute_row_hash(GOOD) == m.compute_row_hash(_launder())
    assert m.compute_row_hash(GOOD) != m.compute_row_hash({**GOOD, "amt_paid": "12.51"})


def test_missing_column(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.csv", [GOOD], header=m.CSV_COLUMNS[:-1])
    with pytest.raises(m.LoaderError, match="13 expected columns"):
        m.read_rows(path)


@pytest.mark.parametrize(
    "bad",
    [
        {"amt_paid": "abc"},
        {"amt_recv": "NaN"},
        {"from_bank": "x"},
        {"ts": "2022-09-01"},
        {"fmt": ""},
        {"laundering": "2"},
    ],
)
def test_bad_values(tmp_path: Path, bad: dict[str, str]) -> None:
    path = _write(tmp_path / "a.csv", [{**GOOD, **bad}])
    with pytest.raises(m.LoaderError, match="CSV row 1"):
        m.read_rows(path)


def test_duplicate_row(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.csv", [GOOD, GOOD])
    with pytest.raises(m.LoaderError, match="duplicate"):
        m.read_rows(path)


def test_bad_label_combos(tmp_path: Path) -> None:
    with pytest.raises(m.LoaderError, match="normal row"):
        m.read_rows(_write(tmp_path / "a.csv", [{**GOOD, "attempt_type": "CYCLE"}]))
    with pytest.raises(m.LoaderError, match="laundering row"):
        m.read_rows(_write(tmp_path / "b.csv", [_launder(attempt_id="")]))
    with pytest.raises(m.LoaderError, match="laundering row"):
        m.read_rows(_write(tmp_path / "c.csv", [_launder(attempt_type="")]))


def test_unknown_family(tmp_path: Path) -> None:
    path = _write(tmp_path / "a.csv", [_launder(attempt_type="MYSTERY")])
    with pytest.raises(m.LoaderError, match="8 known families"):
        m.read_rows(path)


def test_dry_run_on_real_csv(capsys: pytest.CaptureFixture[str]) -> None:
    rows = m.read_rows(m.DEFAULT_CSV)
    s = m.summarize(rows)
    assert (s.rows, s.laundering, s.normal) == (25_493, 522, 24_971)
    assert m.main(["load", "--dry-run"]) == 0
    assert "rows=25493 laundering=522" in capsys.readouterr().out


def test_errors_never_contain_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", SECRET_URL)
    path = _write(tmp_path / "a.csv", [{**GOOD, "amt_paid": "abc"}])
    assert m.main(["load", "--csv", str(path)]) == 1
    monkeypatch.setenv("DATABASE_URL", "mysql://sekretuser:sekretpass@sekrethost.example/db")
    assert m.main(["load"]) == 1
    assert m.main(["verify"]) == 1
    out = capsys.readouterr().out
    for piece in ("sekret", "example"):
        assert piece not in out
    assert m.target_label(SECRET_URL) == "remote database"
    assert m.target_label("postgresql+psycopg://u:p@db:5432/harness") == "local database"
