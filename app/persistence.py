from __future__ import annotations

import csv
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.tr_mapper import normalize_event_time

log = logging.getLogger(__name__)


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
            str(event.get("eventType") or event.get("type") or event.get("event_type") or ""),
            normalize_event_time(event),
            str(event.get("amount") or event.get("value") or ""),
            str(event.get("title") or event.get("name") or event.get("description") or ""),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


# ---------------------------------------------------------------------------
# EventRepository
# ---------------------------------------------------------------------------

class EventRepository:
    """Manages the SQLite dedup database.

    Usage::

        with EventRepository(db_path) as repo:
            new_events = repo.filter_unprocessed(events)
            repo.mark_processed(event)
            repo.commit()
    """

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS processed_events "
            "(event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL)"
        )
        self._conn.commit()

    def filter_unprocessed(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events_with_ids = [(event, dedup_event_id(event)) for event in events]
        ids = [dedup_id for _, dedup_id in events_with_ids]
        if not ids:
            return []

        placeholders = ",".join("?" for _ in ids)
        rows = self._conn.execute(
            f"SELECT event_id FROM processed_events WHERE event_id IN ({placeholders})", ids
        ).fetchall()
        processed = {row[0] for row in rows}

        return [event for event, dedup_id in events_with_ids if dedup_id not in processed]

    def mark_processed(self, event: dict[str, Any]) -> None:
        eid = dedup_event_id(event)
        event_time = normalize_event_time(event)
        self._conn.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, timestamp) VALUES (?, ?)",
            (eid, event_time),
        )

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "EventRepository":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# CSV backup (stateless helper)
# ---------------------------------------------------------------------------

def backup_csv(output_dir: Path, owner_name: str, events: list[dict[str, Any]]) -> Path:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    backup_file = output_dir / f"TradeRepublic_{owner_name}_{month}.csv"
    headers = ["event_id", "event_type", "timestamp", "raw"]

    file_has_data = backup_file.exists() and backup_file.stat().st_size > 0
    with backup_file.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        if not file_has_data:
            writer.writeheader()

        for event in events:
            writer.writerow(
                {
                    "event_id": dedup_event_id(event),
                    "event_type": event.get("eventType") or event.get("type") or event.get("event_type") or "",
                    "timestamp": normalize_event_time(event),
                    "raw": str(event),
                }
            )
    return backup_file
