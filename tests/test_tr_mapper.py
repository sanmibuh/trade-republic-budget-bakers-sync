from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.tr_mapper import (
    _to_decimal,
    _get_first_match,
    extract_amount,
    normalize_event_time,
    build_records_for_event,
    filter_by_lookback,
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


def test_to_decimal_unsupported_type_returns_zero():
    """An unsupported type (e.g. list) should fall through to the final return Decimal('0')."""
    assert _to_decimal([1, 2, 3]) == Decimal("0")
    assert _to_decimal({"value": 5}) == Decimal("0")


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


def test_extract_amount_tr_dict_format():
    """TR sends amount as {"value": 100.0, "currency": "EUR"} — must unwrap value."""
    event = {"amount": {"value": 100.0, "currency": "EUR"}}
    assert extract_amount(event, "amount") == Decimal("100.0")


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
    result = normalize_event_time({})
    datetime.fromisoformat(result.replace("Z", "+00:00"))


def test_normalize_event_time_prefers_timestamp_over_date():
    event = {"timestamp": "2024-01-01T10:00:00Z", "date": "2024-01-02"}
    assert normalize_event_time(event) == "2024-01-01T10:00:00Z"


def test_normalize_event_time_fixes_numeric_tz_offset():
    """TR sends +0000 / +0200 without colon — must be normalised to +00:00 / +02:00."""
    event = {"timestamp": "2026-08-06T22:29:39.067+0000"}
    assert normalize_event_time(event) == "2026-08-06T22:29:39.067+00:00"


def test_normalize_event_time_fixes_nonzero_tz_offset():
    event = {"timestamp": "2026-08-06T22:29:39.067+0200"}
    assert normalize_event_time(event) == "2026-08-06T22:29:39.067+02:00"


# ---------------------------------------------------------------------------
# build_records_for_event
# ---------------------------------------------------------------------------

def test_build_interest_payment_no_tax():
    event = {
        "eventType": "INTEREST_PAYMENT",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "10.00",
        "title": "Zinsen",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    r = records[0]
    assert r["accountId"] == "cash"
    assert r["amount"] == {"value": 10.0}
    assert r["note"] == "Interest Payment: Zinsen"
    assert r["paymentType"] == "web_payment"
    assert "transfer" not in r


def test_build_interest_payout_note_format():
    event = {
        "eventType": "INTEREST_PAYOUT",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "63.73",
        "title": "Zinsen",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    assert records[0]["note"] == "Interest Payout: Zinsen"
    assert records[0]["accountId"] == "cash"
    assert records[0]["paymentType"] == "web_payment"


def test_build_interest_no_tr_title_uses_mapped():
    event = {"eventType": "INTEREST_PAYOUT", "timestamp": "2024-01-01T00:00:00Z", "amount": "10.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Interest Payout"


def test_build_unknown_event_no_title_uses_event_type():
    event = {"eventType": "SOME_FUTURE_TYPE", "timestamp": "2024-01-01T00:00:00Z", "amount": "1.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "SOME_FUTURE_TYPE"


@pytest.mark.parametrize("event_type", ["BUY_ORDER", "SAVINGS_PLAN", "SELL_ORDER", "TRADING_SAVINGSPLAN_EXECUTED", "SAVEBACK_AGGREGATE", "SPARE_CHANGE_AGGREGATE"])
def test_build_order_events_use_transfer(event_type):
    event = {"eventType": event_type, "timestamp": "2024-01-01T00:00:00Z", "amount": "200.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    r = records[0]
    assert r["accountId"] == "cash"
    assert r["transfer"] == {"pairingMode": "new", "accountId": "port"}
    assert r["paymentType"] == "transfer"


def test_build_saveback_no_tax():
    event = {"eventType": "SAVEBACK", "timestamp": "2024-01-01T00:00:00Z", "amount": "5.00", "title": "Saveback"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    assert records[0]["accountId"] == "port"
    assert records[0]["amount"] == {"value": 5.0}


def test_build_card_transaction_uses_debit_card():
    event = {"eventType": "CARD_TRANSACTION", "timestamp": "2024-01-01T00:00:00Z", "amount": "-20.00", "title": "Supermarket"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    assert records[0]["paymentType"] == "debit_card"
    assert records[0]["accountId"] == "cash"
    assert records[0]["note"] == "Supermarket"


def test_build_bank_transaction_incoming_uses_transfer():
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "500.00",
        "title": "Salary",
        "subtitle": "DE89370400440532013000",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    r = records[0]
    assert r["paymentType"] == "transfer"
    assert r["accountId"] == "cash"
    assert r["note"] == "From: Salary"
    assert r["transfer"] == {"pairingMode": "unpaired"}
    assert r["counterParty"] == "DE89370400440532013000"


def test_build_bank_transaction_outgoing_uses_to():
    event = {
        "eventType": "BANK_TRANSACTION_OUTGOING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-200.00",
        "title": "Landlord",
        "subtitle": "DE12345678901234567890",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    r = records[0]
    assert r["paymentType"] == "transfer"
    assert r["note"] == "To: Landlord"
    assert r["transfer"] == {"pairingMode": "unpaired"}
    assert r["counterParty"] == "DE12345678901234567890"


def test_build_bank_transaction_counter_party_truncated():
    long_subtitle = "X" * 300
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "100.00",
        "title": "Test",
        "subtitle": long_subtitle,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert len(records[0]["counterParty"]) == 255


def test_build_bank_transaction_incoming_no_subtitle():
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "500.00",
        "title": "Salary",
        "subtitle": None,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert "counterParty" not in records[0]
    assert records[0]["transfer"] == {"pairingMode": "unpaired"}
    assert records[0]["note"] == "From: Salary"


def test_build_unknown_event_type_posts_to_cash():
    event = {"eventType": "UNKNOWN_EVENT", "timestamp": "2024-01-01T00:00:00Z", "amount": "1.00", "title": "Something"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    assert records[0]["accountId"] == "cash"
    assert records[0]["note"] == "Something"


def test_build_zero_amount_returns_empty():
    event = {"eventType": "CARD_VERIFICATION", "timestamp": "2024-01-01T00:00:00Z", "amount": "0.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records == []


def test_build_zero_amount_tr_dict_format():
    event = {"eventType": "CARD_VERIFICATION", "timestamp": "2024-01-01T00:00:00Z",
             "amount": {"value": 0.0, "currency": "EUR"}}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records == []


def test_build_uses_lowercase_type_field():
    event = {"type": "interest_payment", "timestamp": "2024-01-01T00:00:00Z", "amount": "3.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    assert records[0]["accountId"] == "cash"
    assert records[0]["amount"] == {"value": 3.0}


# ---------------------------------------------------------------------------
# filter_by_lookback
# ---------------------------------------------------------------------------

def _evt(ts: str) -> dict:
    return {"timestamp": ts}


def test_filter_by_lookback_keeps_recent():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([_evt("2024-01-11T00:00:00Z")], since)) == 1


def test_filter_by_lookback_removes_old():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert filter_by_lookback([_evt("2024-01-09T00:00:00Z")], since) == []


def test_filter_by_lookback_keeps_event_on_boundary():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([_evt("2024-01-10T00:00:00Z")], since)) == 1


def test_filter_by_lookback_unparseable_timestamp_kept():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([{"timestamp": "not-a-date"}], since)) == 1


def test_filter_by_lookback_naive_timestamp_treated_as_utc():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([_evt("2024-01-11T00:00:00")], since)) == 1
