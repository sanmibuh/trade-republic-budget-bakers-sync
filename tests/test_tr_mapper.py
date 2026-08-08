from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.tr_mapper import (
    KNOWN_EVENT_TYPES,
    _extract_detail_row,
    _extract_iban_from_details,
    _to_decimal,
    build_records_for_event,
    extract_amount,
    normalize_event_time,
)

# ---------------------------------------------------------------------------
# _to_decimal
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, Decimal(0)),
    (0, Decimal(0)),
    (5, Decimal(5)),
    (3.14, Decimal("3.14")),
    (Decimal("1.5"), Decimal("1.5")),
    ("10.50", Decimal("10.50")),
    ("10,50", Decimal("10.50")),
    ("€ 9.99", Decimal("9.99")),
    ("", Decimal(0)),
    ("  ", Decimal(0)),
    ("not-a-number", Decimal(0)),
])
def test_to_decimal(value, expected):
    assert _to_decimal(value) == expected


def test_to_decimal_unsupported_type_returns_zero():
    """An unsupported type (e.g. list) should fall through to the final return Decimal('0')."""
    assert _to_decimal([1, 2, 3]) == Decimal(0)
    assert _to_decimal({"value": 5}) == Decimal(0)


# ---------------------------------------------------------------------------
# extract_amount
# ---------------------------------------------------------------------------

def test_extract_amount_top_level():
    assert extract_amount({"amount": "50.00"}, "amount") == Decimal("50.00")


def test_extract_amount_nested_dict():
    event = {"amount": {"value": "25.00"}}
    assert extract_amount(event, "value") == Decimal("25.00")


def test_extract_amount_missing_returns_zero():
    assert extract_amount({}, "amount") == Decimal(0)


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


