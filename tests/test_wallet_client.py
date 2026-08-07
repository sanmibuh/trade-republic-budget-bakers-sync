from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.wallet_client import (
    WalletClient,
    _to_decimal,
    _get_first_match,
    extract_amount,
    normalize_event_time,
    build_records_for_event,
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


@pytest.mark.parametrize("event_type", ["BUY_ORDER", "SAVINGS_PLAN", "SELL_ORDER", "TRADING_SAVINGSPLAN_EXECUTED"])
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
    assert records[0]["note"] == "Supermarket"  # TR title, not mapped


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


def test_build_bank_transaction_negative_uses_to():
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-200.00",
        "title": "Landlord",
        "subtitle": None,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert records[0]["note"] == "To: Landlord"


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
    assert records[0]["note"] == "Something"  # TR title used as default


def test_build_zero_amount_returns_empty():
    """Events like CARD_VERIFICATION with amount=0 must be excluded (return empty list)."""
    event = {"eventType": "CARD_VERIFICATION", "timestamp": "2024-01-01T00:00:00Z", "amount": "0.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records == []


def test_build_zero_amount_tr_dict_format():
    """Same exclusion when amount comes as TR dict {"value": 0, "currency": "EUR"}."""
    event = {"eventType": "CARD_VERIFICATION", "timestamp": "2024-01-01T00:00:00Z",
             "amount": {"value": 0.0, "currency": "EUR"}}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records == []


def test_build_uses_lowercase_type_field():
    """Lowercase 'type' key is uppercased internally — INTEREST_PAYMENT branch fires."""
    event = {"type": "interest_payment", "timestamp": "2024-01-01T00:00:00Z", "amount": "3.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    assert records[0]["accountId"] == "cash"
    assert records[0]["amount"] == {"value": 3.0}


# ---------------------------------------------------------------------------
# WalletClient.post_records
# ---------------------------------------------------------------------------

def _make_client() -> WalletClient:
    return WalletClient(api_key="test-key", base_url="https://example.com/wallet")


def _mock_response(status_code: int, body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


def test_post_records_empty_returns_empty():
    client = _make_client()
    assert client.post_records([]) == []


def test_post_records_200_returns_results():
    client = _make_client()
    results = [{"inputIndex": 0, "id": "abc", "success": True}]
    client.session.post = MagicMock(return_value=_mock_response(200, {"results": results}))

    out = client.post_records([{"accountId": "x"}])

    assert out == results
    client.session.post.assert_called_once()
    _, kwargs = client.session.post.call_args
    assert kwargs["params"] == {"returnData": "false"}


def test_post_records_207_returns_results():
    client = _make_client()
    results = [
        {"inputIndex": 0, "id": "abc", "success": True},
        {"inputIndex": 1, "success": False, "error": {"message": "bad"}},
    ]
    client.session.post = MagicMock(return_value=_mock_response(207, {"results": results}))

    out = client.post_records([{}, {}])
    assert out == results


def test_post_records_400_returns_results():
    client = _make_client()
    results = [{"inputIndex": 0, "success": False, "error": {"message": "validation"}}]
    client.session.post = MagicMock(return_value=_mock_response(400, {"results": results}))

    out = client.post_records([{}])
    assert out == results


def test_post_records_401_raises():
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 401
    resp.raise_for_status.side_effect = Exception("Unauthorized")
    client.session.post = MagicMock(return_value=resp)

    with pytest.raises(Exception, match="Unauthorized"):
        client.post_records([{}])


def test_post_records_chunks_at_20():
    """21 records must produce exactly 2 POST calls (chunks of 20 + 1)."""
    client = _make_client()

    def _ok_response(request_args, **kwargs):
        chunk = kwargs.get("json") or request_args[1]
        results = [
            {"inputIndex": i, "id": f"id-{i}", "success": True}
            for i in range(len(chunk))
        ]
        return _mock_response(200, {"results": results})

    client.session.post = MagicMock(side_effect=_ok_response)

    records = [{"accountId": f"acc-{i}"} for i in range(21)]
    results = client.post_records(records)

    assert client.session.post.call_count == 2
    assert len(results) == 21
    # inputIndex must be rebased: second chunk item 0 → global index 20
    assert results[20]["inputIndex"] == 20
