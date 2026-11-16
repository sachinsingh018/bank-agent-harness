"""create corrections

Lessons a reviewer saves with a decision, for future cases. Append-only like case_events: the
records_refuse_change function from 0003 refuses UPDATE, DELETE and TRUNCATE. Nothing in the
agent reads this table yet (docs/plans/edits-and-corrections.md). The downgrade drops it.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KIND_LIST = "'recommendation', 'typology', 'confidence', 'evidence', 'other'"
REFUSE_FUNCTION = "records_refuse_change"  # created in 0003
TABLE = "corrections"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("module", sa.Text(), nullable=False),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("decision_event_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.CheckConstraint(f"kind IN ({KIND_LIST})", name="ck_corrections_kind_valid"),
        sa.CheckConstraint(
            "char_length(rule) BETWEEN 10 AND 500", name="ck_corrections_rule_length"
        ),
        sa.CheckConstraint("author LIKE 'human:_%'", name="ck_corrections_author_valid"),
        sa.ForeignKeyConstraint(
            ["run_id"], ["agent_runs.run_id"], name="fk_corrections_run_id_agent_runs"
        ),
        sa.ForeignKeyConstraint(
            ["decision_event_id"],
            ["case_events.id"],
            name="fk_corrections_decision_event_id_case_events",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_corrections"),
    )
    op.create_index("ix_corrections_module_created_at", TABLE, ["module", "created_at"])
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
    op.drop_index("ix_corrections_module_created_at", table_name=TABLE)
    op.drop_table(TABLE)
