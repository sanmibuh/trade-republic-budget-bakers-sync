from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.wallet_client import (
    WalletClient,
    _to_decimal,
    _get_first_match,
    extract_amount,
    normalize_event_time,
    sync_event_to_wallet,
)


# ---------------------------------------------------------------------------
# _to_decimal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, Decimal("0")),
    (0, Decimal("0")),
    (5, Decimal("5")),
    (3.14, Decimal("3.14")),
    (Decimal("1.5"), Decimal("1.5")),
    ("10.50", Decimal("10.50")),
    ("10,50", Decimal("10.50")),
    ("€ 9.99", Decimal("9.99")),
    ("", Decimal("0")),
    ("  ", Decimal("0")),
    ("not-a-number", Decimal("0")),
])
def test_to_decimal(value, expected):
    assert _to_decimal(value) == expected


# ---------------------------------------------------------------------------
# _get_first_match
# ---------------------------------------------------------------------------

def test_get_first_match_returns_first_found():
    assert _get_first_match({"b": 2, "a": 1}, "a", "b") == 1


def test_get_first_match_skips_missing():
    assert _get_first_match({"b": 2}, "a", "b") == 2


def test_get_first_match_returns_none_when_absent():
    assert _get_first_match({"c": 3}, "a", "b") is None


# ---------------------------------------------------------------------------
# extract_amount
# ---------------------------------------------------------------------------

def test_extract_amount_top_level():
    assert extract_amount({"amount": "50.00"}, "amount") == Decimal("50.00")


def test_extract_amount_nested_dict():
    event = {"amount": {"value": "25.00"}}
    assert extract_amount(event, "value") == Decimal("25.00")


def test_extract_amount_missing_returns_zero():
    assert extract_amount({}, "amount") == Decimal("0")


# ---------------------------------------------------------------------------
# normalize_event_time
# ---------------------------------------------------------------------------

def test_normalize_event_time_timestamp_key():
    event = {"timestamp": "2024-01-01T10:00:00Z"}
    assert normalize_event_time(event) == "2024-01-01T10:00:00Z"


def test_normalize_event_time_createdAt_key():
    event = {"createdAt": "2024-03-01T12:00:00Z"}
    assert normalize_event_time(event) == "2024-03-01T12:00:00Z"


def test_normalize_event_time_date_key():
    event = {"date": "2024-05-01"}
    assert normalize_event_time(event) == "2024-05-01"


def test_normalize_event_time_datetime_object():
    dt = datetime(2024, 6, 1, 9, 0, 0, tzinfo=timezone.utc)
    event = {"timestamp": dt}
    result = normalize_event_time(event)
    assert "2024-06-01" in result


def test_normalize_event_time_fallback_is_isoformat():
    # No known keys → returns current time as ISO string
    result = normalize_event_time({})
    # Should parse without error
    datetime.fromisoformat(result.replace("Z", "+00:00"))


def test_normalize_event_time_prefers_timestamp_over_date():
    event = {"timestamp": "2024-01-01T10:00:00Z", "date": "2024-01-02"}
    assert normalize_event_time(event) == "2024-01-01T10:00:00Z"


# ---------------------------------------------------------------------------
# sync_event_to_wallet
# ---------------------------------------------------------------------------

def _mock_wallet():
    client = MagicMock(spec=WalletClient)
    client.post_record = MagicMock(return_value={})
    return client


def test_sync_interest_payment_no_tax():
    client = _mock_wallet()
    event = {"eventType": "INTEREST_PAYMENT", "timestamp": "2024-01-01T00:00:00Z", "amount": "10.00", "title": "Interest"}
    sync_event_to_wallet(client, event, cash_account_id="cash", portfolio_account_id="port")

    client.post_record.assert_called_once()
    call_kwargs = client.post_record.call_args.kwargs
    assert call_kwargs["account_id"] == "cash"
    assert call_kwargs["tx_type"] == "income"
    assert call_kwargs["category"] == "Interests"
    assert call_kwargs["amount"] == Decimal("10.00")


def test_sync_interest_payment_with_tax():
    client = _mock_wallet()
    event = {
        "eventType": "INTEREST_PAYMENT",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "10.00",
        "tax": "2.50",
        "title": "Interest",
    }
    sync_event_to_wallet(client, event, cash_account_id="cash", portfolio_account_id="port")

    assert client.post_record.call_count == 2
    tax_call = client.post_record.call_args_list[1].kwargs
    assert tax_call["amount"] == Decimal("-2.50")
    assert tax_call["tx_type"] == "expense"
    assert tax_call["category"] == "Taxes"


@pytest.mark.parametrize("event_type", ["BUY_ORDER", "SAVINGS_PLAN", "SELL_ORDER"])
def test_sync_order_events_use_transfer(event_type):
    client = _mock_wallet()
    event = {"eventType": event_type, "timestamp": "2024-01-01T00:00:00Z", "amount": "200.00"}
    sync_event_to_wallet(client, event, cash_account_id="cash", portfolio_account_id="port")

    client.post_record.assert_called_once()
    call_kwargs = client.post_record.call_args.kwargs
    assert call_kwargs["transfer_account_id"] == "port"
    assert call_kwargs["account_id"] == "cash"


def test_sync_saveback_no_tax():
    client = _mock_wallet()
    event = {"eventType": "SAVEBACK", "timestamp": "2024-01-01T00:00:00Z", "amount": "5.00", "title": "Saveback"}
    sync_event_to_wallet(client, event, cash_account_id="cash", portfolio_account_id="port")

    client.post_record.assert_called_once()
    call_kwargs = client.post_record.call_args.kwargs
    assert call_kwargs["account_id"] == "port"
    assert call_kwargs["category"] == "Cashback / Bonuses"


def test_sync_saveback_with_tax():
    client = _mock_wallet()
    event = {
        "eventType": "SAVEBACK",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "5.00",
        "tax": "1.00",
        "title": "Saveback",
    }
    sync_event_to_wallet(client, event, cash_account_id="cash", portfolio_account_id="port")
    assert client.post_record.call_count == 2


def test_sync_unknown_event_type_posts_to_cash():
    client = _mock_wallet()
    event = {"eventType": "UNKNOWN_EVENT", "timestamp": "2024-01-01T00:00:00Z", "amount": "1.00"}
    sync_event_to_wallet(client, event, cash_account_id="cash", portfolio_account_id="port")

    client.post_record.assert_called_once()
    assert client.post_record.call_args.kwargs["account_id"] == "cash"


def test_sync_uses_lowercase_type_field():
    client = _mock_wallet()
    event = {"type": "interest_payment", "timestamp": "2024-01-01T00:00:00Z", "amount": "3.00"}
    sync_event_to_wallet(client, event, cash_account_id="cash", portfolio_account_id="port")
    # type is uppercased internally, so INTEREST_PAYMENT branch fires
    call_kwargs = client.post_record.call_args.kwargs
    assert call_kwargs["tx_type"] == "income"
