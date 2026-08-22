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
    init_db,
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
# init_db
# ---------------------------------------------------------------------------


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()
    assert "processed_events" in tables
    assert "auth_state" in tables
    assert "sync_runs" in tables


def test_init_db_creates_index(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_synced_at'"
    ).fetchall()
    conn.close()
    assert rows


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    init_db(db_path)  # second call must not raise


def test_init_db_creates_file(tmp_path):
    db_path = tmp_path / "test.db"
    assert not db_path.exists()
    init_db(db_path)
    assert db_path.exists()


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """Return a path to an initialized (schema-ready) SQLite database."""
    path = tmp_path / "test.db"
    init_db(path)
    return path


# ---------------------------------------------------------------------------
# EventRepository — schema
# ---------------------------------------------------------------------------


def test_repo_creates_table(db):
    with EventRepository(db) as repo:
        event = {"id": "table-check", "timestamp": "2024-01-01T00:00:00Z"}
        repo.mark_processed(event)
        repo.commit()
        assert repo.is_processed("table-check")


def test_repo_creates_index(db):
    # Verify the index exists via an independent sqlite3 connection.
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_synced_at'"
    ).fetchall()
    conn.close()
    assert rows


def test_repo_idempotent_init(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    with EventRepository(db_path):
        pass
    with EventRepository(db_path):
        pass


# ---------------------------------------------------------------------------
# EventRepository — filter_unprocessed
# ---------------------------------------------------------------------------


def test_repo_filter_unprocessed_all_new(db):
    with EventRepository(db) as repo:
        assert len(repo.filter_unprocessed([{"id": "a"}, {"id": "b"}])) == 2


def test_repo_filter_unprocessed_skips_already_processed(db):
    with EventRepository(db) as repo:
        event = {"id": "already", "timestamp": "2024-01-01T00:00:00Z"}
        repo.mark_processed(event)
        repo.commit()
        result = repo.filter_unprocessed([{"id": "already"}, {"id": "new"}])
        assert len(result) == 1
        assert result[0]["id"] == "new"


def test_repo_filter_unprocessed_empty_input(db):
    with EventRepository(db) as repo:
        assert repo.filter_unprocessed([]) == []


# ---------------------------------------------------------------------------
# EventRepository — mark_processed
# ---------------------------------------------------------------------------


def test_repo_mark_processed_raw_contains_event_payload(db):
    event = {
        "id": "evt-1",
        "eventType": "BUY_ORDER",
        "timestamp": "2024-01-15T10:00:00Z",
        "amount": {"value": 100.0, "currency": "EUR"},
    }
    with EventRepository(db) as repo:
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


def test_repo_mark_processed_raw_is_valid_json(db):
    event = {
        "id": "evt-json",
        "eventType": "SELL_ORDER",
        "timestamp": "2024-01-01T00:00:00Z",
    }
    with EventRepository(db) as repo:
        repo.mark_processed(event)
        repo.commit()
        raw = repo.get_raw("evt-json")

    parsed = json.loads(raw)
    assert parsed["eventType"] == "SELL_ORDER"


def test_repo_mark_processed_is_idempotent(db):
    with EventRepository(db) as repo:
        event = {"id": "evt-2", "timestamp": "2024-01-15T10:00:00Z"}
        repo.mark_processed(event)
        repo.mark_processed(event)
        repo.commit()
        assert repo.count_processed() == 1


def test_repo_mark_processed_handles_non_serializable_event(db):
    """json.dumps raises TypeError → fallback to str(event) (lines 111-112)."""

    class _Unserializable:
        def __repr__(self):
            return "<Unserializable>"

    event = {
        "id": "evt-bad",
        "timestamp": "2024-01-01T00:00:00Z",
        "data": _Unserializable(),
    }
    with EventRepository(db) as repo:
        repo.mark_processed(event)
        repo.commit()
        row = repo.get_raw("evt-bad")
    assert row is not None
    # raw should be the str() fallback, not valid JSON
    assert "Unserializable" in row


def test_repo_mark_processed_raw_falls_back_to_str_on_type_error(db, monkeypatch):
    """When json.dumps raises TypeError, raw is set to str(event)."""
    from unittest.mock import patch

    event = {"id": "evt-fallback", "timestamp": "2024-01-01T00:00:00Z"}
    with (
        patch("app.persistence.json.dumps", side_effect=TypeError("not serialisable")),
        EventRepository(db) as repo,
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


def test_purge_removes_old_records(db, monkeypatch):
    with EventRepository(db) as repo:
        old = datetime.now(UTC) - timedelta(days=90)
        recent = datetime.now(UTC)
        _mark_processed_at(repo, "old-evt", old, monkeypatch)
        _mark_processed_at(repo, "recent-evt", recent, monkeypatch)

        deleted = repo.purge_old_records(ttl_days=60)

        assert deleted == 1
        assert repo.is_processed("recent-evt")
        assert not repo.is_processed("old-evt")


def test_purge_returns_zero_when_nothing_to_delete(db, monkeypatch):
    with EventRepository(db) as repo:
        recent = datetime.now(UTC)
        _mark_processed_at(repo, "recent-evt", recent, monkeypatch)
        assert repo.purge_old_records(ttl_days=60) == 0


def test_purge_empty_db_returns_zero(db):
    with EventRepository(db) as repo:
        assert repo.purge_old_records() == 0


# ---------------------------------------------------------------------------
# EventRepository — context manager
# ---------------------------------------------------------------------------


def test_repo_context_manager_closes_connection(db):
    with EventRepository(db) as repo:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        repo.is_processed("any")


# ---------------------------------------------------------------------------
# EventRepository — wallet_record_id schema
# ---------------------------------------------------------------------------


def test_repo_schema_has_wallet_record_id_column(db):
    # Verify column exists by exercising public API.
    event = {"id": "schema-check", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed(event, wallet_record_id="wid-schema")
        repo.commit()
        assert repo.get_wallet_record_id(event) == "wid-schema"


# ---------------------------------------------------------------------------
# EventRepository — wallet_record_id read / write
# ---------------------------------------------------------------------------


def test_mark_processed_stores_wallet_record_id(db):
    event = {"id": "evt-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed(event, wallet_record_id="wid-abc")
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result == "wid-abc"


def test_mark_processed_without_wallet_record_id_stores_null(db):
    event = {"id": "evt-no-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed(event)
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result is None


def test_get_wallet_record_id_returns_stored_id(db):
    event = {"id": "evt-lookup", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed(event, wallet_record_id="wid-xyz")
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result == "wid-xyz"


def test_get_wallet_record_id_returns_none_when_not_found(db):
    with EventRepository(db) as repo:
        result = repo.get_wallet_record_id({"id": "unknown"})
    assert result is None


def test_get_wallet_record_id_returns_none_when_id_is_null(db):
    event = {"id": "evt-null-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed(event)
        repo.commit()
        result = repo.get_wallet_record_id(event)
    assert result is None


# ---------------------------------------------------------------------------
# EventRepository — _build_event_row (private helper)
# ---------------------------------------------------------------------------


def test_build_event_row_returns_correct_tuple(db):
    """_build_event_row should return a tuple with the expected 8 fields and exact values."""
    import json

    event = {
        "id": "row-evt",
        "timestamp": "2026-07-01T12:00:00Z",
        "amount": 42.5,
        "type": "payment",
    }
    wallet_record_id = "wid-row"
    with EventRepository(db, instance="test-instance") as repo:
        row = repo._build_event_row(event, wallet_record_id)

    assert len(row) == 8
    eid, instance, event_type, event_timestamp, amount, raw, synced_at, wrid = row
    assert eid == "row-evt"
    assert instance == "test-instance"
    assert event_type == "PAYMENT"
    assert event_timestamp == "2026-07-01T12:00:00Z"
    assert amount == "42.5"
    parsed = json.loads(raw)
    assert parsed["id"] == "row-evt"
    assert synced_at is not None
    assert wrid == "wid-row"


def test_build_event_row_zero_amount_stored_as_zero_string(db):
    """_build_event_row must store '0' for amount=0, not an empty string."""
    event = {"id": "zero-amt", "timestamp": "2026-07-01T12:00:00Z", "amount": 0}
    with EventRepository(db) as repo:
        row = repo._build_event_row(event, None)

    amount = row[4]
    assert amount == "0"


def test_build_event_row_wallet_record_id_none(db):
    """_build_event_row stores None when wallet_record_id is not provided."""
    event = {"id": "row-none", "timestamp": "2026-07-01T12:00:00Z"}
    with EventRepository(db) as repo:
        row = repo._build_event_row(event, None)

    assert row[-1] is None


def test_build_event_row_falls_back_to_str_on_type_error(db):
    """_build_event_row falls back to str(event) when json.dumps raises TypeError."""
    from unittest.mock import patch

    event = {"id": "row-fallback", "timestamp": "2026-07-01T00:00:00Z"}
    with (
        patch("app.persistence.json.dumps", side_effect=TypeError("not serialisable")),
        EventRepository(db) as repo,
    ):
        row = repo._build_event_row(event, None)

    raw = row[5]
    assert "row-fallback" in raw


# ---------------------------------------------------------------------------
# EventRepository — mark_processed_force (upsert for resync)
# ---------------------------------------------------------------------------


def test_mark_processed_force_inserts_new_event(db):
    """mark_processed_force should persist a new event."""
    event = {"id": "new-evt", "timestamp": "2026-07-01T10:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed_force(event, wallet_record_id="wid-1")
        repo.commit()
        assert repo.is_processed("new-evt")
        assert repo.get_wallet_record_id(event) == "wid-1"


def test_mark_processed_force_updates_existing_event(db):
    """mark_processed_force should update wallet_record_id for an already-processed event."""
    event = {"id": "existing-evt", "timestamp": "2026-07-01T10:00:00Z"}
    with EventRepository(db) as repo:
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


def test_mark_processed_force_replaces_excluded_with_synced(db):
    """mark_processed_force replaces NULL wallet_record_id (excluded) with a real ID."""
    event = {"id": "excluded-evt", "timestamp": "2026-07-01T10:00:00Z"}
    with EventRepository(db) as repo:
        repo.mark_processed(event, wallet_record_id=None)
        repo.commit()
        assert repo.get_wallet_record_id(event) is None

        repo.mark_processed_force(event, wallet_record_id="wid-abc")
        repo.commit()
        assert repo.get_wallet_record_id(event) == "wid-abc"


def test_mark_processed_force_falls_back_to_str_on_type_error(db, monkeypatch):
    """When json.dumps raises TypeError, mark_processed_force stores str(event)."""
    from unittest.mock import patch

    event = {"id": "force-fallback", "timestamp": "2026-07-01T00:00:00Z"}
    with (
        patch("app.persistence.json.dumps", side_effect=TypeError("not serialisable")),
        EventRepository(db) as repo,
    ):
        repo.mark_processed_force(event, wallet_record_id="wid-x")
        repo.commit()
        raw = repo.get_raw("force-fallback")

    assert "force-fallback" in raw


# ---------------------------------------------------------------------------
# EventRepository — auth_state table
# ---------------------------------------------------------------------------


def test_auth_state_table_created(db):
    """auth_state table must exist after init_db."""
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_state'"
    ).fetchall()
    conn.close()
    assert rows, "auth_state table was not created"


def test_set_and_get_auth_state_ok(db):
    with EventRepository(db) as repo:
        repo.set_auth_state("david", "ok")
        assert repo.get_auth_state("david") == "ok"


def test_set_and_get_auth_state_failed(db):
    with EventRepository(db) as repo:
        repo.set_auth_state("david", "failed")
        assert repo.get_auth_state("david") == "failed"


def test_set_and_get_auth_state_expired(db):
    with EventRepository(db) as repo:
        repo.set_auth_state("david", "expired")
        assert repo.get_auth_state("david") == "expired"


def test_get_auth_state_returns_none_when_absent(db):
    with EventRepository(db) as repo:
        assert repo.get_auth_state("nonexistent") is None


def test_set_auth_state_overwrites_existing(db):
    with EventRepository(db) as repo:
        repo.set_auth_state("david", "ok")
        repo.set_auth_state("david", "failed")
        assert repo.get_auth_state("david") == "failed"


def test_set_auth_state_persists_updated_at(db):
    """set_auth_state must store a non-empty updated_at timestamp."""
    with EventRepository(db) as repo:
        repo.set_auth_state("david", "ok")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT updated_at FROM auth_state WHERE instance = 'david'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0]  # non-empty


# ---------------------------------------------------------------------------
# EventRepository — sync_runs table
# ---------------------------------------------------------------------------


def test_sync_runs_table_created(db):
    """sync_runs table must exist after init_db."""
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_runs'"
    ).fetchall()
    conn.close()
    assert rows, "sync_runs table was not created"


def test_set_and_get_sync_run_success(db):
    with EventRepository(db) as repo:
        repo.set_sync_run("david", status="success", saved=3, failed=0, excluded=1)
        run = repo.get_sync_run("david")
    assert run is not None
    assert run["status"] == "success"
    assert run["saved"] == 3
    assert run["failed"] == 0
    assert run["excluded"] == 1
    assert run["ran_at"]


def test_set_and_get_sync_run_failed(db):
    with EventRepository(db) as repo:
        repo.set_sync_run("david", status="failed", saved=0, failed=2, excluded=0)
        run = repo.get_sync_run("david")
    assert run is not None
    assert run["status"] == "failed"
    assert run["saved"] == 0
    assert run["failed"] == 2


def test_get_sync_run_returns_none_when_absent(db):
    with EventRepository(db) as repo:
        assert repo.get_sync_run("nonexistent") is None


def test_set_sync_run_overwrites_existing(db):
    with EventRepository(db) as repo:
        repo.set_sync_run("david", status="success", saved=1, failed=0, excluded=0)
        repo.set_sync_run("david", status="failed", saved=0, failed=1, excluded=0)
        run = repo.get_sync_run("david")
    assert run["status"] == "failed"


def test_set_sync_run_stores_ran_at_timestamp(db):
    """set_sync_run must store a non-empty UTC ran_at timestamp."""
    with EventRepository(db) as repo:
        repo.set_sync_run("david", status="success", saved=1, failed=0, excluded=0)

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT ran_at FROM sync_runs WHERE instance = 'david'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0]


# ---------------------------------------------------------------------------
# EventRepository — instance scoping (shared DB, issue #173)
# ---------------------------------------------------------------------------


def test_repo_instance_scopes_filter_unprocessed(tmp_path):
    """Two instances sharing a DB must not see each other's processed events."""
    db_path = tmp_path / "shared.db"
    init_db(db_path)
    event = {"id": "shared-evt", "timestamp": "2024-01-01T00:00:00Z"}

    with EventRepository(db_path, instance="alice") as repo_a:
        repo_a.mark_processed(event)
        repo_a.commit()

    # bob has not processed the same event — must appear as unprocessed for him
    with EventRepository(db_path, instance="bob") as repo_b:
        unprocessed = repo_b.filter_unprocessed([event])
    assert len(unprocessed) == 1


def test_repo_instance_scopes_is_processed(tmp_path):
    """is_processed must return True only for the owning instance."""
    db_path = tmp_path / "shared.db"
    init_db(db_path)
    event = {"id": "scope-evt", "timestamp": "2024-01-01T00:00:00Z"}

    with EventRepository(db_path, instance="alice") as repo_a:
        repo_a.mark_processed(event)
        repo_a.commit()
        assert repo_a.is_processed("scope-evt")

    with EventRepository(db_path, instance="bob") as repo_b:
        assert not repo_b.is_processed("scope-evt")


def test_repo_same_event_id_two_instances(tmp_path):
    """Same event_id must be storeable independently for two instances."""
    db_path = tmp_path / "shared.db"
    init_db(db_path)
    event = {"id": "dup-id", "timestamp": "2024-01-01T00:00:00Z"}

    with EventRepository(db_path, instance="alice") as repo_a:
        repo_a.mark_processed(event, wallet_record_id="wid-a")
        repo_a.commit()

    with EventRepository(db_path, instance="bob") as repo_b:
        repo_b.mark_processed(event, wallet_record_id="wid-b")
        repo_b.commit()

    with EventRepository(db_path, instance="alice") as repo_a:
        assert repo_a.get_wallet_record_id(event) == "wid-a"

    with EventRepository(db_path, instance="bob") as repo_b:
        assert repo_b.get_wallet_record_id(event) == "wid-b"


def test_repo_instance_scopes_count_processed(tmp_path):
    """count_processed must count only rows for the repo's own instance."""
    db_path = tmp_path / "shared.db"
    init_db(db_path)
    e1 = {"id": "e1", "timestamp": "2024-01-01T00:00:00Z"}
    e2 = {"id": "e2", "timestamp": "2024-01-01T00:00:00Z"}

    with EventRepository(db_path, instance="alice") as repo_a:
        repo_a.mark_processed(e1)
        repo_a.mark_processed(e2)
        repo_a.commit()
        assert repo_a.count_processed() == 2

    with EventRepository(db_path, instance="bob") as repo_b:
        assert repo_b.count_processed() == 0


def test_repo_instance_scopes_purge(tmp_path, monkeypatch):
    """purge_old_records must only delete records belonging to the repo's instance."""
    db_path = tmp_path / "shared.db"
    init_db(db_path)
    old = datetime.now(UTC) - timedelta(days=90)
    recent = datetime.now(UTC)

    with EventRepository(db_path, instance="alice") as repo_a:
        _mark_processed_at(repo_a, "alice-old", old, monkeypatch)
        _mark_processed_at(repo_a, "alice-recent", recent, monkeypatch)

    with EventRepository(db_path, instance="bob") as repo_b:
        _mark_processed_at(repo_b, "alice-old", old, monkeypatch)  # same id, bob's
        repo_b.purge_old_records(ttl_days=60)
        # bob's old event is deleted
        assert not repo_b.is_processed("alice-old")

    # alice's records must be untouched
    with EventRepository(db_path, instance="alice") as repo_a:
        assert repo_a.is_processed("alice-old")
        assert repo_a.is_processed("alice-recent")


def test_repo_instance_scopes_get_raw(tmp_path):
    """get_raw must return only the row for the repo's instance."""
    db_path = tmp_path / "shared.db"
    init_db(db_path)
    event = {"id": "raw-evt", "timestamp": "2024-01-01T00:00:00Z", "data": "alice"}

    with EventRepository(db_path, instance="alice") as repo_a:
        repo_a.mark_processed(event)
        repo_a.commit()

    with EventRepository(db_path, instance="bob") as repo_b:
        assert repo_b.get_raw("raw-evt") is None


def test_repo_instance_scopes_mark_processed_force(tmp_path):
    """mark_processed_force must upsert within the same instance scope only."""
    db_path = tmp_path / "shared.db"
    init_db(db_path)
    event = {"id": "force-evt", "timestamp": "2024-01-01T00:00:00Z"}

    with EventRepository(db_path, instance="alice") as repo_a:
        repo_a.mark_processed(event, wallet_record_id="wid-orig")
        repo_a.commit()

    with EventRepository(db_path, instance="alice") as repo_a:
        repo_a.mark_processed_force(event, wallet_record_id="wid-updated")
        repo_a.commit()
        assert repo_a.get_wallet_record_id(event) == "wid-updated"
        assert repo_a.count_processed() == 1  # no duplicate row
