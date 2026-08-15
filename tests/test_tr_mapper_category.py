"""Tests for _make_record and build_records_for_event with optional categoryId."""

from __future__ import annotations

from decimal import Decimal

from app.tr_mapper import _make_record, build_records_for_event

# ---------------------------------------------------------------------------
# _make_record — categoryId field
# ---------------------------------------------------------------------------


def test_make_record_without_category_id_omits_field():
    record = _make_record("acc-1", Decimal("10.00"), "Coffee", "2026-08-15T10:00:00")
    assert "categoryId" not in record


def test_make_record_with_category_id_includes_field():
    record = _make_record(
        "acc-1",
        Decimal("10.00"),
        "Coffee",
        "2026-08-15T10:00:00",
        category_id="cat-food",
    )
    assert record["categoryId"] == "cat-food"


def test_make_record_with_none_category_id_omits_field():
    record = _make_record(
        "acc-1",
        Decimal("10.00"),
        "Coffee",
        "2026-08-15T10:00:00",
        category_id=None,
    )
    assert "categoryId" not in record


# ---------------------------------------------------------------------------
# build_records_for_event — category_id propagation
# ---------------------------------------------------------------------------


def _card_event() -> dict:
    return {
        "eventType": "CARD_TRANSACTION",
        "amount": {"value": -15.0},
        "timestamp": "2026-08-15T12:00:00+00:00",
        "title": "Starbucks",
    }


def test_build_records_for_event_without_category_id():
    records = build_records_for_event(
        _card_event(),
        cash_account_id="cash-1",
        portfolio_account_id="port-1",
    )
    assert len(records) == 1
    assert "categoryId" not in records[0]


def test_build_records_for_event_with_category_id():
    records = build_records_for_event(
        _card_event(),
        cash_account_id="cash-1",
        portfolio_account_id="port-1",
        category_id="cat-food",
    )
    assert len(records) == 1
    assert records[0]["categoryId"] == "cat-food"


def test_build_records_for_event_category_id_on_multi_record_event():
    """For events that produce multiple records (e.g. BUY_ORDER), category_id
    is applied to every sub-record."""
    event = {
        "eventType": "BUY_ORDER",
        "amount": {"value": -100.0},
        "timestamp": "2026-08-15T12:00:00+00:00",
        "title": "Apple Inc.",
    }
    records = build_records_for_event(
        event,
        cash_account_id="cash-1",
        portfolio_account_id="port-1",
        category_id="cat-investment",
    )
    assert len(records) >= 1
    for record in records:
        assert record.get("categoryId") == "cat-investment"


def test_build_records_for_event_category_id_none_omits_field():
    records = build_records_for_event(
        _card_event(),
        cash_account_id="cash-1",
        portfolio_account_id="port-1",
        category_id=None,
    )
    assert "categoryId" not in records[0]
