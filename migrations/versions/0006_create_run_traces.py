"""create run_traces

The stored steps of each agent run (docs/plans/investigation-replay.md): the maker's RunTrace and
the checker's, with tool result text capped, plus a small case card (the alert as the agent read
it) for the case gallery. Append-only like agent_runs: the records_refuse_change function from
0003 refuses UPDATE, DELETE and TRUNCATE. A separate table, because the run digest covers every
agent_runs column and a new column there would break the digests of runs already recorded. The
case event of a run recorded with its trace carries trace_digest instead. The downgrade drops it.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REFUSE_FUNCTION = "records_refuse_change"  # created in 0003
TABLE = "run_traces"


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("trace", _jsonb(), nullable=False),
        sa.Column("case_card", _jsonb(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.run_id"], name="fk_run_traces_run_id_agent_runs"
        ),
        sa.PrimaryKeyConstraint("run_id", name="pk_run_traces"),
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
