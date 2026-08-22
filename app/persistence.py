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
        event_id         TEXT NOT NULL,
        instance         TEXT NOT NULL DEFAULT '',
        event_type       TEXT NOT NULL DEFAULT '',
        event_timestamp  TEXT NOT NULL DEFAULT '',
        amount           TEXT NOT NULL DEFAULT '',
        raw              TEXT NOT NULL DEFAULT '',
        synced_at        TEXT NOT NULL,
        wallet_record_id TEXT,
        PRIMARY KEY (event_id, instance)
    )
"""

_MIGRATE_ADD_WALLET_RECORD_ID = """
    ALTER TABLE processed_events ADD COLUMN wallet_record_id TEXT
"""

_MIGRATE_ADD_INSTANCE = """
    ALTER TABLE processed_events ADD COLUMN instance TEXT NOT NULL DEFAULT ''
"""

_CREATE_INDEX = """
    CREATE INDEX IF NOT EXISTS idx_synced_at ON processed_events (synced_at)
"""

_CREATE_AUTH_STATE_TABLE = """
    CREATE TABLE IF NOT EXISTS auth_state (
        instance    TEXT PRIMARY KEY,
        status      TEXT NOT NULL,
        updated_at  TEXT NOT NULL
    )
"""

_CREATE_SYNC_RUNS_TABLE = """
    CREATE TABLE IF NOT EXISTS sync_runs (
        instance  TEXT PRIMARY KEY,
        status    TEXT NOT NULL,
        ran_at    TEXT NOT NULL,
        saved     INTEGER NOT NULL DEFAULT 0,
        failed    INTEGER NOT NULL DEFAULT 0,
        excluded  INTEGER NOT NULL DEFAULT 0
    )
"""


class EventRepository:
    """Manages the SQLite dedup database.

    Stores full event data for auditing. Records older than _TTL_DAYS are
    purged automatically on each open to keep the DB small.

    All ``processed_events`` queries are scoped to *instance* so that a single
    shared database can serve multiple sync instances without cross-contamination.

    Usage::

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
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_CREATE_INDEX)
        self._conn.execute(_CREATE_AUTH_STATE_TABLE)
        self._conn.execute(_CREATE_SYNC_RUNS_TABLE)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        cols = self._column_names("processed_events")
        if "wallet_record_id" not in cols:
            self._conn.execute(_MIGRATE_ADD_WALLET_RECORD_ID)
        if "instance" not in cols:
            self._conn.execute(_MIGRATE_ADD_INSTANCE)

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
            f"SELECT event_id FROM processed_events "
            f"WHERE instance = ? AND event_id IN ({placeholders})",
            [self._instance, *ids],
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
            "(event_id, instance, event_type, event_timestamp, amount, raw, synced_at, wallet_record_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eid,
                self._instance,
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
            "(event_id, instance, event_type, event_timestamp, amount, raw, synced_at, wallet_record_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eid,
                self._instance,
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
            excluded: Number of zero-amount events excluded from sync.
        """
        ran_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO sync_runs "
            "(instance, status, ran_at, saved, failed, excluded) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (instance, status, ran_at, saved, failed, excluded),
        )
        self._conn.commit()

    def get_sync_run(self, instance: str) -> dict | None:
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


# ---------------------------------------------------------------------------
# Legacy per-instance DB migration (issue #173)
# ---------------------------------------------------------------------------


def migrate_legacy_databases(shared_db_path: Path) -> None:
    """Migrate existing per-instance ``sync.db`` files into the shared database.

    Trigger condition: the shared database at *shared_db_path* does not yet
    exist, but at least one old per-instance ``{root}/sync/{name}/sync.db``
    file exists.  When the shared DB is already present this function is a
    no-op (idempotent by design).

    For each discovered per-instance database:
    - ``processed_events`` rows are copied with ``instance`` stamped from the
      directory name.
    - ``auth_state`` rows are copied as-is (already keyed by instance name).
    - Locked or corrupt databases are skipped with a ``WARNING`` log.
    - Old database files are left untouched — no deletion.

    Args:
        shared_db_path: Destination path for the new shared ``sync.db``.
    """
    if shared_db_path.exists():
        return

    root_data_dir = shared_db_path.parent
    sync_dir = root_data_dir / "sync"
    if not sync_dir.is_dir():
        return

    old_dbs: list[tuple[str, Path]] = []
    for entry in sorted(sync_dir.iterdir()):
        if entry.is_dir():
            candidate = entry / "sync.db"
            if candidate.exists():
                old_dbs.append((entry.name, candidate))

    if not old_dbs:
        return

    # Open (create) the shared DB with the new schema
    with EventRepository(shared_db_path, instance="") as _:
        pass  # schema creation handled by __init__

    shared_conn = sqlite3.connect(shared_db_path)
    try:
        for name, old_path in old_dbs:
            try:
                _copy_legacy_db(shared_conn, old_path, name)
            except Exception as exc:
                log.warning(
                    "Skipping legacy DB for instance %r (%s): %s",
                    name,
                    old_path,
                    exc,
                )
        shared_conn.commit()
    finally:
        shared_conn.close()


def _copy_legacy_db(shared_conn: sqlite3.Connection, old_path: Path, name: str) -> None:
    """Copy rows from *old_path* into *shared_conn*, stamping ``instance = name``."""
    old_conn = sqlite3.connect(old_path)
    try:
        # Validate that it is a proper SQLite file by querying the schema
        tables = {
            row[0]
            for row in old_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "processed_events" in tables:
            old_cols = {
                row[1]
                for row in old_conn.execute(
                    "PRAGMA table_info(processed_events)"
                ).fetchall()
            }
            rows = old_conn.execute("SELECT * FROM processed_events").fetchall()
            col_list = [
                row[1]
                for row in old_conn.execute(
                    "PRAGMA table_info(processed_events)"
                ).fetchall()
            ]
            for row in rows:
                row_dict = dict(zip(col_list, row, strict=True))
                shared_conn.execute(
                    "INSERT OR IGNORE INTO processed_events "
                    "(event_id, instance, event_type, event_timestamp, amount, raw, "
                    "synced_at, wallet_record_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row_dict.get("event_id", ""),
                        name,
                        row_dict.get("event_type", ""),
                        row_dict.get("event_timestamp", ""),
                        row_dict.get("amount", ""),
                        row_dict.get("raw", ""),
                        row_dict.get("synced_at", ""),
                        row_dict.get("wallet_record_id"),
                    ),
                )
            pe_count = len(rows)
        else:
            pe_count = 0
            old_cols = set()

        if "auth_state" in tables:
            auth_rows = old_conn.execute("SELECT * FROM auth_state").fetchall()
            for auth_row in auth_rows:
                shared_conn.execute(
                    "INSERT OR IGNORE INTO auth_state (instance, status, updated_at) "
                    "VALUES (?, ?, ?)",
                    auth_row,
                )
            auth_count = len(auth_rows)
        else:
            auth_count = 0

        log.info(
            "Migrated %d processed_events and %d auth_state rows from sync/%s/sync.db",
            pe_count,
            auth_count,
            name,
        )
        _ = old_cols  # referenced to satisfy linter (used above for dict building)
    finally:
        old_conn.close()
