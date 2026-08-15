"""Category assignment for BudgetBakers Wallet records.

Two classes are provided:

* :class:`CategoryCache` — thin TTL-based wrapper around
  ``WalletClient.get_categories()`` so the full category list is fetched at
  most once per 24 hours within a running process.

* :class:`HistoryCategorizer` — assigns a ``categoryId`` to a new record by
  looking up the most frequently used non-unknown category for records that
  share the same ``note`` in recent Wallet history.
"""

from __future__ import annotations

import time
from collections import Counter
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.wallet_client import WalletClient


class CategoryCache:
    """24-hour TTL cache for the Wallet categories list.

    Wraps ``WalletClient.get_categories()`` so the API is called at most once
    per :attr:`TTL` seconds.  Call :meth:`invalidate` to force a reload on the
    next :meth:`get` call.
    """

    TTL: float = 86_400.0  # 24 hours in seconds

    def __init__(self, wallet_client: WalletClient) -> None:
        self._client = wallet_client
        self._categories: list[dict[str, Any]] | None = None
        self._loaded_at: float | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self) -> list[dict[str, Any]]:
        """Return the categories list, loading from the API when necessary."""
        if self._categories is None or self._is_stale():
            self._reload()
        assert self._categories is not None
        return self._categories

    def invalidate(self) -> None:
        """Discard the cached categories so the next :meth:`get` fetches fresh data."""
        self._categories = None
        self._loaded_at = None

    def category_ids(self) -> set[str]:
        """Return the set of valid (non-empty) category IDs from the cache."""
        return {c["id"] for c in self.get() if c.get("id")}

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _is_stale(self) -> bool:
        return (
            self._loaded_at is None or (time.monotonic() - self._loaded_at) > self.TTL
        )

    def _reload(self) -> None:
        self._categories = self._client.get_categories()
        self._loaded_at = time.monotonic()


class HistoryCategorizer:
    """Assign categories to new records based on Wallet history matching by note.

    On the first :meth:`get_category_id` call the categorizer fetches all
    Wallet records for the last ``history_days`` days and builds an in-memory
    index mapping ``note → [categoryId, ...]``.  Subsequent calls reuse the
    index without any further API calls.

    The most frequent *valid* ``categoryId`` among the last ``top_n`` entries
    for a given note is returned.  A ``categoryId`` is considered valid if it
    appears in the current :class:`CategoryCache`.  Returns ``None`` when no
    match is found.
    """

    def __init__(
        self,
        wallet_client: WalletClient,
        history_days: int = 90,
        top_n: int = 5,
    ) -> None:
        self._client = wallet_client
        self._history_days = history_days
        self._top_n = top_n
        self._cache = CategoryCache(wallet_client)
        self._index: dict[str, list[str]] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_category_id(self, note: str) -> str | None:
        """Return the best-matching ``categoryId`` for *note*, or ``None``."""
        if self._index is None:
            self._build_index()
        cat_ids = (self._index or {}).get(note, [])
        if not cat_ids:
            return None
        counts = Counter(cat_ids[-self._top_n :])
        return counts.most_common(1)[0][0]

    def invalidate_cache(self) -> None:
        """Invalidate the category cache so the next lookup fetches fresh data.

        Call this when the Wallet API rejects a ``categoryId`` — the category
        may have been deleted after the cache was last loaded.
        """
        self._cache.invalidate()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_index(self) -> None:
        """Fetch recent records and build the note → [categoryId] index."""
        today = date.today()
        date_from = (today - timedelta(days=self._history_days)).isoformat()
        date_to = today.isoformat()
        records = self._client.get_records(date_from, date_to)

        valid_ids = self._cache.category_ids()
        self._index = {}
        for record in records:
            note: str = record.get("note", "")
            cat_id: str | None = record.get("categoryId")
            if note and cat_id and cat_id in valid_ids:
                self._index.setdefault(note, []).append(cat_id)
