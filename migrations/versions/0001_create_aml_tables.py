"""create accounts, transactions, transaction_labels

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ATTEMPT_LIST = (
    "'BIPARTITE', 'CYCLE', 'FAN-IN', 'FAN-OUT', 'GATHER-SCATTER', "
    "'RANDOM', 'SCATTER-GATHER', 'STACK'"
)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("bank", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("acct", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("bank", "acct", name="pk_accounts"),
    )
    op.create_table(
        "transactions",
        sa.Column("txn_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=False), nullable=False),
        sa.Column("from_bank", sa.Integer(), nullable=False),
        sa.Column("from_acct", sa.Text(), nullable=False),
        sa.Column("to_bank", sa.Integer(), nullable=False),
        sa.Column("to_acct", sa.Text(), nullable=False),
        sa.Column("amt_recv", sa.Numeric(20, 6), nullable=False),
        sa.Column("amt_paid", sa.Numeric(20, 6), nullable=False),
        sa.Column("cur_recv", sa.Text(), nullable=False),
        sa.Column("cur_paid", sa.Text(), nullable=False),
        sa.Column("fmt", sa.Text(), nullable=False),
        sa.Column("row_hash", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["from_bank", "from_acct"],
            ["accounts.bank", "accounts.acct"],
            name="fk_transactions_from_accounts",
        ),
        sa.ForeignKeyConstraint(
            ["to_bank", "to_acct"],
            ["accounts.bank", "accounts.acct"],
            name="fk_transactions_to_accounts",
        ),
        sa.PrimaryKeyConstraint("txn_id", name="pk_transactions"),
        sa.UniqueConstraint("row_hash", name="uq_transactions_row_hash"),
    )
    op.create_index("ix_transactions_from", "transactions", ["from_bank", "from_acct"])
    op.create_index("ix_transactions_to", "transactions", ["to_bank", "to_acct"])
    op.create_index("ix_transactions_ts", "transactions", ["ts"])
    op.create_table(
        "transaction_labels",
        sa.Column("txn_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("is_laundering", sa.Boolean(), nullable=False),
        sa.Column("attempt_id", sa.Integer(), nullable=True),
        sa.Column("attempt_type", sa.Text(), nullable=True),
        sa.CheckConstraint(
            f"attempt_type IN ({ATTEMPT_LIST})",
            name="ck_transaction_labels_attempt_type_valid",
        ),
        sa.CheckConstraint(
            "(is_laundering AND attempt_id IS NOT NULL AND attempt_type IS NOT NULL) "
            "OR (NOT is_laundering AND attempt_id IS NULL AND attempt_type IS NULL)",
            name="ck_transaction_labels_attempt_matches_laundering",
        ),
        sa.ForeignKeyConstraint(
            ["txn_id"], ["transactions.txn_id"], name="fk_transaction_labels_txn_id_transactions"
        ),
        sa.PrimaryKeyConstraint("txn_id", name="pk_transaction_labels"),
    )


def downgrade() -> None:
    op.drop_table("transaction_labels")
    op.drop_index("ix_transactions_ts", table_name="transactions")
    op.drop_index("ix_transactions_to", table_name="transactions")
    op.drop_index("ix_transactions_from", table_name="transactions")
    op.drop_table("transactions")
    op.drop_table("accounts")
