"""SQLAlchemy 2.0 models for the AML slice. transaction_labels is the eval answer key."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Scan of the slice: max 6 decimal places, max 10 integer digits, so Numeric(20, 6) is exact.
MONEY = Numeric(20, 6)

ATTEMPT_TYPES = (
    "BIPARTITE",
    "CYCLE",
    "FAN-IN",
    "FAN-OUT",
    "GATHER-SCATTER",
    "RANDOM",
    "SCATTER-GATHER",
    "STACK",
)
_ATTEMPT_LIST = ", ".join(f"'{t}'" for t in ATTEMPT_TYPES)

DETECTORS = (
    "fan-out",
    "fan-in",
    "cycle",
    "scatter-gather",
    "gather-scatter",
    "stack",
    "bipartite",
    "structuring",
    "volume-spike",
)
_DETECTOR_LIST = ", ".join(f"'{d}'" for d in DETECTORS)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Account(Base):
    __tablename__ = "accounts"

    bank: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    acct: Mapped[str] = mapped_column(Text, primary_key=True)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["from_bank", "from_acct"],
            ["accounts.bank", "accounts.acct"],
            name="fk_transactions_from_accounts",
        ),
        ForeignKeyConstraint(
            ["to_bank", "to_acct"],
            ["accounts.bank", "accounts.acct"],
            name="fk_transactions_to_accounts",
        ),
        Index("ix_transactions_from", "from_bank", "from_acct"),
        Index("ix_transactions_to", "to_bank", "to_acct"),
        Index("ix_transactions_ts", "ts"),
    )

    txn_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ts: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False))
    from_bank: Mapped[int] = mapped_column(Integer)
    from_acct: Mapped[str] = mapped_column(Text)
    to_bank: Mapped[int] = mapped_column(Integer)
    to_acct: Mapped[str] = mapped_column(Text)
    amt_recv: Mapped[Decimal] = mapped_column(MONEY)
    amt_paid: Mapped[Decimal] = mapped_column(MONEY)
    cur_recv: Mapped[str] = mapped_column(Text)
    cur_paid: Mapped[str] = mapped_column(Text)
    fmt: Mapped[str] = mapped_column(Text)
    row_hash: Mapped[str] = mapped_column(Text, unique=True)


class TransactionLabel(Base):
    __tablename__ = "transaction_labels"
    __table_args__ = (
        CheckConstraint(f"attempt_type IN ({_ATTEMPT_LIST})", name="attempt_type_valid"),
        CheckConstraint(
            "(is_laundering AND attempt_id IS NOT NULL AND attempt_type IS NOT NULL) "
            "OR (NOT is_laundering AND attempt_id IS NULL AND attempt_type IS NULL)",
            name="attempt_matches_laundering",
        ),
    )

    txn_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.txn_id"), primary_key=True, autoincrement=False
    )
    is_laundering: Mapped[bool] = mapped_column(Boolean)
    attempt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_type: Mapped[str | None] = mapped_column(Text, nullable=True)


class Alert(Base):
    """Derived data written by the rules layer. Holds no label information."""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint(f"detector IN ({_DETECTOR_LIST})", name="detector_valid"),
        Index("ix_alerts_ruleset_detector", "ruleset_version", "detector"),
    )

    alert_id: Mapped[str] = mapped_column(Text, primary_key=True)
    ruleset_version: Mapped[str] = mapped_column(Text)
    detector: Mapped[str] = mapped_column(Text)
    anchor_bank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anchor_acct: Mapped[str | None] = mapped_column(Text, nullable=True)
    window_start: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False))
    window_end: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=False))
    txn_count: Mapped[int] = mapped_column(Integer)
    account_count: Mapped[int] = mapped_column(Integer)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now()
    )


class AlertTransaction(Base):
    __tablename__ = "alert_transactions"

    alert_id: Mapped[str] = mapped_column(
        Text, ForeignKey("alerts.alert_id", ondelete="CASCADE"), primary_key=True
    )
    txn_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("transactions.txn_id"), primary_key=True, autoincrement=False
    )
    role: Mapped[str] = mapped_column(Text)
