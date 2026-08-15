"""Tests for CategoryCache — TTL-based cache around WalletClient.get_categories()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.categorizer import CategoryCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wallet_client(categories: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.get_categories.return_value = categories or [
        {"id": "cat-1", "name": "Food"},
        {"id": "cat-2", "name": "Transport"},
    ]
    return client


# ---------------------------------------------------------------------------
# Initial load
# ---------------------------------------------------------------------------


def test_get_loads_categories_on_first_call():
    wallet = _make_wallet_client()
    cache = CategoryCache(wallet)
    result = cache.get()
    wallet.get_categories.assert_called_once()
    assert result == [
        {"id": "cat-1", "name": "Food"},
        {"id": "cat-2", "name": "Transport"},
    ]


def test_get_returns_cached_on_second_call():
    wallet = _make_wallet_client()
    cache = CategoryCache(wallet)
    cache.get()
    cache.get()
    assert wallet.get_categories.call_count == 1


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


def test_get_reloads_after_ttl():
    wallet = _make_wallet_client()
    cache = CategoryCache(wallet)

    # Call sequence:
    # get() #1: _categories is None → _reload() → time.monotonic() called (→ 0.0)
    # get() #2: _is_stale() → time.monotonic() (→ 0.0); 0.0 - 0.0 < TTL → no reload
    # get() #3: _is_stale() → time.monotonic() (→ TTL+1); stale → _reload() → time.monotonic() (→ TTL+1)
    with patch(
        "app.categorizer.time.monotonic",
        side_effect=[0.0, 0.0, CategoryCache.TTL + 1, CategoryCache.TTL + 1],
    ):
        cache.get()  # initial load at t=0
        cache.get()  # still fresh (t=0)
        cache.get()  # stale (t=TTL+1) → reload

    assert wallet.get_categories.call_count == 2


def test_get_does_not_reload_before_ttl():
    wallet = _make_wallet_client()
    cache = CategoryCache(wallet)

    # get() #1: _categories is None → _reload() → time.monotonic() (→ 0.0)
    # get() #2: _is_stale() → time.monotonic() (→ 0.0); not stale
    # get() #3: _is_stale() → time.monotonic() (→ TTL-1); not stale
    with patch(
        "app.categorizer.time.monotonic", side_effect=[0.0, 0.0, CategoryCache.TTL - 1]
    ):
        cache.get()
        cache.get()
        cache.get()

    assert wallet.get_categories.call_count == 1


# ---------------------------------------------------------------------------
# Manual invalidation
# ---------------------------------------------------------------------------


def test_invalidate_forces_reload_on_next_get():
    wallet = _make_wallet_client()
    cache = CategoryCache(wallet)
    cache.get()
    cache.invalidate()
    cache.get()
    assert wallet.get_categories.call_count == 2


def test_invalidate_when_never_loaded_is_safe():
    wallet = _make_wallet_client()
    cache = CategoryCache(wallet)
    cache.invalidate()  # should not raise
    wallet.get_categories.assert_not_called()


# ---------------------------------------------------------------------------
# Category ID set helper
# ---------------------------------------------------------------------------


def test_category_ids_returns_set_of_ids():
    wallet = _make_wallet_client()
    cache = CategoryCache(wallet)
    assert cache.category_ids() == {"cat-1", "cat-2"}


def test_category_ids_excludes_entries_without_id():
    wallet = MagicMock()
    wallet.get_categories.return_value = [
        {"id": "cat-1", "name": "Food"},
        {"name": "No ID here"},
        {"id": "", "name": "Empty ID"},
    ]
    cache = CategoryCache(wallet)
    assert cache.category_ids() == {"cat-1"}
