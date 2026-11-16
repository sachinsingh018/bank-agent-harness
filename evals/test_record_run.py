"""record_run checks that run before any database connection."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals import record_run


def test_remote_write_url_is_refused_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "HARNESS_WRITE_DATABASE_URL", "postgresql+psycopg://u:p@example.neon.tech/db"
    )
    assert record_run.main(["--run", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "write target: remote database" in out
    assert "--allow-remote" in out
    assert "example.neon.tech" not in out


def test_missing_write_url_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("HARNESS_WRITE_DATABASE_URL", raising=False)
    assert record_run.main(["--run", str(tmp_path)]) == 1
    assert "HARNESS_WRITE_DATABASE_URL is not set" in capsys.readouterr().out


def test_bad_results_line_is_refused(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text('{"run_id": "x"}')
    (tmp_path / "results.jsonl").write_text('{"case_id": "a"}\n')
    with pytest.raises(record_run.SetupError, match="not a CaseResult"):
        record_run.read_run_dir(tmp_path, with_score=False)
