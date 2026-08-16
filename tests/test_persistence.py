from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.persistence import (
    EventRepository,
    dedup_event_id,
    event_id,
)

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
    event = {
        "eventType": "INTEREST_PAYMENT",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "5.00",
        "title": "Interest",
    }
    result = dedup_event_id(event)
    assert result.startswith("hash:")
    assert len(result) == len("hash:") + 64


def test_dedup_event_id_hash_is_deterministic():
    event = {
        "eventType": "BUY_ORDER",
        "timestamp": "2024-06-01T10:00:00Z",
        "amount": "100",
        "title": "AAPL",
    }
    first = dedup_event_id(event)
    second = dedup_event_id(event)
    assert first == second


def test_dedup_event_id_different_events_produce_different_hashes():
    e1 = {
        "eventType": "BUY_ORDER",
        "timestamp": "2024-06-01T10:00:00Z",
        "amount": "100",
        "title": "AAPL",
    }
    e2 = {
        "eventType": "SELL_ORDER",
        "timestamp": "2024-06-01T10:00:00Z",
        "amount": "100",
        "title": "AAPL",
    }
    assert dedup_event_id(e1) != dedup_event_id(e2)


# ---------------------------------------------------------------------------
# EventRepository — schema
# ---------------------------------------------------------------------------


def test_repo_creates_table(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        # Verify table exists: mark_processed + is_processed queries the table;
        # a missing table would raise OperationalError.
        event = {"id": "table-check", "timestamp": "2024-01-01T00:00:00Z"}
        repo.mark_processed(event)
        repo.commit()
        assert repo.is_processed("table-check")


def test_repo_creates_index(tmp_path):
    db_path = tmp_path / "test.db"
    with EventRepository(db_path):
        pass
    # Verify the index exists via an independent sqlite3 connection to avoid
    # using the private repo._conn while still checking the actual schema.
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_synced_at'"
    ).fetchall()
    conn.close()
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