def test_build_saveback_aggregate_note_prefixed():
    event = {
        "eventType": "SAVEBACK_AGGREGATE",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "12.15",
        "title": "Core MSCI World USD (Acc)",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Saveback: Core MSCI World USD (Acc)"


def test_build_spare_change_aggregate_note_prefixed():
    event = {
        "eventType": "SPARE_CHANGE_AGGREGATE",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "6.21",
        "title": "Core MSCI World USD (Acc)",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Round-up Investment: Core MSCI World USD (Acc)"


def test_build_saveback_aggregate_no_title_uses_mapped():
    event = {"eventType": "SAVEBACK_AGGREGATE", "timestamp": "2024-01-01T00:00:00Z", "amount": "5.00"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Saveback"


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
        "title": "Salary Corp",
        "subtitle": "Erhalten",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    r = records[0]
    assert r["paymentType"] == "transfer"
    assert r["accountId"] == "cash"
    assert r["note"] == "From: Salary Corp"
    assert r["transfer"] == {"pairingMode": "unpaired"}
    assert r["counterParty"] == "Salary Corp"


def test_build_bank_transaction_outgoing_uses_to():
    event = {
        "eventType": "BANK_TRANSACTION_OUTGOING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-200.00",
        "title": "Landlord",
        "subtitle": "Gesendet",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    r = records[0]
    assert r["paymentType"] == "transfer"
    assert r["note"] == "To: Landlord"
    assert r["transfer"] == {"pairingMode": "unpaired"}
    assert r["counterParty"] == "Landlord"


def test_build_bank_transaction_counter_party_truncated():
    long_title = "X" * 300
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "100.00",
        "title": long_title,
        "subtitle": "Erhalten",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert len(records[0]["counterParty"]) == 255


def test_build_bank_transaction_incoming_no_title():
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "500.00",
        "subtitle": "Erhalten",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert "counterParty" not in records[0]
    assert records[0]["transfer"] == {"pairingMode": "unpaired"}


def test_build_unknown_event_type_posts_to_cash():
    event = {"eventType": "UNKNOWN_EVENT", "timestamp": "2024-01-01T00:00:00Z", "amount": "1.00", "title": "Something"}
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert len(records) == 1
    assert records[0]["accountId"] == "cash"
    assert records[0]["note"] == "Something"


def test_build_unknown_event_type_logs_warning():
    event = {"eventType": "MYSTERY_TYPE", "timestamp": "2024-01-01T00:00:00Z", "amount": "1.00"}
    import logging
    with patch.object(logging.getLogger("app.tr_mapper"), "warning") as mock_warn:
        build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    mock_warn.assert_called_once()
    assert "MYSTERY_TYPE" in mock_warn.call_args.args[1]


def test_known_event_types_contains_expected_types():
    for t in ("BUY_ORDER", "SELL_ORDER", "CARD_TRANSACTION", "INTEREST_PAYMENT",
              "BANK_TRANSACTION_INCOMING", "BANK_TRANSACTION_OUTGOING"):
        assert t in KNOWN_EVENT_TYPES


def test_known_event_type_does_not_log_warning():
    event = {"eventType": "BUY_ORDER", "timestamp": "2024-01-01T00:00:00Z", "amount": "100.00"}
    import logging
    with patch.object(logging.getLogger("app.tr_mapper"), "warning") as mock_warn:
        build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    mock_warn.assert_not_called()


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
# _extract_iban_from_details
# ---------------------------------------------------------------------------

def _make_iban_details(full_iban: str, event_id: str = "abc") -> dict:
    """Build a minimal TR details payload containing a full IBAN in the nested structure."""
    return {
        "id": event_id,
        "sections": [
            {"type": "header", "title": "Du hast 100 € gesendet", "data": {}},
            {
                "title": "Übersicht",
                "data": [
                    {
                        "title": "IBAN",
                        "detail": {
                            "text": f"..{full_iban[-4:]}",
                            "action": {
                                "type": "infoPage",
                                "payload": {
                                    "sections": [
                                        {
                                            "data": [
                                                {
                                                    "title": full_iban,
                                                    "detail": {"type": "listItemAvatarDefault"},
                                                }
                                            ]
                                        }
                                    ]
                                },
                            },
                        },
                        "style": "plain",
                    }
                ],
            },
        ],
    }


def test_extract_iban_full_iban_no_spaces():
    details = _make_iban_details("ES86 0182 5297 2402 0031 7648")
    assert _extract_iban_from_details(details) == "ES860182529724020031 7648".replace(" ", "")


def test_extract_iban_returns_none_when_no_iban_section():
    details = {"sections": [{"type": "header", "data": {}}]}
    assert _extract_iban_from_details(details) is None


def test_extract_iban_falls_back_to_masked_when_payload_missing():
    details = {
        "sections": [
            {
                "data": [
                    {
                        "title": "IBAN",
                        "detail": {"text": "..7648"},
                    }
                ]
            }
        ]
    }
    assert _extract_iban_from_details(details) == "..7648"


def test_extract_iban_returns_none_on_empty_details():
    assert _extract_iban_from_details({}) is None


def test_extract_iban_ignores_non_iban_rows():
    details = {
        "sections": [
            {
                "data": [
                    {"title": "Status", "detail": {"text": "Abgeschlossen"}},
                    {"title": "Empfänger", "detail": {"text": "DAVID BELMEZ"}},
                ]
            }
        ]
    }
    assert _extract_iban_from_details(details) is None


# ---------------------------------------------------------------------------
# build_records_for_event — IBAN extraction in bank transactions
# ---------------------------------------------------------------------------

def test_build_bank_transaction_uses_iban_as_counter_party():
    event = {
        "eventType": "BANK_TRANSACTION_OUTGOING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-200.00",
        "title": "Landlord",
        "details": _make_iban_details("ES86 0182 5297 2402 0031 7648"),
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")

    assert records[0]["counterParty"] == "ES860182529724020031 7648".replace(" ", "")
    assert records[0]["note"] == "To: Landlord"


def test_build_bank_transaction_falls_back_to_title_when_no_details():
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "500.00",
        "title": "Salary Corp",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["counterParty"] == "Salary Corp"


def test_build_bank_transaction_falls_back_to_title_when_iban_absent_in_details():
    details_without_iban = {
        "id": "x",
        "sections": [{"type": "header", "data": {}}],
    }
    event = {
        "eventType": "BANK_TRANSACTION_INCOMING",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "500.00",
        "title": "Salary Corp",
        "details": details_without_iban,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["counterParty"] == "Salary Corp"


# ---------------------------------------------------------------------------
# KNOWN_EVENT_TYPES — document-only types
# ---------------------------------------------------------------------------

def test_known_event_types_includes_document_types():
    assert "QUARTERLY_NET_WORTH_STATEMENT_CREATED" in KNOWN_EVENT_TYPES
    assert "EX_POST_COST_REPORT_CREATED" in KNOWN_EVENT_TYPES


def test_document_event_types_are_zero_amount_excluded():
    """Document-only events always have zero amount and should produce no records."""
    for event_type in ("QUARTERLY_NET_WORTH_STATEMENT_CREATED", "EX_POST_COST_REPORT_CREATED"):
        event = {"eventType": event_type, "timestamp": "2024-01-01T00:00:00Z", "amount": "0.00"}
        assert build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port") == []


# ---------------------------------------------------------------------------
# _extract_detail_row
# ---------------------------------------------------------------------------

def _make_table_details(*rows: tuple[str, str]) -> dict:
    """Build a minimal details payload with a table section containing the given (title, text) rows."""
    return {
        "sections": [
            {
                "title": "Übersicht",
                "data": [
                    {"title": t, "detail": {"text": v}, "style": "plain"}
                    for t, v in rows
                ],
            }
        ]
    }


def test_extract_detail_row_found():
    details = _make_table_details(("Transaktion", "0,39713 × 125,90 €"), ("Gebühr", "Kostenlos"))
    assert _extract_detail_row(details, "Transaktion") == "0,39713 × 125,90 €"


def test_extract_detail_row_not_found_returns_none():
    details = _make_table_details(("Gebühr", "Kostenlos"))
    assert _extract_detail_row(details, "Transaktion") is None


def test_extract_detail_row_prefers_display_value():
    details = {
        "sections": [
            {
                "data": [
                    {
                        "title": "Transaktion",
                        "detail": {
                            "text": "raw text",
                            "displayValue": {"text": "clean text"},
                        },
                    }
                ]
            }
        ]
    }
    assert _extract_detail_row(details, "Transaktion") == "clean text"


def test_extract_detail_row_empty_details():
    assert _extract_detail_row({}, "Transaktion") is None


# ---------------------------------------------------------------------------
# build_records_for_event — Transaktion in investment events
# ---------------------------------------------------------------------------

def test_build_investment_appends_transaktion_to_note():
    details = _make_table_details(("Transaktion", "0,397 × 125,90 €"), ("Gebühr", "Kostenlos"))
    event = {
        "eventType": "TRADING_SAVINGSPLAN_EXECUTED",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-50.00",
        "title": "Core MSCI World USD (Acc)",
        "details": details,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Savings Plan: Core MSCI World USD (Acc) · 0,397 × 125,90 €"


def test_build_spare_change_appends_transaktion_to_note():
    details = _make_table_details(("Transaktion", "0,049 × 125,92 €"))
    event = {
        "eventType": "SPARE_CHANGE_AGGREGATE",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-6.21",
        "title": "Core MSCI World USD (Acc)",
        "details": details,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Round-up Investment: Core MSCI World USD (Acc) · 0,049 × 125,92 €"


def test_build_investment_note_without_details():
    event = {
        "eventType": "TRADING_SAVINGSPLAN_EXECUTED",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-50.00",
        "title": "Core MSCI World USD (Acc)",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Savings Plan: Core MSCI World USD (Acc)"


# ---------------------------------------------------------------------------
# build_records_for_event — gross + tax in INTEREST_PAYOUT
# ---------------------------------------------------------------------------

def test_build_interest_appends_gross_and_tax():
    details = _make_table_details(("Angesammelt", "78,68 €"), ("Steuern", "14,95 €"), ("Gesamt", "63,73 €"))
    event = {
        "eventType": "INTEREST_PAYOUT",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "63.73",
        "title": "Zinsen",
        "details": details,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Interest Payout: Zinsen · gross 78,68 €, tax 14,95 €"


def test_build_interest_note_without_details():
    event = {
        "eventType": "INTEREST_PAYOUT",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "63.73",
        "title": "Zinsen",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Interest Payout: Zinsen"


# ---------------------------------------------------------------------------
# build_records_for_event — Transaktion + gross + tax in SAVEBACK_AGGREGATE
# ---------------------------------------------------------------------------

def test_build_saveback_aggregate_appends_transaktion_and_tax():
    details = _make_table_details(
        ("Transaktion", "0,097 × 125,85 €"),
        ("Angefallen", "+ 15,00 €"),
        ("Steuern", "2,85 €"),
    )
    event = {
        "eventType": "SAVEBACK_AGGREGATE",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-12.15",
        "title": "Core MSCI World USD (Acc)",
        "details": details,
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Saveback: Core MSCI World USD (Acc) · 0,097 × 125,85 € · gross + 15,00 €, tax 2,85 €"


def test_build_saveback_aggregate_note_without_details():
    event = {
        "eventType": "SAVEBACK_AGGREGATE",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "-12.15",
        "title": "Core MSCI World USD (Acc)",
    }
    records = build_records_for_event(event, cash_account_id="cash", portfolio_account_id="port")
    assert records[0]["note"] == "Saveback: Core MSCI World USD (Acc)"
