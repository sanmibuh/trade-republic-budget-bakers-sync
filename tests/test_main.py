from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.main import (
    _dedup_event_id,
    _event_id,
    _filter_by_lookback,
    _filter_unprocessed_events,
    _init_db,
    _mark_processed,
    _required_env,
)


# ---------------------------------------------------------------------------
# _required_env
# ---------------------------------------------------------------------------

def test_required_env_present(monkeypatch):
    monkeypatch.setenv("MY_VAR", "hello")
    assert _required_env("MY_VAR") == "hello"


def test_required_env_missing(monkeypatch):
    monkeypatch.delenv("MY_VAR", raising=False)
    with pytest.raises(ValueError, match="MY_VAR"):
        _required_env("MY_VAR")


def test_required_env_blank(monkeypatch):
    monkeypatch.setenv("MY_VAR", "   ")
    with pytest.raises(ValueError, match="MY_VAR"):
        _required_env("MY_VAR")


# ---------------------------------------------------------------------------
# _event_id
# ---------------------------------------------------------------------------

def test_event_id_uses_id_field():
    assert _event_id({"id": "abc"}) == "abc"


def test_event_id_uses_eventId():
    assert _event_id({"eventId": "xyz"}) == "xyz"


def test_event_id_uses_event_id():
    assert _event_id({"event_id": "qrs"}) == "qrs"


def test_event_id_missing_returns_empty():
    assert _event_id({"foo": "bar"}) == ""


def test_event_id_prefers_id_over_eventId():
    assert _event_id({"id": "first", "eventId": "second"}) == "first"


# ---------------------------------------------------------------------------
# _dedup_event_id
# ---------------------------------------------------------------------------

def test_dedup_event_id_returns_native_id_when_present():
    assert _dedup_event_id({"id": "native-id"}) == "native-id"


def test_dedup_event_id_falls_back_to_hash():
    event = {"eventType": "INTEREST_PAYMENT", "timestamp": "2024-01-01T00:00:00Z", "amount": "5.00", "title": "Interest"}
    result = _dedup_event_id(event)
    assert result.startswith("hash:")
    assert len(result) == len("hash:") + 64  # sha256 hex


def test_dedup_event_id_hash_is_deterministic():
    event = {"eventType": "BUY_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    first = _dedup_event_id(event)
    second = _dedup_event_id(event)
    assert first == second


def test_dedup_event_id_different_events_produce_different_hashes():
    e1 = {"eventType": "BUY_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    e2 = {"eventType": "SELL_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    assert _dedup_event_id(e1) != _dedup_event_id(e2)


# ---------------------------------------------------------------------------
# _init_db
# ---------------------------------------------------------------------------

def test_init_db_creates_table(tmp_path):
    db_path = tmp_path / "test.db"
    conn = _init_db(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='processed_events'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    conn = _init_db(db_path)
    conn.close()
    # Second call should not raise
    conn2 = _init_db(db_path)
    conn2.close()


# ---------------------------------------------------------------------------
# _filter_by_lookback
# ---------------------------------------------------------------------------

def _make_event(timestamp: str, **kwargs) -> dict:
    return {"timestamp": timestamp, **kwargs}


def test_filter_by_lookback_keeps_recent():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [_make_event("2024-01-11T00:00:00Z")]
    assert len(_filter_by_lookback(events, since)) == 1


def test_filter_by_lookback_removes_old():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [_make_event("2024-01-09T00:00:00Z")]
    assert _filter_by_lookback(events, since) == []


def test_filter_by_lookback_keeps_event_on_boundary():
    since = datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)
    events = [_make_event("2024-01-10T00:00:00Z")]
    assert len(_filter_by_lookback(events, since)) == 1


def test_filter_by_lookback_keeps_event_with_unparseable_timestamp():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [{"timestamp": "not-a-date"}]
    # unparseable timestamps are kept (fail-open)
    assert len(_filter_by_lookback(events, since)) == 1


def test_filter_by_lookback_keeps_event_without_timestamp():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [{"amount": "5"}]
    assert len(_filter_by_lookback(events, since)) == 1


def test_filter_by_lookback_mixed():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [
        _make_event("2024-01-09T00:00:00Z"),
        _make_event("2024-01-11T00:00:00Z"),
        _make_event("2024-01-12T00:00:00Z"),
    ]
    result = _filter_by_lookback(events, since)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# _filter_unprocessed_events
# ---------------------------------------------------------------------------

def _in_memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE processed_events (event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def test_filter_unprocessed_all_new():
    conn = _in_memory_db()
    events = [{"id": "a"}, {"id": "b"}]
    result = _filter_unprocessed_events(events, conn)
    assert len(result) == 2
    conn.close()


def test_filter_unprocessed_skips_already_processed():
    conn = _in_memory_db()
    conn.execute("INSERT INTO processed_events VALUES ('already', '2024-01-01')")
    conn.commit()
    events = [{"id": "already"}, {"id": "new"}]
    result = _filter_unprocessed_events(events, conn)
    assert len(result) == 1
    assert result[0]["id"] == "new"
    conn.close()


def test_filter_unprocessed_empty_input():
    conn = _in_memory_db()
    assert _filter_unprocessed_events([], conn) == []
    conn.close()


# ---------------------------------------------------------------------------
# _mark_processed
# ---------------------------------------------------------------------------

def test_mark_processed_inserts_row():
    conn = _in_memory_db()
    event = {"id": "evt-1", "timestamp": "2024-01-15T10:00:00Z"}
    _mark_processed(conn, event)
    conn.commit()
    row = conn.execute("SELECT event_id FROM processed_events WHERE event_id='evt-1'").fetchone()
    assert row is not None
    conn.close()


def test_mark_processed_is_idempotent():
    conn = _in_memory_db()
    event = {"id": "evt-2", "timestamp": "2024-01-15T10:00:00Z"}
    _mark_processed(conn, event)
    _mark_processed(conn, event)  # INSERT OR IGNORE — should not raise
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM processed_events WHERE event_id='evt-2'").fetchone()[0]
    assert count == 1
    conn.close()
