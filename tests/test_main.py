from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from app.config import _positive_int_env, _required_env
from app.persistence import (
    EventRepository,
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
# _positive_int_env
# ---------------------------------------------------------------------------

def test_positive_int_env_uses_default(monkeypatch):
    monkeypatch.delenv("LOOKBACK_DAYS", raising=False)
    assert _positive_int_env("LOOKBACK_DAYS", default=7) == 7


def test_positive_int_env_reads_env(monkeypatch):
    monkeypatch.setenv("LOOKBACK_DAYS", "14")
    assert _positive_int_env("LOOKBACK_DAYS", default=7) == 14


def test_positive_int_env_rejects_non_integer(monkeypatch):
    monkeypatch.setenv("LOOKBACK_DAYS", "abc")
    with pytest.raises(ValueError, match="integer"):
        _positive_int_env("LOOKBACK_DAYS", default=7)


def test_positive_int_env_rejects_zero(monkeypatch):
    monkeypatch.setenv("LOOKBACK_DAYS", "0")
    with pytest.raises(ValueError, match="positive"):
        _positive_int_env("LOOKBACK_DAYS", default=7)


def test_positive_int_env_rejects_negative(monkeypatch):
    monkeypatch.setenv("LOOKBACK_DAYS", "-5")
    with pytest.raises(ValueError, match="positive"):
        _positive_int_env("LOOKBACK_DAYS", default=7)


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
    first = dedup_event_id(event)
    second = dedup_event_id(event)
    assert first == second


def test_dedup_event_id_different_events_produce_different_hashes():
    e1 = {"eventType": "BUY_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    e2 = {"eventType": "SELL_ORDER", "timestamp": "2024-06-01T10:00:00Z", "amount": "100", "title": "AAPL"}
    assert dedup_event_id(e1) != dedup_event_id(e2)


# ---------------------------------------------------------------------------
# EventRepository — schema
# ---------------------------------------------------------------------------

def test_repo_creates_table(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        rows = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='processed_events'"
        ).fetchall()
        assert rows


def test_repo_creates_index(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        rows = repo._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_synced_at'"
        ).fetchall()
        assert rows


def test_repo_idempotent_init(tmp_path):
    db = tmp_path / "test.db"
    with EventRepository(db):
        pass
    with EventRepository(db):
        pass


# ---------------------------------------------------------------------------
# EventRepository — filter_unprocessed
# ---------------------------------------------------------------------------

def test_repo_filter_unprocessed_all_new(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        assert len(repo.filter_unprocessed([{"id": "a"}, {"id": "b"}])) == 2


def test_repo_filter_unprocessed_skips_already_processed(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        event = {"id": "already", "timestamp": "2024-01-01T00:00:00Z"}
        repo.mark_processed(event)
        repo.commit()
        result = repo.filter_unprocessed([{"id": "already"}, {"id": "new"}])
        assert len(result) == 1
        assert result[0]["id"] == "new"


def test_repo_filter_unprocessed_empty_input(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        assert repo.filter_unprocessed([]) == []


# ---------------------------------------------------------------------------
# EventRepository — mark_processed
# ---------------------------------------------------------------------------

def test_repo_mark_processed_stores_all_fields(tmp_path):
    event = {
        "id": "evt-1",
        "eventType": "BUY_ORDER",
        "timestamp": "2024-01-15T10:00:00Z",
        "amount": {"value": 100.0, "currency": "EUR"},
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        row = repo._conn.execute(
            "SELECT event_id, event_type, event_timestamp, amount, raw, synced_at "
            "FROM processed_events WHERE event_id='evt-1'"
        ).fetchone()

    assert row is not None
    event_id_val, event_type, event_timestamp, amount, raw, synced_at = row
    assert event_id_val == "evt-1"
    assert event_type == "BUY_ORDER"
    assert event_timestamp == "2024-01-15T10:00:00Z"
    assert "100" in amount or "100.0" in amount
    assert "BUY_ORDER" in raw
    assert synced_at  # non-empty ISO timestamp


def test_repo_mark_processed_raw_is_valid_json(tmp_path):
    event = {"id": "evt-json", "eventType": "SELL_ORDER", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        raw = repo._conn.execute(
            "SELECT raw FROM processed_events WHERE event_id='evt-json'"
        ).fetchone()[0]

    parsed = json.loads(raw)
    assert parsed["eventType"] == "SELL_ORDER"


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


# ---------------------------------------------------------------------------
# EventRepository — purge_old_records
# ---------------------------------------------------------------------------

def _insert_record(repo: EventRepository, event_id: str, synced_at: str) -> None:
    repo._conn.execute(
        "INSERT OR IGNORE INTO processed_events "
        "(event_id, event_type, event_timestamp, amount, raw, synced_at) "
        "VALUES (?, '', '', '', '', ?)",
        (event_id, synced_at),
    )
    repo._conn.commit()


def test_purge_removes_old_records(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        _insert_record(repo, "old-evt", old)
        _insert_record(repo, "recent-evt", recent)

        deleted = repo.purge_old_records(ttl_days=60)

        assert deleted == 1
        remaining = repo._conn.execute(
            "SELECT event_id FROM processed_events"
        ).fetchall()
        ids = {r[0] for r in remaining}
        assert "recent-evt" in ids
        assert "old-evt" not in ids


def test_purge_returns_zero_when_nothing_to_delete(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        recent = datetime.now(timezone.utc).isoformat()
        _insert_record(repo, "recent-evt", recent)
        assert repo.purge_old_records(ttl_days=60) == 0


def test_purge_empty_db_returns_zero(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        assert repo.purge_old_records() == 0


# ---------------------------------------------------------------------------
# EventRepository — context manager
# ---------------------------------------------------------------------------

def test_repo_context_manager_closes_connection(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        conn = repo._conn
    with pytest.raises(sqlite3.ProgrammingError):
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
# _build_batch — unknown event type triggers notifier
# ---------------------------------------------------------------------------

def test_build_batch_notifies_on_unknown_event_type(tmp_path):
    from app.config import Config
    from app.main import _build_batch
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}

    notifier = MagicMock(spec=Notifier)

    event = {"eventType": "TOTALLY_NEW_TYPE", "timestamp": "2024-01-01T00:00:00Z", "amount": "5.00"}
    with EventRepository(tmp_path / "test.db") as repo:
        _build_batch([event], cfg, repo, notifier)

    notifier.unknown_event_type.assert_called_once_with("TOTALLY_NEW_TYPE")


def test_build_batch_no_notification_for_known_event_type(tmp_path):
    from app.config import Config
    from app.main import _build_batch
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}

    notifier = MagicMock(spec=Notifier)

    event = {"eventType": "BUY_ORDER", "timestamp": "2024-01-01T00:00:00Z", "amount": "100.00"}
    with EventRepository(tmp_path / "test.db") as repo:
        _build_batch([event], cfg, repo, notifier)

    notifier.unknown_event_type.assert_not_called()
