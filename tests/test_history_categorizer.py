"""Tests for HistoryCategorizer — majority-vote category assignment from historical records."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from app.categorizer import HistoryCategorizer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CATEGORIES = [
    {"id": "cat-food", "name": "Food & Drink"},
    {"id": "cat-transport", "name": "Transport"},
    {"id": "cat-shopping", "name": "Shopping"},
]

_TODAY = date(2026, 8, 15)


def _make_wallet_client(records: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.get_categories.return_value = _CATEGORIES
    client.get_records.return_value = records or []
    return client


def _make_record(note: str, category_id: str | None) -> dict:
    r: dict = {"note": note}
    if category_id is not None:
        r["categoryId"] = category_id
    return r


# ---------------------------------------------------------------------------
# No historical records
# ---------------------------------------------------------------------------


def test_returns_none_when_no_records():
    wallet = _make_wallet_client(records=[])
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Starbucks")
    assert result is None


def test_returns_none_when_no_matching_note():
    wallet = _make_wallet_client(records=[_make_record("McDonald's", "cat-food")])
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Starbucks")
    assert result is None


# ---------------------------------------------------------------------------
# Single match
# ---------------------------------------------------------------------------


def test_returns_category_for_single_matching_record():
    wallet = _make_wallet_client(records=[_make_record("Starbucks", "cat-food")])
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Starbucks")
    assert result == "cat-food"


# ---------------------------------------------------------------------------
# Majority vote
# ---------------------------------------------------------------------------


def test_majority_vote_returns_most_frequent():
    records = [
        _make_record("Amazon", "cat-shopping"),
        _make_record("Amazon", "cat-shopping"),
        _make_record("Amazon", "cat-food"),
    ]
    wallet = _make_wallet_client(records=records)
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Amazon")
    assert result == "cat-shopping"


def test_majority_uses_last_top_n_records():
    """Only the last `top_n` records per note contribute to the vote."""
    # 6 records for "Amazon": first 3 are transport, last 3 are shopping
    records = [
        _make_record("Amazon", "cat-transport"),
        _make_record("Amazon", "cat-transport"),
        _make_record("Amazon", "cat-transport"),
        _make_record("Amazon", "cat-shopping"),
        _make_record("Amazon", "cat-shopping"),
        _make_record("Amazon", "cat-shopping"),
    ]
    wallet = _make_wallet_client(records=records)
    cat = HistoryCategorizer(wallet, top_n=3)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Amazon")
    assert result == "cat-shopping"


# ---------------------------------------------------------------------------
# Invalid / unknown categories filtered out
# ---------------------------------------------------------------------------


def test_ignores_records_without_category_id():
    records = [
        _make_record("Netflix", None),  # no categoryId
        _make_record("Netflix", "cat-shopping"),
    ]
    wallet = _make_wallet_client(records=records)
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Netflix")
    assert result == "cat-shopping"


def test_ignores_category_ids_not_in_categories_list():
    """Category IDs returned by historical records but absent from the current
    categories list (e.g. deleted categories) are filtered out."""
    records = [
        _make_record("Glovo", "cat-deleted"),  # not in _CATEGORIES
        _make_record("Glovo", "cat-food"),
    ]
    wallet = _make_wallet_client(records=records)
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Glovo")
    assert result == "cat-food"


def test_returns_none_when_all_categories_are_unknown():
    records = [_make_record("Glovo", "cat-deleted")]
    wallet = _make_wallet_client(records=records)
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("Glovo")
    assert result is None


# ---------------------------------------------------------------------------
# API call parameters
# ---------------------------------------------------------------------------


def test_get_records_called_with_correct_date_range():
    wallet = _make_wallet_client()
    cat = HistoryCategorizer(wallet, history_days=90)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
        cat.get_category_id("test")
    expected_from = (_TODAY - timedelta(days=90)).isoformat()
    expected_to = _TODAY.isoformat()
    wallet.get_records.assert_called_once_with(expected_from, expected_to)


# ---------------------------------------------------------------------------
# Index is built only once per instance (caching)
# ---------------------------------------------------------------------------


def test_index_built_once_for_multiple_calls():
    records = [_make_record("Uber", "cat-transport")]
    wallet = _make_wallet_client(records=records)
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        cat.get_category_id("Uber")
        cat.get_category_id("Uber")
    wallet.get_records.assert_called_once()


# ---------------------------------------------------------------------------
# Records without a note are ignored
# ---------------------------------------------------------------------------


def test_records_without_note_are_ignored():
    records = [{"categoryId": "cat-food"}]  # no 'note' key
    wallet = _make_wallet_client(records=records)
    cat = HistoryCategorizer(wallet)
    with patch("app.categorizer.date") as mock_date:
        mock_date.today.return_value = _TODAY
        result = cat.get_category_id("")
    assert result is None


# ---------------------------------------------------------------------------
# invalidate_cache
# ---------------------------------------------------------------------------


def test_invalidate_cache_delegates_to_inner_cache():
    """invalidate_cache() must call _cache.invalidate() so the next lookup is fresh."""
    wallet = _make_wallet_client()
    cat = HistoryCategorizer(wallet)
    with patch.object(cat._cache, "invalidate") as mock_invalidate:
        cat.invalidate_cache()
    mock_invalidate.assert_called_once()
