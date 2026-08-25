from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from app.tr_mapper import extract_event_type, is_canceled_event, normalize_event_time

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


def _resolve_amount(event: dict[str, Any]) -> str:
    """Return the event amount/value as a string, preserving 0 as ``"0"``."""
    amount_raw = event.get("amount")
    if amount_raw is None:
        amount_raw = event.get("value")
    return str(amount_raw) if amount_raw is not None else ""


def dedup_event_id(event: dict[str, Any]) -> str:
    eid = event_id(event)
    if eid:
        return eid

    seed = "|".join(
        [
            extract_event_type(event),
            normalize_event_time(event),
            _resolve_amount(event),
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
# Schema initialisation
# ---------------------------------------------------------------------------


def init_db(db_path: Path) -> None:
    """Initialise the SQLite database schema from ``schema.sql``.

    Reads the idempotent DDL bundled alongside this module and executes it
    against *db_path*, creating the file if it does not yet exist.  Safe to
    call multiple times — all statements use ``CREATE … IF NOT EXISTS``.

    Call this once at process startup before any :class:`EventRepository` is
    opened.  The CLI entry point (``app/__main__.py``) calls it at the start
    of each command that uses the database (``sync``, ``resync``).  The
    Telegram bot calls it during ``TelegramBot.__init__``.

    Args:
        db_path: Filesystem path for the SQLite database file.
    """
    sql = (Path(__file__).parent / "schema.sql").read_text()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(sql)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# EventRepository
# ---------------------------------------------------------------------------


class EventRepository:
    """Manages the SQLite dedup database.

    Stores full event data for auditing. Records older than _TTL_DAYS are
    purged automatically on each open to keep the DB small.

    All ``processed_events`` queries are scoped to *instance* so that a single
    shared database can serve multiple sync instances without cross-contamination.

    The database schema must be initialised by calling :func:`init_db` once at
    process startup before any ``EventRepository`` is opened.

    Usage::

        init_db(db_path)           # once at startup

        with EventRepository(db_path, instance="alice") as repo:
            repo.purge_old_records()
            new_events = repo.filter_unprocessed(events)
            for event in new_events:
                repo.mark_processed(event, wallet_record_id="wid-abc")
            repo.commit()
    """

    def __init__(self, db_path: Path, instance: str = "") -> None:
        self._instance = instance
        self._conn = sqlite3.connect(db_path)

    def filter_unprocessed(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events_with_ids = [(event, dedup_event_id(event)) for event in events]
        ids = [dedup_id for _, dedup_id in events_with_ids]
        if not ids:
            return []

        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT event_id FROM processed_events "
            f"WHERE instance = ? AND event_id IN ({placeholders})",
            [self._instance, *ids],
        ).fetchall()
        processed = {row[0] for row in rows}

        return [
            event for event, dedup_id in events_with_ids if dedup_id not in processed
        ]

    def filter_cancellation_pending(
        self, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Return events that need a reversal record posted in BudgetBakers.

        An event qualifies when **all** of the following are true:

        - It already exists in ``processed_events`` for this instance.
        - Its stored ``wallet_record_id`` is non-NULL (a real Wallet record exists).
        - It currently carries ``status == "CANCELED"`` in the TR timeline payload.

        These are events synced while active that TR has since canceled.  The caller
        must post a reversal and then call ``mark_processed_force(wallet_record_id=None)``
        so subsequent syncs do not re-post the reversal.
        """
        if not events:
            return []

        canceled = [e for e in events if is_canceled_event(e)]
        if not canceled:
            return []

        events_with_ids = [(e, dedup_event_id(e)) for e in canceled]
        ids = [dedup_id for _, dedup_id in events_with_ids]
        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT event_id FROM processed_events "
            f"WHERE instance = ? AND event_id IN ({placeholders})"
            f" AND wallet_record_id IS NOT NULL",
            [self._instance, *ids],
        ).fetchall()
        with_wallet_id = {row[0] for row in rows}
        return [e for e, dedup_id in events_with_ids if dedup_id in with_wallet_id]

    def _build_event_row(
        self, event: dict[str, Any], wallet_record_id: str | None
    ) -> tuple[str, str, str, str, str, str, str, str | None]:
        """Build the row tuple shared by :meth:`mark_processed` and :meth:`mark_processed_force`."""
        eid = dedup_event_id(event)
        event_type = extract_event_type(event)
        event_timestamp = normalize_event_time(event)
        amount = _resolve_amount(event)
        try:
            raw = json.dumps(event, ensure_ascii=False, default=str)
        except TypeError:
            raw = str(event)
        synced_at = datetime.now(UTC).isoformat()
        return (
            eid,
            self._instance,
            event_type,
            event_timestamp,
            amount,
            raw,
            synced_at,
            wallet_record_id,
        )

    _SQL_INSERT_IGNORE = (
        "INSERT OR IGNORE INTO processed_events "
        "(event_id, instance, event_type, event_timestamp, amount, raw, synced_at, wallet_record_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    _SQL_INSERT_REPLACE = (
        "INSERT OR REPLACE INTO processed_events "
        "(event_id, instance, event_type, event_timestamp, amount, raw, synced_at, wallet_record_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    def _insert_processed_event(
        self,
        event: dict[str, Any],
        wallet_record_id: str | None,
        *,
        conflict: Literal["IGNORE", "REPLACE"],
    ) -> None:
        if conflict == "IGNORE":
            sql = self._SQL_INSERT_IGNORE
        elif conflict == "REPLACE":
            sql = self._SQL_INSERT_REPLACE
        else:
            raise ValueError(
                f"Unsupported conflict mode for processed_events insert: {conflict!r}"
            )
        self._conn.execute(sql, self._build_event_row(event, wallet_record_id))

    def mark_processed(
        self, event: dict[str, Any], *, wallet_record_id: str | None = None
    ) -> None:
        self._insert_processed_event(event, wallet_record_id, conflict="IGNORE")

    def mark_processed_force(
        self, event: dict[str, Any], *, wallet_record_id: str | None = None
    ) -> None:
        """Upsert a processed event, replacing any existing row.

        Unlike :meth:`mark_processed` (which uses ``INSERT OR IGNORE`` and never
        updates an existing row), this method uses ``INSERT OR REPLACE`` so that
        ``wallet_record_id`` is always updated. Used by the resync path to
        record updated Wallet IDs after a forced re-upload.
        """
        self._insert_processed_event(event, wallet_record_id, conflict="REPLACE")

    def get_wallet_record_id(self, event: dict[str, Any]) -> str | None:
        eid = dedup_event_id(event)
        row = self._conn.execute(
            "SELECT wallet_record_id FROM processed_events "
            "WHERE event_id = ? AND instance = ?",
            (eid, self._instance),
        ).fetchone()
        return row[0] if row else None

    def is_processed(self, event_id: str) -> bool:
        """Return True if the given event_id has been marked as processed."""
        row = self._conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ? AND instance = ?",
            (event_id, self._instance),
        ).fetchone()
        return row is not None

    def get_raw(self, event_id: str) -> str | None:
        """Return the stored raw payload for the given event_id, or None if not found.

        The value is normally valid JSON, but may be a plain ``str(event)`` fallback
        when ``json.dumps`` failed at insertion time (e.g. non-serialisable fields).
        """
        row = self._conn.execute(
            "SELECT raw FROM processed_events WHERE event_id = ? AND instance = ?",
            (event_id, self._instance),
        ).fetchone()
        return row[0] if row else None

    def count_processed(self) -> int:
        """Return the total number of processed events stored for this instance."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM processed_events WHERE instance = ?",
            (self._instance,),
        ).fetchone()
        return row[0]

    def purge_old_records(self, ttl_days: int = _TTL_DAYS) -> int:
        """Delete records synced more than ttl_days ago. Returns number of rows deleted."""
        cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff -= timedelta(days=ttl_days)
        cursor = self._conn.execute(
            "DELETE FROM processed_events WHERE instance = ? AND synced_at < ?",
            (self._instance, cutoff.isoformat()),
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

    def set_auth_state(self, instance: str, status: str) -> None:
        """Persist authentication status for *instance* to the ``auth_state`` table.

        Args:
            instance: Logical instance name (e.g. ``"david"``).
            status:   One of ``"ok"``, ``"failed"``, or ``"expired"``.
        """
        updated_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO auth_state (instance, status, updated_at) "
            "VALUES (?, ?, ?)",
            (instance, status, updated_at),
        )
        self._conn.commit()

    def get_auth_state(self, instance: str) -> str | None:
        """Return the persisted auth status for *instance*, or ``None`` if absent."""
        row = self._conn.execute(
            "SELECT status FROM auth_state WHERE instance = ?", (instance,)
        ).fetchone()
        return row[0] if row else None

    def set_sync_run(
        self,
        instance: str,
        *,
        status: str,
        saved: int,
        failed: int,
        excluded: int,
    ) -> None:
        """Persist the result of a sync run for *instance*.

        Args:
            instance: Logical instance name (e.g. ``"david"``).
            status:   One of ``"success"``, ``"partial"``, or ``"failed"``.
            saved:    Number of events successfully saved to Wallet.
            failed:   Number of events that failed to save.
            excluded: Number of events excluded from sync (zero-amount, CANCELED without a prior record, or already reversed).
        """
        ran_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO sync_runs "
            "(instance, status, ran_at, saved, failed, excluded) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (instance, status, ran_at, saved, failed, excluded),
        )
        self._conn.commit()

    def get_sync_run(self, instance: str) -> dict[str, Any] | None:
        """Return the last sync run result for *instance*, or ``None`` if absent."""
        row = self._conn.execute(
            "SELECT status, ran_at, saved, failed, excluded "
            "FROM sync_runs WHERE instance = ?",
            (instance,),
        ).fetchone()
        if row is None:
            return None
        return {
            "status": row[0],
            "ran_at": row[1],
            "saved": row[2],
            "failed": row[3],
            "excluded": row[4],
        }

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> EventRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
