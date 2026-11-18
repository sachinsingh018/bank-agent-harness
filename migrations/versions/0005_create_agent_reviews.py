"""create agent_reviews

The checker's review of an agent run (maker-checker, docs/plans/maker-checker.md). Append-only
like agent_runs: the records_refuse_change function from 0003 refuses UPDATE, DELETE and
TRUNCATE. A separate table, because the run digest covers every agent_runs column and a new
column there would break the digests of runs already recorded. The downgrade drops it.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFUSE_FUNCTION = "records_refuse_change"  # created in 0003
TABLE = "agent_reviews"


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("review", _jsonb(), nullable=True),
        sa.Column("verifier_results", _jsonb(), nullable=False),
        sa.Column("usage", _jsonb(), nullable=False),
        sa.Column("cost_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("tool_steps", sa.Integer(), nullable=False),
        sa.Column("wall_ms", sa.Integer(), nullable=False),
        sa.Column("profile", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Text(), nullable=False),
        sa.Column("prompt_sha256", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('approve', 'reject')",
            name="ck_agent_reviews_verdict_valid",
        ),
        sa.CheckConstraint(
            "(verdict IS NULL) <> (failure_reason IS NULL)",
            name="ck_agent_reviews_verdict_or_failure",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.run_id"], name="fk_agent_reviews_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_agent_reviews"),
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_update_delete BEFORE UPDATE OR DELETE ON {TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {REFUSE_FUNCTION}()"
    )
    op.execute(
        f"CREATE TRIGGER {TABLE}_no_truncate BEFORE TRUNCATE ON {TABLE} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {REFUSE_FUNCTION}()"
    )


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_truncate ON {TABLE}")
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_update_delete ON {TABLE}")
    op.drop_table(TABLE)
