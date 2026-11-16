"""create eval_runs, eval_results, agent_runs, model_calls, case_events

case_events, agent_runs and model_calls are append-only (triggers refuse UPDATE, DELETE and
TRUNCATE). The downgrade refuses to drop a non-empty audit log unless the dev only escape
`alembic -x allow_audit_drop=1 downgrade 0002` is given.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EVENT_LIST = "'proposal_created', 'human_review_created', 'approved', 'rejected', 'edited'"

REFUSE_FUNCTION = "records_refuse_change"
APPEND_ONLY_TABLES = ("case_events", "agent_runs", "model_calls")
AUDIT_DROP_FLAG = "allow_audit_drop"


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("eval_run_id", sa.Text(), nullable=False),
        sa.Column("dataset", sa.Text(), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("dataset_sha256", sa.Text(), nullable=False),
        sa.Column("git_commit", sa.Text(), nullable=True),
        sa.Column("git_dirty", sa.Boolean(), nullable=True),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.Text(), nullable=False),
        sa.Column("limits", _jsonb(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("counts", _jsonb(), nullable=True),
        sa.Column("total_cost_usd", sa.Numeric(14, 6), nullable=True),
        sa.PrimaryKeyConstraint("eval_run_id", name="pk_eval_runs"),
    )
    op.create_table(
        "eval_results",
        sa.Column("eval_run_id", sa.Text(), nullable=False),
        sa.Column("scored_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("score", _jsonb(), nullable=False),
        sa.Column("target_recall", sa.Numeric(), nullable=False),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["eval_runs.eval_run_id"],
            name="fk_eval_results_eval_run_id_eval_runs",
        ),
        sa.PrimaryKeyConstraint("eval_run_id", name="pk_eval_results"),
    )
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("findings", _jsonb(), nullable=True),
        sa.Column("verifier_results", _jsonb(), nullable=False),
        sa.Column("totals", _jsonb(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("tool_steps", sa.Integer(), nullable=False),
        sa.Column("wall_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.Text(), nullable=False),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("eval_run_id", sa.Text(), nullable=True),
        sa.Column("trace_path", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'human_review')", name="ck_agent_runs_status_valid"
        ),
        sa.ForeignKeyConstraint(
            ["eval_run_id"],
            ["eval_runs.eval_run_id"],
            name="fk_agent_runs_eval_run_id_eval_runs",
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_agent_runs"),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"])
    op.create_index("ix_agent_runs_eval_run_id", "agent_runs", ["eval_run_id"])
    op.create_table(
        "model_calls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.run_id"],
            name="fk_model_calls_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_calls"),
    )
    op.create_index("ix_model_calls_run_id", "model_calls", ["run_id"])
    op.create_table(
        "case_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column("payload", _jsonb(), nullable=False),
        sa.Column("prev_hash", sa.Text(), nullable=True),
        sa.Column("hash", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "actor = 'agent-runner' OR actor LIKE 'human:_%'", name="ck_case_events_actor_valid"
        ),
        sa.CheckConstraint(f"event IN ({EVENT_LIST})", name="ck_case_events_event_valid"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.run_id"], name="fk_case_events_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_case_events"),
        sa.UniqueConstraint("hash", name="uq_case_events_hash"),
        sa.UniqueConstraint("prev_hash", name="uq_case_events_prev_hash"),
    )
    op.create_index("ix_case_events_case_id", "case_events", ["case_id"])
    # At most one row may start the chain (prev_hash NULL).
    op.create_index(
        "uq_case_events_one_chain_start",
        "case_events",
        [sa.text("(true)")],
        unique=True,
        postgresql_where=sa.text("prev_hash IS NULL"),
    )
    op.execute(
        f"""
        CREATE FUNCTION {REFUSE_FUNCTION}() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP;
        END;
        $$
        """
    )
    for table in APPEND_ONLY_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_no_update_delete BEFORE UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {REFUSE_FUNCTION}()"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_truncate BEFORE TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION {REFUSE_FUNCTION}()"
        )


def downgrade() -> None:
    rows = op.get_bind().execute(sa.text("SELECT count(*) FROM case_events")).scalar_one()
    allowed = context.get_x_argument(as_dictionary=True).get(AUDIT_DROP_FLAG) == "1"
    if rows and not allowed:
        raise RuntimeError(
            f"case_events holds {rows} audit row(s); refusing to drop the audit log. Dev only "
            f"escape: alembic -x {AUDIT_DROP_FLAG}=1 downgrade 0002"
        )
    for table in APPEND_ONLY_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_truncate ON {table}")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_delete ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {REFUSE_FUNCTION}()")
    op.drop_index("uq_case_events_one_chain_start", table_name="case_events")
    op.drop_index("ix_case_events_case_id", table_name="case_events")
    op.drop_table("case_events")
    op.drop_index("ix_model_calls_run_id", table_name="model_calls")
    op.drop_table("model_calls")
    op.drop_index("ix_agent_runs_eval_run_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_case_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_table("eval_results")
    op.drop_table("eval_runs")
