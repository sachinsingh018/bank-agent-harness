"""Tool failures, keyset cursors and the response size cap. Pure code, no database."""

import base64
import binascii
from collections.abc import Callable, Sequence
from datetime import datetime

from pydantic import BaseModel

from tools.aml.caps import MAX_RESPONSE_CHARS
from tools.aml.models import ErrorCode, PageBase, ToolError


class ToolFailure(Exception):
    """A typed, safe failure. The message is always generic and never holds SQL."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message

    def to_error(self) -> ToolError:
        return ToolError(code=self.code, message=self.message)


def encode_cursor(ts: datetime, txn_id: int) -> str:
    raw = f"{ts.isoformat()}|{txn_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    bad = ToolFailure("invalid_argument", "invalid cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        ts_text, sep, id_text = raw.partition("|")
        if not sep:
            raise bad
        ts = datetime.fromisoformat(ts_text)
        txn_id = int(id_text)
    except (ValueError, binascii.Error, UnicodeError):
        raise bad from None
    if ts.tzinfo is not None or txn_id < 1:
        raise bad
    return ts, txn_id


def fit_items[T](items: Sequence[T], measure: Callable[[list[T]], int]) -> list[T]:
    """Drop items from the end until the measured size is within the cap."""
    kept = list(items)
    while kept and measure(kept) > MAX_RESPONSE_CHARS:
        kept.pop()
    if items and not kept:
        raise ToolFailure("too_large", "a single item is larger than the response cap")
    return kept


def make_page[P: PageBase, T](
    page_cls: type[P],
    items: Sequence[T],
    *,
    total_count: int,
    has_more: bool,
    cursor_of: Callable[[T], str],
    reserved: int = 0,
) -> P:
    """Build a page. Items that do not fit the cap are dropped, never hidden: truncated is set
    and next_cursor points at the last kept item so the caller can continue."""

    def build(kept: list[T], cut: bool) -> P:
        return page_cls(
            items=kept,  # type: ignore[call-arg]
            total_count=total_count,
            returned=len(kept),
            truncated=cut,
            next_cursor=cursor_of(kept[-1]) if kept and cut else None,
        )

    def measure(kept: list[T]) -> int:
        return len(build(kept, True).model_dump_json()) + reserved

    kept = fit_items(items, measure)
    cut = has_more or len(kept) < len(items)
    return build(kept, cut)


def json_size(model: BaseModel) -> int:
    return len(model.model_dump_json())
