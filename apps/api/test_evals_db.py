"""The eval runs API on the local test database.

Skipped unless TEST_DATABASE_URL points at a local host. Reads TEST_DATABASE_URL only, never
DATABASE_URL: the records engine is injected with a FastAPI dependency override. Eval runs are
recorded through harness.records and removed again by tests_support.records_db. No model API
calls."""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from apps.api.deps import get_records_engine
from apps.api.evals import read_score
from apps.api.main import app
from harness.records import record_eval_result, record_eval_run
from tests_support.aml_db import local_url
from tests_support.records_db import records_cleaned

SECRET_ALERT = "AL-should-never-leak"


@pytest.fixture(scope="module")
def records_engine() -> Iterator[Engine]:
    engine = create_engine(local_url(), echo=False, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def client(records_engine: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_records_engine] = lambda: records_engine
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _manifest(run_id: str, started: datetime) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "dataset": {"name": "smoke", "version": "v2", "sha256": "a" * 64},
        "git": {"commit": "abc123", "dirty": False},
        "model": {"profile": "scripted", "provider": "scripted", "model": "scripted"},
        "prompt": {"version": "v3", "sha256": "b" * 64},
        "limits": {"max_tool_steps": 12, "max_wall_seconds": 300},
        "started_at": started.isoformat(),
        "finished_at": (started + timedelta(seconds=30)).isoformat(),
        "counts": {
            "total": 4,
            "by_status": {"proposed": 3, "human_review": 1},
            "by_human_review_reason": {"schema_failed": 1},
            "alert_ids": [SECRET_ALERT],
        },
        "total_cost_usd": "0.012300",
    }


def _split(alerts: int) -> dict[str, Any]:
    return {
        "alerts": alerts,
        "baseline": {"raised": alerts, "true_strict": 1, "precision_strict": 0.25},
        "agent_with_human_review": {
            "strict": {"n": alerts, "tp": 1, "fp": 1, "precision": 0.5, "recall": 1.0},
            "loose": {"n": alerts, "precision": 1.0, "recall": 0.5},
        },
        "human_review": {"count": 1, "rate": 0.25, "reasons": {"schema_failed": 1}},
        "review": {"reviewed": 3, "rejected": 1, "reject_rate": 0.333333},
        "calibration": {"n": 3, "ece": 0.1, "bins": [], "indicative": True},
        "per_alert": [{"alert_id": SECRET_ALERT, "is_laundering": True}],
    }


SCORE: dict[str, Any] = {
    "run_id": "x",
    "headline": "early",
    "late_cutoff": "2022-09-10",
    "target_recall": 0.9,
    "splits": {"early": _split(4), "late": _split(0), "all": _split(4)},
    "lessons": {"enabled": False, "cases_with_lessons": 0, "lesson_ids": [7]},
    "notes": ["Small sets are indicative."],
    "alerts": [SECRET_ALERT],
}


def test_read_score_drops_unknown_fields() -> None:
    score = read_score(SCORE)
    assert score is not None
    dumped = score.model_dump_json()
    assert SECRET_ALERT not in dumped
    assert "per_alert" not in dumped
    assert read_score("not a dict") is None
    assert read_score({"splits": "broken"}) is None


def test_list_and_detail(client: TestClient, records_engine: Engine) -> None:
    now = datetime.now(UTC) + timedelta(days=3650)  # newer than any recorded run
    older = f"test-{uuid.uuid4().hex[:12]}"
    newer = f"test-{uuid.uuid4().hex[:12]}"
    with records_cleaned(records_engine):
        with records_engine.begin() as conn:
            record_eval_run(conn, _manifest(older, now))
            record_eval_run(conn, _manifest(newer, now + timedelta(minutes=5)))
            record_eval_result(conn, newer, SCORE, 0.9)

        listed = client.get("/evals", params={"limit": 2})
        assert listed.status_code == 200
        items = listed.json()["items"]
        assert [i["eval_run_id"] for i in items] == [newer, older]
        assert items[0]["scored"] is True
        assert items[0]["cases"] == 4
        assert items[0]["total_cost_usd"] == "0.012300"
        assert items[0]["headline"] == {
            "split": "early",
            "alerts": 4,
            "precision": 0.5,
            "recall": 1.0,
            "baseline_precision": 0.25,
            "human_review_rate": 0.25,
            "reviewer_reject_rate": 0.333333,
        }
        assert items[1]["scored"] is False
        assert items[1]["headline"] is None
        assert SECRET_ALERT not in listed.text

        detail = client.get(f"/evals/{newer}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["limits"] == {"max_tool_steps": 12, "max_wall_seconds": 300}
        assert body["counts"]["by_status"] == {"proposed": 3, "human_review": 1}
        assert body["score"]["splits"]["early"]["calibration"] == {
            "n": 3,
            "ece": 0.1,
            "indicative": True,
        }
        assert body["score"]["lessons"] == {"enabled": False, "cases_with_lessons": 0}
        assert SECRET_ALERT not in detail.text
        assert "per_alert" not in detail.text
        assert "is_laundering" not in detail.text

        unscored = client.get(f"/evals/{older}").json()
        assert unscored["score"] is None and unscored["scored_at"] is None


def test_detail_not_found_and_invalid_id(client: TestClient) -> None:
    assert client.get("/evals/test-does-not-exist").status_code == 404
    assert client.get("/evals/bad%20id").status_code == 422


def test_limit_is_capped(client: TestClient) -> None:
    assert client.get("/evals", params={"limit": 51}).status_code == 422
    assert client.get("/evals", params={"limit": 0}).status_code == 422
