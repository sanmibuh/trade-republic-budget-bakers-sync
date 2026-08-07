from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.config import _required_env
from app.persistence import (
    backup_csv,
    dedup_event_id,
    event_id,
    filter_unprocessed,
    init_db,
    mark_processed,
)
from app.main import _filter_by_lookback


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
# event_id
# ---------------------------------------------------------------------------

def test_event_id_uses_id_field():
    assert event_id({"id": "abc"}) == "abc"


def test_event_id_uses_eventId():
    assert event_id({"eventId": "xyz"}) == "xyz"


def test_event_id_uses_event_id():
    assert event_id({"event_id": "qrs"}) == "qrs"


def test_event_id_missing_returns_empty():
    assert event_id({"foo": "bar"}) == ""


def test_event_id_prefers_id_over_eventId():
    assert event_id({"id": "first", "eventId": "second"}) == "first"


# ---------------------------------------------------------------------------
# dedup_event_id
# ---------------------------------------------------------------------------

def test_dedup_event_id_returns_native_id_when_present():
    assert dedup_event_id({"id": "native-id"}) == "native-id"


def test_dedup_event_id_falls_back_to_hash():
    event = {"eventType": "INTEREST_PAYMENT", "timestamp": "2024-01-01T00:00:00Z", "amount": "5.00", "title": "Interest"}
    result = dedup_event_id(event)
    assert result.startswith("hash:")
    assert len(result) == len("hash:") + 64  # sha256 hex


def test_dedup_event_id_hash_is_deterministic():
    event = {"eventType": "BUY_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    first = dedup_event_id(event)
    second = dedup_event_id(event)
    assert first == second


def test_dedup_event_id_different_events_produce_different_hashes():
    e1 = {"eventType": "BUY_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    e2 = {"eventType": "SELL_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    assert dedup_event_id(e1) != dedup_event_id(e2)


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------

def test_init_db_creates_table(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='processed_events'")
    assert cursor.fetchone() is not None
    conn.close()


def test_init_db_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    conn.close()
    conn2 = init_db(db_path)
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
    assert len(_filter_by_lookback(events, since)) == 1


def test_filter_by_lookback_keeps_event_without_timestamp():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [{"amount": "5"}]
    assert len(_filter_by_lookback(events, since)) == 1


def test_filter_by_lookback_naive_timestamp_treated_as_utc():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [_make_event("2024-01-11T00:00:00")]
    assert len(_filter_by_lookback(events, since)) == 1


def test_filter_by_lookback_naive_timestamp_gets_utc():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [_make_event("2024-01-11T00:00:00")]
    assert len(_filter_by_lookback(events, since)) == 1
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [
        _make_event("2024-01-09T00:00:00Z"),
        _make_event("2024-01-11T00:00:00Z"),
        _make_event("2024-01-12T00:00:00Z"),
    ]
    result = _filter_by_lookback(events, since)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# filter_unprocessed
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
    result = filter_unprocessed(events, conn)
    assert len(result) == 2
    conn.close()


def test_filter_unprocessed_skips_already_processed():
    conn = _in_memory_db()
    conn.execute("INSERT INTO processed_events VALUES ('already', '2024-01-01')")
    conn.commit()
    events = [{"id": "already"}, {"id": "new"}]
    result = filter_unprocessed(events, conn)
    assert len(result) == 1
    assert result[0]["id"] == "new"
    conn.close()


def test_filter_unprocessed_empty_input():
    conn = _in_memory_db()
    assert filter_unprocessed([], conn) == []
    conn.close()


# ---------------------------------------------------------------------------
# mark_processed
# ---------------------------------------------------------------------------

def test_mark_processed_inserts_row():
    conn = _in_memory_db()
    event = {"id": "evt-1", "timestamp": "2024-01-15T10:00:00Z"}
    mark_processed(conn, event)
    conn.commit()
    row = conn.execute("SELECT event_id FROM processed_events WHERE event_id='evt-1'").fetchone()
    assert row is not None
    conn.close()


def test_mark_processed_is_idempotent():
    conn = _in_memory_db()
    event = {"id": "evt-2", "timestamp": "2024-01-15T10:00:00Z"}
    mark_processed(conn, event)
    mark_processed(conn, event)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM processed_events WHERE event_id='evt-2'").fetchone()[0]
    assert count == 1
    conn.close()


# ---------------------------------------------------------------------------
# backup_csv
# ---------------------------------------------------------------------------

def test_backup_csv_creates_file(tmp_path):
    events = [
        {"id": "e1", "eventType": "BUY_ORDER", "timestamp": "2024-01-15T10:00:00Z", "amount": "100"},
        {"id": "e2", "eventType": "SELL_ORDER", "timestamp": "2024-01-16T10:00:00Z", "amount": "200"},
    ]
    path = backup_csv(tmp_path, "TestUser", events)
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "event_id" in content
    assert "e1" in content
    assert "e2" in content


def test_backup_csv_appends_on_second_call(tmp_path):
    event1 = [{"id": "e1", "eventType": "BUY_ORDER", "timestamp": "2024-01-15T10:00:00Z", "amount": "100"}]
    event2 = [{"id": "e2", "eventType": "SELL_ORDER", "timestamp": "2024-01-16T10:00:00Z", "amount": "200"}]
    backup_csv(tmp_path, "TestUser", event1)
    backup_csv(tmp_path, "TestUser", event2)
    csv_file = next(tmp_path.glob("*.csv"))
    content = csv_file.read_text(encoding="utf-8")
    assert content.count("event_id") == 1  # header written only once
    assert "e1" in content
    assert "e2" in content