def test_repo_mark_processed_raw_contains_event_payload(tmp_path):
    event = {
        "id": "evt-1",
        "eventType": "BUY_ORDER",
        "timestamp": "2024-01-15T10:00:00Z",
        "amount": {"value": 100.0, "currency": "EUR"},
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        assert repo.is_processed("evt-1")
        raw = repo.get_raw("evt-1")

    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["id"] == "evt-1"
    assert parsed["eventType"] == "BUY_ORDER"
    assert parsed["timestamp"] == "2024-01-15T10:00:00Z"
    assert "100" in str(parsed.get("amount", ""))


def test_repo_mark_processed_raw_is_valid_json(tmp_path):
    event = {
        "id": "evt-json",
        "eventType": "SELL_ORDER",
        "timestamp": "2024-01-01T00:00:00Z",
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        raw = repo.get_raw("evt-json")

    parsed = json.loads(raw)
    assert parsed["eventType"] == "SELL_ORDER"


def test_repo_mark_processed_is_idempotent(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        event = {"id": "evt-2", "timestamp": "2024-01-15T10:00:00Z"}
        repo.mark_processed(event)
        repo.mark_processed(event)
        repo.commit()
        assert repo.count_processed() == 1


def test_repo_mark_processed_handles_non_serializable_event(tmp_path):
    """json.dumps raises TypeError → fallback to str(event) (lines 111-112)."""

    class _Unserializable:
        def __repr__(self):
            return "<Unserializable>"

    event = {
        "id": "evt-bad",
        "timestamp": "2024-01-01T00:00:00Z",
        "data": _Unserializable(),
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        row = repo.get_raw("evt-bad")
    assert row is not None
    # raw should be the str() fallback, not valid JSON
    assert "Unserializable" in row


def test_repo_mark_processed_raw_falls_back_to_str_on_type_error(tmp_path, monkeypatch):
    """When json.dumps raises TypeError, raw is set to str(event)."""
    from unittest.mock import patch

    event = {"id": "evt-fallback", "timestamp": "2024-01-01T00:00:00Z"}
    with (
        patch("app.persistence.json.dumps", side_effect=TypeError("not serialisable")),
        EventRepository(tmp_path / "db") as repo,
    ):
        repo.mark_processed(event)
        repo.commit()
        raw = repo.get_raw("evt-fallback")

    assert "evt-fallback" in raw


# ---------------------------------------------------------------------------
# EventRepository — purge_old_records
# ---------------------------------------------------------------------------


def _mark_processed_at(
    repo: EventRepository, event_id: str, synced_at: datetime, monkeypatch
) -> None:
    """Insert a processed event with a specific synced_at timestamp via monkeypatching."""
    import app.persistence as persistence_mod

    fake_now = MagicMock(return_value=synced_at)
    monkeypatch.setattr(persistence_mod, "datetime", MagicMock(now=fake_now, UTC=UTC))
    repo.mark_processed({"id": event_id, "timestamp": synced_at.isoformat()})
    repo.commit()
    monkeypatch.undo()


def test_purge_removes_old_records(tmp_path, monkeypatch):
    with EventRepository(tmp_path / "db") as repo:
        old = datetime.now(UTC) - timedelta(days=90)
        recent = datetime.now(UTC)
        _mark_processed_at(repo, "old-evt", old, monkeypatch)
        _mark_processed_at(repo, "recent-evt", recent, monkeypatch)

        deleted = repo.purge_old_records(ttl_days=60)

        assert deleted == 1
        assert repo.is_processed("recent-evt")
        assert not repo.is_processed("old-evt")


def test_purge_returns_zero_when_nothing_to_delete(tmp_path, monkeypatch):
    with EventRepository(tmp_path / "db") as repo:
        recent = datetime.now(UTC)
        _mark_processed_at(repo, "recent-evt", recent, monkeypatch)
        assert repo.purge_old_records(ttl_days=60) == 0


def test_purge_empty_db_returns_zero(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        assert repo.purge_old_records() == 0


# ---------------------------------------------------------------------------
# EventRepository — context manager
# ---------------------------------------------------------------------------


def test_repo_context_manager_closes_connection(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        repo.is_processed("any")


# ---------------------------------------------------------------------------
# EventRepository — wallet_record_id schema & migration
# ---------------------------------------------------------------------------


def test_repo_schema_has_wallet_record_id_column(tmp_path):
    # Verify column exists by exercising public API — get_wallet_record_id reads that column
    event = {"id": "schema-check", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-schema")
        repo.commit()
        assert repo.get_wallet_record_id(event) == "wid-schema"


def test_repo_migration_adds_wallet_record_id_to_existing_db(tmp_path):
    """Opening an old DB (without wallet_record_id) should add the column."""
    db = tmp_path / "old.db"
    # Create a legacy DB without wallet_record_id
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE processed_events ("
        "event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL DEFAULT '', "
        "event_timestamp TEXT NOT NULL DEFAULT '', amount TEXT NOT NULL DEFAULT '', "
        "raw TEXT NOT NULL DEFAULT '', synced_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    event = {"id": "migration-check", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed(event, wallet_record_id="wid-migrated")
        repo.commit()
        assert repo.get_wallet_record_id(event) == "wid-migrated"


# ---------------------------------------------------------------------------
# EventRepository — wallet_record_id read / write
# ---------------------------------------------------------------------------


def test_mark_processed_stores_wallet_record_id(tmp_path):
    event = {"id": "evt-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-abc")
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result == "wid-abc"


def test_mark_processed_without_wallet_record_id_stores_null(tmp_path):
    event = {"id": "evt-no-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result is None


def test_get_wallet_record_id_returns_stored_id(tmp_path):
    event = {"id": "evt-lookup", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-xyz")
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result == "wid-xyz"


def test_get_wallet_record_id_returns_none_when_not_found(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        result = repo.get_wallet_record_id({"id": "unknown"})
    assert result is None


def test_get_wallet_record_id_returns_none_when_id_is_null(tmp_path):
    event = {"id": "evt-null-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result is None


# ---------------------------------------------------------------------------
# EventRepository — mark_processed_force (upsert for resync)
# ---------------------------------------------------------------------------


def test_mark_processed_force_inserts_new_event(tmp_path):
    """mark_processed_force should persist a new event."""
    event = {"id": "new-evt", "timestamp": "2026-07-01T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed_force(event, wallet_record_id="wid-1")
        repo.commit()
        assert repo.is_processed("new-evt")
        assert repo.get_wallet_record_id(event) == "wid-1"


def test_mark_processed_force_updates_existing_event(tmp_path):
    """mark_processed_force should update wallet_record_id for an already-processed event."""
    event = {"id": "existing-evt", "timestamp": "2026-07-01T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        # First pass: mark with original wallet ID
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()
        assert repo.get_wallet_record_id(event) == "old-wid"

        # Force-update: should replace the wallet ID
        repo.mark_processed_force(event, wallet_record_id="new-wid")
        repo.commit()
        assert repo.get_wallet_record_id(event) == "new-wid"
        # Count must remain 1 (no duplicate row)
        assert repo.count_processed() == 1


def test_mark_processed_force_replaces_excluded_with_synced(tmp_path):
    """mark_processed_force replaces NULL wallet_record_id (excluded) with a real ID."""
    event = {"id": "excluded-evt", "timestamp": "2026-07-01T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id=None)
        repo.commit()
        assert repo.get_wallet_record_id(event) is None

        repo.mark_processed_force(event, wallet_record_id="wid-abc")
        repo.commit()
        assert repo.get_wallet_record_id(event) == "wid-abc"


def test_mark_processed_force_falls_back_to_str_on_type_error(tmp_path, monkeypatch):
    """When json.dumps raises TypeError, mark_processed_force stores str(event)."""
    from unittest.mock import patch

    event = {"id": "force-fallback", "timestamp": "2026-07-01T00:00:00Z"}
    with (
        patch("app.persistence.json.dumps", side_effect=TypeError("not serialisable")),
        EventRepository(tmp_path / "db") as repo,
    ):
        repo.mark_processed_force(event, wallet_record_id="wid-x")
        repo.commit()
        raw = repo.get_raw("force-fallback")

    assert "force-fallback" in raw


# ---------------------------------------------------------------------------
# EventRepository — auth_state table
# ---------------------------------------------------------------------------


def test_auth_state_table_created(tmp_path):
    """auth_state table must exist after EventRepository init."""
    db_path = tmp_path / "test.db"
    with EventRepository(db_path):
        pass
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_state'"
    ).fetchall()
    conn.close()
    assert rows, "auth_state table was not created"


def test_set_and_get_auth_state_ok(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        repo.set_auth_state("david", "ok")
        assert repo.get_auth_state("david") == "ok"


def test_set_and_get_auth_state_failed(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        repo.set_auth_state("david", "failed")
        assert repo.get_auth_state("david") == "failed"


def test_set_and_get_auth_state_expired(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        repo.set_auth_state("david", "expired")
        assert repo.get_auth_state("david") == "expired"


def test_get_auth_state_returns_none_when_absent(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        assert repo.get_auth_state("nonexistent") is None


def test_set_auth_state_overwrites_existing(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        repo.set_auth_state("david", "ok")
        repo.set_auth_state("david", "failed")
        assert repo.get_auth_state("david") == "failed"


def test_auth_state_migration_adds_table_to_existing_db(tmp_path):
    """Opening an existing DB without auth_state table must create it automatically."""
    db_path = tmp_path / "test.db"
    # Create an old-style DB without auth_state table
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE processed_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL DEFAULT '',
            event_timestamp TEXT NOT NULL DEFAULT '',
            amount TEXT NOT NULL DEFAULT '',
            raw TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL,
            wallet_record_id TEXT
        )"""
    )
    conn.commit()
    conn.close()

    # Opening with EventRepository must create auth_state table automatically
    with EventRepository(db_path) as repo:
        repo.set_auth_state("eli", "ok")
        assert repo.get_auth_state("eli") == "ok"


def test_set_auth_state_persists_updated_at(tmp_path):
    """set_auth_state must store a non-empty updated_at timestamp."""
    db_path = tmp_path / "test.db"
    with EventRepository(db_path) as repo:
        repo.set_auth_state("david", "ok")

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT updated_at FROM auth_state WHERE instance = 'david'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0]  # non-empty


# ---------------------------------------------------------------------------
# EventRepository — sync_runs table
# ---------------------------------------------------------------------------


def test_sync_runs_table_created(tmp_path):
    """sync_runs table must exist after EventRepository init."""
    db_path = tmp_path / "test.db"
    with EventRepository(db_path):
        pass
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_runs'"
    ).fetchall()
    conn.close()
    assert rows, "sync_runs table was not created"


def test_set_and_get_sync_run_success(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        repo.set_sync_run("david", status="success", saved=3, failed=0, excluded=1)
        run = repo.get_sync_run("david")
    assert run is not None
    assert run["status"] == "success"
    assert run["saved"] == 3
    assert run["failed"] == 0
    assert run["excluded"] == 1
    assert run["ran_at"]


def test_set_and_get_sync_run_failed(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        repo.set_sync_run("david", status="failed", saved=0, failed=2, excluded=0)
        run = repo.get_sync_run("david")
    assert run is not None
    assert run["status"] == "failed"
    assert run["saved"] == 0
    assert run["failed"] == 2


def test_get_sync_run_returns_none_when_absent(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        assert repo.get_sync_run("nonexistent") is None


def test_set_sync_run_overwrites_existing(tmp_path):
    with EventRepository(tmp_path / "test.db") as repo:
        repo.set_sync_run("david", status="success", saved=1, failed=0, excluded=0)
        repo.set_sync_run("david", status="failed", saved=0, failed=1, excluded=0)
        run = repo.get_sync_run("david")
    assert run["status"] == "failed"


def test_sync_runs_migration_adds_table_to_existing_db(tmp_path):
    """Opening an existing DB without sync_runs table must create it automatically."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE processed_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL DEFAULT '',
            event_timestamp TEXT NOT NULL DEFAULT '',
            amount TEXT NOT NULL DEFAULT '',
            raw TEXT NOT NULL DEFAULT '',
            synced_at TEXT NOT NULL,
            wallet_record_id TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE auth_state (
            instance TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()

    with EventRepository(db_path) as repo:
        repo.set_sync_run("eli", status="success", saved=5, failed=0, excluded=2)
        run = repo.get_sync_run("eli")
    assert run["status"] == "success"
    assert run["saved"] == 5


def test_set_sync_run_stores_ran_at_timestamp(tmp_path):
    """set_sync_run must store a non-empty UTC ran_at timestamp."""
    db_path = tmp_path / "test.db"
    with EventRepository(db_path) as repo:
        repo.set_sync_run("david", status="success", saved=1, failed=0, excluded=0)

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT ran_at FROM sync_runs WHERE instance = 'david'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0]
