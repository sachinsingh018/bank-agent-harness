"""create alerts, alert_transactions

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DETECTOR_LIST = (
    "'fan-out', 'fan-in', 'cycle', 'scatter-gather', 'gather-scatter', "
    "'stack', 'bipartite', 'structuring', 'volume-spike'"
)


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.Text(), nullable=False),
        sa.Column("ruleset_version", sa.Text(), nullable=False),
        sa.Column("detector", sa.Text(), nullable=False),
        sa.Column("anchor_bank", sa.Integer(), nullable=True),
        sa.Column("anchor_acct", sa.Text(), nullable=True),
        sa.Column("window_start", sa.TIMESTAMP(timezone=False), nullable=False),
        sa.Column("window_end", sa.TIMESTAMP(timezone=False), nullable=False),
        sa.Column("txn_count", sa.Integer(), nullable=False),
        sa.Column("account_count", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(f"detector IN ({DETECTOR_LIST})", name="ck_alerts_detector_valid"),
        sa.PrimaryKeyConstraint("alert_id", name="pk_alerts"),
    )
    op.create_index("ix_alerts_ruleset_detector", "alerts", ["ruleset_version", "detector"])
    op.create_table(
        "alert_transactions",
        sa.Column("alert_id", sa.Text(), nullable=False),
        sa.Column("txn_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.alert_id"],
            name="fk_alert_transactions_alert_id_alerts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["txn_id"], ["transactions.txn_id"], name="fk_alert_transactions_txn_id_transactions"
        ),
        sa.PrimaryKeyConstraint("alert_id", "txn_id", name="pk_alert_transactions"),
    )


def downgrade() -> None:
    op.drop_table("alert_transactions")
    op.drop_index("ix_alerts_ruleset_detector", table_name="alerts")
    op.drop_table("alerts")
