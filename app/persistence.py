from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.tr_mapper import extract_event_type, normalize_event_time

log = logging.getLogger(__name__)

_TTL_DAYS = 60


# ---------------------------------------------------------------------------
# Pure helpers (no state)
# ---------------------------------------------------------------------------


def event_id(event: dict[str, Any]) -> str:
    for key in ("id", "eventId", "event_id"):
        value = event.get(key)
        if value:
            return str(value)
    return ""


def dedup_event_id(event: dict[str, Any]) -> str:
    eid = event_id(event)
    if eid:
        return eid

    seed = "|".join(
        [
            extract_event_type(event),
            normalize_event_time(event),
            str(event.get("amount") or event.get("value") or ""),
            str(
                event.get("title")
                or event.get("name")
                or event.get("description")
                or ""
            ),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


# ---------------------------------------------------------------------------
# EventRepository
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
    CREATE TABLE IF NOT EXISTS processed_events (
        event_id         TEXT PRIMARY KEY,
        event_type       TEXT NOT NULL DEFAULT '',
        event_timestamp  TEXT NOT NULL DEFAULT '',
        amount           TEXT NOT NULL DEFAULT '',
        raw              TEXT NOT NULL DEFAULT '',
        synced_at        TEXT NOT NULL,
        wallet_record_id TEXT
    )
"""

_MIGRATE_ADD_WALLET_RECORD_ID = """
    ALTER TABLE processed_events ADD COLUMN wallet_record_id TEXT
"""

_CREATE_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_synced_at ON processed_events (synced_at)
"""


class EventRepository:
    """Manages the SQLite dedup database.

    Stores full event data for auditing. Records older than _TTL_DAYS are
    purged automatically on each open to keep the DB small.

    Usage::

        with EventRepository(db_path) as repo:
            repo.purge_old_records()
            new_events = repo.filter_unprocessed(events)
            for event in new_events:
                repo.mark_processed(event, wallet_record_id="wid-abc")
            repo.commit()
    """

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        if "wallet_record_id" not in self._column_names("processed_events"):
            self._conn.execute(_MIGRATE_ADD_WALLET_RECORD_ID)

    def _column_names(self, table: str) -> set[str]:
        return {
            row[1]
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def filter_unprocessed(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events_with_ids = [(event, dedup_event_id(event)) for event in events]
        ids = [dedup_id for _, dedup_id in events_with_ids]
        if not ids:
            return []

        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT event_id FROM processed_events WHERE event_id IN ({placeholders})",
            ids,
        ).fetchall()
        processed = {row[0] for row in rows}

        return [
            event for event, dedup_id in events_with_ids if dedup_id not in processed
        ]

    def mark_processed(
        self, event: dict[str, Any], *, wallet_record_id: str | None = None
    ) -> None:
        eid = dedup_event_id(event)
        event_type = extract_event_type(event)
        event_timestamp = normalize_event_time(event)
        amount = str(event.get("amount") or event.get("value") or "")
        try:
            raw = json.dumps(event, ensure_ascii=False, default=str)
        except TypeError:
            raw = str(event)
        synced_at = datetime.now(UTC).isoformat()

        self._conn.execute(
            "INSERT OR IGNORE INTO processed_events "
            "(event_id, event_type, event_timestamp, amount, raw, synced_at, wallet_record_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                eid,
                event_type,
                event_timestamp,
                amount,
                raw,
                synced_at,
                wallet_record_id,
            ),
        )

    def mark_processed_force(
        self, event: dict[str, Any], *, wallet_record_id: str | None = None
    ) -> None:
        """Upsert a processed event, replacing any existing row.

        Unlike :meth:`mark_processed` (which uses ``INSERT OR IGNORE`` and never
        updates an existing row), this method uses ``INSERT OR REPLACE`` so that
        ``wallet_record_id`` is always updated. Used by the resync path to
        record updated Wallet IDs after a forced re-upload.
        """
        eid = dedup_event_id(event)
        event_type = extract_event_type(event)
        event_timestamp = normalize_event_time(event)
        amount = str(event.get("amount") or event.get("value") or "")
        try:
            raw = json.dumps(event, ensure_ascii=False, default=str)
        except TypeError:
            raw = str(event)
        synced_at = datetime.now(UTC).isoformat()

        self._conn.execute(
            "INSERT OR REPLACE INTO processed_events "
            "(event_id, event_type, event_timestamp, amount, raw, synced_at, wallet_record_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                eid,
                event_type,
                event_timestamp,
                amount,
                raw,
                synced_at,
                wallet_record_id,
            ),
        )

    def get_wallet_record_id(self, event: dict[str, Any]) -> str | None:
        eid = dedup_event_id(event)
        row = self._conn.execute(
            "SELECT wallet_record_id FROM processed_events WHERE event_id = ?", (eid,)
        ).fetchone()
        return row[0] if row else None

    def is_processed(self, event_id: str) -> bool:
        """Return True if the given event_id has been marked as processed."""
        row = self._conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None

    def get_raw(self, event_id: str) -> str | None:
        """Return the stored raw payload for the given event_id, or None if not found.

        The value is normally valid JSON, but may be a plain ``str(event)`` fallback
        when ``json.dumps`` failed at insertion time (e.g. non-serialisable fields).
        """
        row = self._conn.execute(
            "SELECT raw FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row[0] if row else None

    def count_processed(self) -> int:
        """Return the total number of processed events stored."""
        row = self._conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()
        return row[0]

    def purge_old_records(self, ttl_days: int = _TTL_DAYS) -> int:
        """Delete records synced more than ttl_days ago. Returns number of rows deleted."""
        cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff -= timedelta(days=ttl_days)
        cursor = self._conn.execute(
            "DELETE FROM processed_events WHERE synced_at < ?",
            (cutoff.isoformat(),),
        )
        deleted = cursor.rowcount
        if deleted:
            log.info(
                "Purged %d processed_events records older than %d days",
                deleted,
                ttl_days,
            )
        self._conn.commit()
        return deleted

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EventRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
