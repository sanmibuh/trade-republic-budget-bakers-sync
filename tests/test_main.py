from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.config import _required_env
from app.persistence import (
    EventRepository,
    backup_csv,
    dedup_event_id,
    event_id,
)
from app.tr_mapper import filter_by_lookback


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
    assert len(result) == len("hash:") + 64


def test_dedup_event_id_hash_is_deterministic():
    event = {"eventType": "BUY_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    assert dedup_event_id(event) == dedup_event_id(event)


def test_dedup_event_id_different_events_produce_different_hashes():
    e1 = {"eventType": "BUY_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    e2 = {"eventType": "SELL_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    assert dedup_event_id(e1) != dedup_event_id(e2)


# ---------------------------------------------------------------------------
# EventRepository
# ---------------------------------------------------------------------------

def test_repo_creates_table(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        rows = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_events'"
        ).fetchall()
        assert rows


def test_repo_idempotent_init(tmp_path):
    db = tmp_path / "test.db"
    with EventRepository(db):
        pass
    with EventRepository(db):
        pass  # second open should not raise


def test_repo_filter_unprocessed_all_new(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        events = [{"id": "a"}, {"id": "b"}]
        assert len(repo.filter_unprocessed(events)) == 2


def test_repo_filter_unprocessed_skips_already_processed(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        repo._conn.execute("INSERT INTO processed_events VALUES ('already', '2024-01-01')")
        repo._conn.commit()
        events = [{"id": "already"}, {"id": "new"}]
        result = repo.filter_unprocessed(events)
        assert len(result) == 1
        assert result[0]["id"] == "new"


def test_repo_filter_unprocessed_empty_input(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        assert repo.filter_unprocessed([]) == []


def test_repo_mark_processed_inserts_row(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        event = {"id": "evt-1", "timestamp": "2024-01-15T10:00:00Z"}
        repo.mark_processed(event)
        repo.commit()
        row = repo._conn.execute(
            "SELECT event_id FROM processed_events WHERE event_id='evt-1'"
        ).fetchone()
        assert row is not None


def test_repo_mark_processed_is_idempotent(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        event = {"id": "evt-2", "timestamp": "2024-01-15T10:00:00Z"}
        repo.mark_processed(event)
        repo.mark_processed(event)
        repo.commit()
        count = repo._conn.execute(
            "SELECT COUNT(*) FROM processed_events WHERE event_id='evt-2'"
        ).fetchone()[0]
        assert count == 1


def test_repo_context_manager_closes_connection(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        conn = repo._conn
    # After __exit__ the connection is closed; any operation should raise
    with pytest.raises(Exception):
        conn.execute("SELECT 1")


# ---------------------------------------------------------------------------
# filter_by_lookback
# ---------------------------------------------------------------------------

def _make_event(timestamp: str, **kwargs) -> dict:
    return {"timestamp": timestamp, **kwargs}


def test_filter_by_lookback_keeps_recent():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([_make_event("2024-01-11T00:00:00Z")], since)) == 1


def test_filter_by_lookback_removes_old():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert filter_by_lookback([_make_event("2024-01-09T00:00:00Z")], since) == []


def test_filter_by_lookback_keeps_event_on_boundary():
    since = datetime(2024, 1, 10, 0, 0, 0, tzinfo=timezone.utc)
    assert len(filter_by_lookback([_make_event("2024-01-10T00:00:00Z")], since)) == 1


def test_filter_by_lookback_keeps_event_with_unparseable_timestamp():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([{"timestamp": "not-a-date"}], since)) == 1


def test_filter_by_lookback_keeps_event_without_timestamp():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([{"amount": "5"}], since)) == 1


def test_filter_by_lookback_naive_timestamp_treated_as_utc():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    assert len(filter_by_lookback([_make_event("2024-01-11T00:00:00")], since)) == 1


def test_filter_by_lookback_multiple_events():
    since = datetime(2024, 1, 10, tzinfo=timezone.utc)
    events = [
        _make_event("2024-01-09T00:00:00Z"),
        _make_event("2024-01-11T00:00:00Z"),
        _make_event("2024-01-12T00:00:00Z"),
    ]
    assert len(filter_by_lookback(events, since)) == 2


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
    backup_csv(tmp_path, "TestUser", [{"id": "e1", "eventType": "BUY_ORDER", "timestamp": "2024-01-15T10:00:00Z", "amount": "100"}])
    backup_csv(tmp_path, "TestUser", [{"id": "e2", "eventType": "SELL_ORDER", "timestamp": "2024-01-16T10:00:00Z", "amount": "200"}])
    content = next(tmp_path.glob("*.csv")).read_text(encoding="utf-8")
    assert content.count("event_id") == 1
    assert "e1" in content
    assert "e2" in content
