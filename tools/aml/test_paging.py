"""Pure tests: cursors and the response size cap."""

import base64
from datetime import datetime

import pytest

from tools.aml.caps import MAX_RESPONSE_CHARS
from tools.aml.models import AccountRef, Money, TransferPage, TransferRecord
from tools.aml.paging import ToolFailure, decode_cursor, encode_cursor, make_page


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_cursor_round_trip() -> None:
    ts = datetime(2022, 9, 1, 12, 30, 5, 123456)
    assert decode_cursor(encode_cursor(ts, 42)) == (ts, 42)
    assert decode_cursor(encode_cursor(datetime(2022, 9, 1), 1)) == (datetime(2022, 9, 1), 1)


@pytest.mark.parametrize(
    "cursor",
    [
        "!!!",
        "abc",
        _b64("no separator"),
        _b64("not-a-date|5"),
        _b64("2022-09-01T00:00:00|abc"),
        _b64("2022-09-01T00:00:00|0"),
        _b64("2022-09-01T00:00:00|-3"),
        _b64("2022-09-01T00:00:00+00:00|5"),
        _b64("|5"),
        "éé",
    ],
)
def test_invalid_cursors(cursor: str) -> None:
    with pytest.raises(ToolFailure) as info:
        decode_cursor(cursor)
    assert info.value.code == "invalid_argument"


def _record(i: int, pad: int = 0) -> TransferRecord:
    return TransferRecord(
        txn_id=i,
        ts=datetime(2022, 9, 1, 0, 0, i % 60),
        from_account=AccountRef(bank=1, acct="A"),
        to_account=AccountRef(bank=2, acct="B"),
        amount_paid=Money(value="1.000000", currency="USD"),
        amount_received=Money(value="1.000000", currency="USD"),
        format="W" * pad,
    )


def _cursor_of(item: TransferRecord) -> str:
    return encode_cursor(item.ts, item.txn_id)


def test_small_page_is_untouched() -> None:
    items = [_record(i) for i in range(1, 6)]
    page = make_page(TransferPage, items, total_count=5, has_more=False, cursor_of=_cursor_of)
    assert page.returned == 5 and page.total_count == 5
    assert not page.truncated and page.next_cursor is None
    assert [r.txn_id for r in page.items] == [1, 2, 3, 4, 5]


def test_has_more_sets_truncated_and_cursor_of_last_item() -> None:
    items = [_record(i) for i in range(1, 4)]
    page = make_page(TransferPage, items, total_count=90, has_more=True, cursor_of=_cursor_of)
    assert page.truncated and page.returned == 3
    assert page.next_cursor == _cursor_of(items[-1])


def test_oversize_page_drops_from_the_end() -> None:
    items = [_record(i, pad=3000) for i in range(1, 11)]
    page = make_page(TransferPage, items, total_count=10, has_more=False, cursor_of=_cursor_of)
    assert len(page.model_dump_json()) <= MAX_RESPONSE_CHARS
    assert page.truncated
    assert 0 < page.returned < 10
    assert page.returned == len(page.items)
    assert page.total_count == 10
    assert [r.txn_id for r in page.items] == list(range(1, page.returned + 1))
    assert page.next_cursor == _cursor_of(page.items[-1])
    # adding one more item would have broken the cap, so nothing fitting was dropped
    more = TransferPage(
        items=items[: page.returned + 1],
        total_count=10,
        returned=page.returned + 1,
        truncated=True,
        next_cursor=page.next_cursor,
    )
    assert len(more.model_dump_json()) > MAX_RESPONSE_CHARS


def test_reserved_space_is_counted() -> None:
    items = [_record(i, pad=1000) for i in range(1, 11)]
    free = make_page(TransferPage, items, total_count=10, has_more=False, cursor_of=_cursor_of)
    tight = make_page(
        TransferPage, items, total_count=10, has_more=False, cursor_of=_cursor_of, reserved=6000
    )
    assert tight.returned < free.returned
    assert tight.truncated


def test_single_item_over_the_cap_is_too_large() -> None:
    with pytest.raises(ToolFailure) as info:
        make_page(
            TransferPage,
            [_record(1, pad=MAX_RESPONSE_CHARS)],
            total_count=1,
            has_more=False,
            cursor_of=_cursor_of,
        )
    assert info.value.code == "too_large"


def test_empty_page() -> None:
    page = make_page(TransferPage, [], total_count=0, has_more=False, cursor_of=_cursor_of)
    assert page.returned == 0 and not page.truncated and page.next_cursor is None
