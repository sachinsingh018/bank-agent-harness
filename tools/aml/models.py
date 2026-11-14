"""Typed inputs and outputs of the AML tools. Money is always an exact decimal string."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tools.aml.caps import DEFAULT_ROWS, MAX_ROWS

# ---------------------------------------------------------------- outputs


class AccountRef(BaseModel):
    bank: int
    acct: str


class Money(BaseModel):
    value: Annotated[str, Field(pattern=r"^-?[0-9]+(\.[0-9]+)?$")]
    currency: str

    @classmethod
    def of(cls, value: Decimal, currency: str) -> Self:
        return cls(value=format(value, "f"), currency=currency)


class TransferRecord(BaseModel):
    txn_id: int
    ts: datetime
    from_account: AccountRef
    to_account: AccountRef
    amount_paid: Money
    amount_received: Money
    format: str


class AlertTransfer(TransferRecord):
    role: str


class PageBase(BaseModel):
    """Shared list shape: how many matched, how many came back, and whether it was cut."""

    total_count: int
    returned: int
    truncated: bool
    next_cursor: str | None = None


class TransferPage(PageBase):
    items: list[TransferRecord]


class AlertTransferPage(PageBase):
    items: list[AlertTransfer]


class AlertRecord(BaseModel):
    alert_id: str
    ruleset_version: str
    detector: str
    anchor: AccountRef | None
    window_start: datetime
    window_end: datetime
    txn_count: int
    account_count: int
    params: dict[str, Any]
    summary: str
    transfers: AlertTransferPage


class BusiestDay(BaseModel):
    day: date
    transfers: int


class AccountSummary(BaseModel):
    account: AccountRef
    first_seen: datetime | None
    last_seen: datetime | None
    transfers_in: int
    transfers_out: int
    self_transfers: int
    distinct_counterparties_in: int
    distinct_counterparties_out: int
    currencies: list[str]
    formats: list[str]
    busiest_day: BusiestDay | None


class CounterpartyRecord(BaseModel):
    account: AccountRef
    transfers: int
    first_seen: datetime
    last_seen: datetime
    sent_transfers: int
    received_transfers: int
    sent_totals_by_currency: list[Money]
    received_totals_by_currency: list[Money]


class CounterpartiesResult(BaseModel):
    account: AccountRef
    items: list[CounterpartyRecord]
    total_count: int
    returned: int
    truncated: bool


ErrorCode = Literal["not_found", "invalid_argument", "too_large", "internal"]


class ToolError(BaseModel):
    code: ErrorCode
    message: str


# ----------------------------------------------------------------- inputs

Bank = Annotated[int, Field(ge=0, strict=True)]
Acct = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{1,32}$")]
AlertId = Annotated[str, Field(pattern=r"^AL-[0-9a-f]{12}$")]
TxnId = Annotated[int, Field(ge=1, strict=True)]
Limit = Annotated[int, Field(ge=1, le=MAX_ROWS, strict=True)]
Cursor = Annotated[str, Field(min_length=1, max_length=200)]
Direction = Literal["in", "out", "both"]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlertIdInput(_Input):
    alert_id: AlertId


class AlertTransactionsInput(_Input):
    alert_id: AlertId
    cursor: Cursor | None = None
    limit: Limit = DEFAULT_ROWS


class TransactionInput(_Input):
    txn_id: TxnId


class AccountInput(_Input):
    bank: Bank
    acct: Acct


class _WindowInput(_Input):
    start: datetime | None = None
    end: datetime | None = None

    @field_validator("start", "end")
    @classmethod
    def _naive_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must not be after end")
        return self


class AccountHistoryInput(_WindowInput):
    bank: Bank
    acct: Acct
    direction: Direction = "both"
    cursor: Cursor | None = None
    limit: Limit = DEFAULT_ROWS


class CounterpartiesInput(_WindowInput):
    bank: Bank
    acct: Acct
    direction: Direction = "both"
