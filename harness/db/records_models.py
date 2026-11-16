"""SQLAlchemy 2.0 models for the harness records: the audit log, run records and eval runs.

case_events is the append-only audit log (a trigger refuses UPDATE, DELETE and TRUNCATE, and
a hash chain makes tampering visible). The other tables are plain run records. Only the write
path in harness/records.py writes here, through HARNESS_WRITE_DATABASE_URL. Names follow
docs/brief.md section 9.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from harness.db.models import Base

USD = Numeric(14, 6)

CASE_EVENT_TYPES = ("proposal_created", "human_review_created", "approved", "rejected", "edited")
_EVENT_LIST = ", ".join(f"'{e}'" for e in CASE_EVENT_TYPES)
AGENT_RUNNER_ACTOR = "agent-runner"
RUN_STATUSES = ("proposed", "human_review")


class EvalRun(Base):
    __tablename__ = "eval_runs"

    eval_run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dataset: Mapped[str] = mapped_column(Text)
    dataset_version: Mapped[str] = mapped_column(Text)
    dataset_sha256: Mapped[str] = mapped_column(Text)
    git_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_dirty: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    profile: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    prompt_version: Mapped[str] = mapped_column(Text)
    prompt_sha256: Mapped[str] = mapped_column(Text)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    total_cost_usd: Mapped[Decimal | None] = mapped_column(USD, nullable=True)


class EvalResult(Base):
    """The scorer's output for one eval run: counts and rates only, no per alert truth."""

    __tablename__ = "eval_results"

    eval_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("eval_runs.eval_run_id"), primary_key=True
    )
    scored_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True))
    score: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_recall: Mapped[Decimal] = mapped_column(Numeric)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint("status IN ('proposed', 'human_review')", name="status_valid"),
        Index("ix_agent_runs_case_id", "case_id"),
        Index("ix_agent_runs_eval_run_id", "eval_run_id"),
    )

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    case_id: Mapped[str] = mapped_column(Text)
    module: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    verifier_results: Mapped[list[Any]] = mapped_column(JSONB)
    totals: Mapped[dict[str, Any]] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal] = mapped_column(USD)
    tool_steps: Mapped[int] = mapped_column(Integer)
    wall_ms: Mapped[int] = mapped_column(Integer)
    prompt_version: Mapped[str] = mapped_column(Text)
    prompt_sha256: Mapped[str] = mapped_column(Text)
    profile: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    eval_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("eval_runs.eval_run_id"), nullable=True
    )
    trace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class ModelCall(Base):
    __tablename__ = "model_calls"
    __table_args__ = (Index("ix_model_calls_run_id", "run_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[str] = mapped_column(Text, ForeignKey("agent_runs.run_id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cache_read_tokens: Mapped[int] = mapped_column(Integer)
    cache_write_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(USD)
    latency_ms: Mapped[int] = mapped_column(Integer)
    stop_reason: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)


class CaseEvent(Base):
    """Append-only audit log. Every row carries the hash of the row before it."""

    __tablename__ = "case_events"
    __table_args__ = (
        CheckConstraint(
            f"actor = '{AGENT_RUNNER_ACTOR}' OR actor LIKE 'human:_%'", name="actor_valid"
        ),
        CheckConstraint(f"event IN ({_EVENT_LIST})", name="event_valid"),
        Index("ix_case_events_case_id", "case_id"),
        # At most one row may start the chain (prev_hash NULL); prev_hash is unique, so the
        # chain cannot fork.
        Index(
            "uq_case_events_one_chain_start",
            text("(true)"),
            unique=True,
            postgresql_where=text("prev_hash IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    actor: Mapped[str] = mapped_column(Text)
    event: Mapped[str] = mapped_column(Text)
    case_id: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(Text, ForeignKey("agent_runs.run_id"), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    prev_hash: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    hash: Mapped[str] = mapped_column(Text, unique=True)
