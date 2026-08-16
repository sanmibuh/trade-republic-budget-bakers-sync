from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.config import LABELABLE_EVENT_TYPES, BackupConfig, Config
from app.persistence import (
    EventRepository,
    dedup_event_id,
    event_id,
)
from app.tr_mapper import filter_by_lookback

# ---------------------------------------------------------------------------
# Config.from_env / BackupConfig.from_env — env var parsing (public API)
# ---------------------------------------------------------------------------


def _set_sync_env(monkeypatch, **overrides: str) -> None:
    """Set the minimum env vars required for Config.from_env() to succeed."""
    defaults = {
        "PHONE_NUMBER": "+49123456789",
        "PIN": "1234",
        "WALLET_API_KEY": "key",
        "WALLET_CASH_ACCOUNT_ID": "cash-id",
        "WALLET_PORTFOLIO_ACCOUNT_ID": "portfolio-id",
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)


# required env var — present


def test_required_env_present(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "my-key")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)
    cfg = BackupConfig.from_env()
    assert cfg.wallet_api_key == "my-key"


# required env var — missing


def test_required_env_missing(monkeypatch):
    monkeypatch.delenv("WALLET_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)
    with pytest.raises(ValueError, match="WALLET_API_KEY"):
        BackupConfig.from_env()


# required env var — blank


def test_required_env_blank(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "   ")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)
    with pytest.raises(ValueError, match="WALLET_API_KEY"):
        BackupConfig.from_env()


# positive-int env var — default used when var is absent


def test_positive_int_env_uses_default(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.delenv("LOOKBACK_DAYS", raising=False)
    assert Config.from_env().lookback_days == 7


# positive-int env var — explicit value is read


def test_positive_int_env_reads_env(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LOOKBACK_DAYS", "14")
    assert Config.from_env().lookback_days == 14


# positive-int env var — non-integer value is rejected


def test_positive_int_env_rejects_non_integer(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LOOKBACK_DAYS", "abc")
    with pytest.raises(ValueError, match="integer"):
        Config.from_env()


# positive-int env var — zero is rejected


def test_positive_int_env_rejects_zero(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LOOKBACK_DAYS", "0")
    with pytest.raises(ValueError, match="positive"):
        Config.from_env()


# positive-int env var — negative is rejected


def test_positive_int_env_rejects_negative(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LOOKBACK_DAYS", "-5")
    with pytest.raises(ValueError, match="positive"):
        Config.from_env()


# ---------------------------------------------------------------------------
# DEDUP_TTL_DAYS
# ---------------------------------------------------------------------------


def test_dedup_ttl_days_default(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.delenv("DEDUP_TTL_DAYS", raising=False)
    assert Config.from_env().dedup_ttl_days == 60


def test_dedup_ttl_days_explicit(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("DEDUP_TTL_DAYS", "90")
    assert Config.from_env().dedup_ttl_days == 90


def test_dedup_ttl_days_rejects_non_integer(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("DEDUP_TTL_DAYS", "abc")
    with pytest.raises(ValueError, match="integer"):
        Config.from_env()


def test_dedup_ttl_days_rejects_zero(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("DEDUP_TTL_DAYS", "0")
    with pytest.raises(ValueError, match="positive"):
        Config.from_env()


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
# filter_by_lookback
# ---------------------------------------------------------------------------


def _make_event(timestamp: str, **kwargs) -> dict:
    return {"timestamp": timestamp, **kwargs}


def test_filter_by_lookback_keeps_recent():
    since = datetime(2024, 1, 10, tzinfo=UTC)
    assert len(filter_by_lookback([_make_event("2024-01-11T00:00:00Z")], since)) == 1


def test_filter_by_lookback_removes_old():
    since = datetime(2024, 1, 10, tzinfo=UTC)
    assert filter_by_lookback([_make_event("2024-01-09T00:00:00Z")], since) == []


def test_filter_by_lookback_keeps_event_on_boundary():
    since = datetime(2024, 1, 10, 0, 0, 0, tzinfo=UTC)
    assert len(filter_by_lookback([_make_event("2024-01-10T00:00:00Z")], since)) == 1


def test_filter_by_lookback_keeps_event_with_unparseable_timestamp():
    since = datetime(2024, 1, 10, tzinfo=UTC)
    assert len(filter_by_lookback([{"timestamp": "not-a-date"}], since)) == 1


def test_filter_by_lookback_keeps_event_without_timestamp():
    since = datetime(2024, 1, 10, tzinfo=UTC)
    assert len(filter_by_lookback([{"amount": "5"}], since)) == 1


def test_filter_by_lookback_naive_timestamp_treated_as_utc():
    since = datetime(2024, 1, 10, tzinfo=UTC)
    assert len(filter_by_lookback([_make_event("2024-01-11T00:00:00")], since)) == 1


def test_filter_by_lookback_multiple_events():
    since = datetime(2024, 1, 10, tzinfo=UTC)
    events = [
        _make_event("2024-01-09T00:00:00Z"),
        _make_event("2024-01-11T00:00:00Z"),
        _make_event("2024-01-12T00:00:00Z"),
    ]
    assert len(filter_by_lookback(events, since)) == 2


def test_filter_by_lookback_with_until_excludes_events_on_or_after():
    """Events at or after `until` must be excluded when until is provided."""
    since = datetime(2024, 1, 10, tzinfo=UTC)
    until = datetime(2024, 1, 11, tzinfo=UTC)
    events = [
        _make_event("2024-01-09T00:00:00Z"),  # before since → excluded
        _make_event("2024-01-10T06:00:00Z"),  # within window → kept
        _make_event("2024-01-11T00:00:00Z"),  # exactly at until → excluded
        _make_event("2024-01-12T00:00:00Z"),  # after until → excluded
    ]
    result = filter_by_lookback(events, since, until=until)
    assert len(result) == 1
    assert result[0]["timestamp"] == "2024-01-10T06:00:00Z"


def test_filter_by_lookback_until_none_behaves_as_before():
    """until=None must produce the same result as not passing until."""
    since = datetime(2024, 1, 10, tzinfo=UTC)
    events = [
        _make_event("2024-01-10T00:00:00Z"),
        _make_event("2024-01-11T00:00:00Z"),
    ]
    assert filter_by_lookback(events, since, until=None) == filter_by_lookback(
        events, since
    )


# ---------------------------------------------------------------------------
# SyncRunner.build_batch — unknown event type triggers notifier
# ---------------------------------------------------------------------------


def test_build_batch_notifies_on_unknown_event_type(tmp_path):
    from app.config import Config
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {
        "eventType": "TOTALLY_NEW_TYPE",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "5.00",
    }
    with EventRepository(tmp_path / "test.db") as repo:
        runner.build_batch([event], repo)

    notifier.unknown_event_type.assert_called_once_with("TOTALLY_NEW_TYPE")


def test_build_batch_no_notification_for_known_event_type(tmp_path):
    from app.config import Config
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {
        "eventType": "BUY_ORDER",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "100.00",
    }
    with EventRepository(tmp_path / "test.db") as repo:
        runner.build_batch([event], repo)

    notifier.unknown_event_type.assert_not_called()


# ---------------------------------------------------------------------------
# SyncRunner.build_batch — zero-amount events are excluded
# ---------------------------------------------------------------------------


def test_build_batch_excludes_zero_amount_event(tmp_path):
    from app.config import Config
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    # A zero-amount event produces no records → should be excluded
    event = {
        "eventType": "SAVINGS_PLAN_EXECUTED",
        "id": "ev-zero",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "0.00",
    }
    with EventRepository(tmp_path / "test.db") as repo:
        batch = runner.build_batch([event], repo)

    assert batch.excluded_count == 1
    assert batch.records == []


# ---------------------------------------------------------------------------
# SyncRunner.fetch_events — error branches
# ---------------------------------------------------------------------------


def test_fetch_events_login_failed_exits():
    from unittest.mock import patch

    from app.sync_runner import SyncRunner
    from app.tr_client import LoginFailedError

    cfg = MagicMock()
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime.now(UTC)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = LoginFailedError("bad pin")
        with pytest.raises(SystemExit) as exc_info:
            runner.fetch_events(since)

    assert exc_info.value.code == 1
    notifier.login_failed.assert_called_once()


def test_fetch_events_session_expired_exits():
    from unittest.mock import patch

    from app.sync_runner import SyncRunner
    from app.tr_client import SessionExpiredError

    cfg = MagicMock()
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime.now(UTC)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = SessionExpiredError("needs bootstrap")
        with pytest.raises(SystemExit) as exc_info:
            runner.fetch_events(since)

    assert exc_info.value.code == 1
    notifier.authentication_required.assert_called_once()
    notifier.login_failed.assert_not_called()


def test_fetch_events_http_401_exits():
    from unittest.mock import patch

    from requests import HTTPError

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime.now(UTC)

    err = HTTPError()
    err.response = MagicMock()
    err.response.status_code = 401

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = err
        with pytest.raises(SystemExit) as exc_info:
            runner.fetch_events(since)

    assert exc_info.value.code == 1
    notifier.authentication_required.assert_called_once()


def test_fetch_events_http_non_401_reraises():
    from unittest.mock import patch

    from requests import HTTPError

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime.now(UTC)

    err = HTTPError()
    err.response = MagicMock()
    err.response.status_code = 500

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = err
        with pytest.raises(HTTPError):
            runner.fetch_events(since)

    notifier.error.assert_called_once_with(err)


def test_fetch_events_unexpected_exception_reraises():
    from unittest.mock import patch

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime.now(UTC)

    boom = RuntimeError("unexpected")
    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = boom
        with pytest.raises(RuntimeError):
            runner.fetch_events(since)

    notifier.error.assert_called_once_with(boom)


def test_handle_http_error_401_raises_system_exit(tmp_path):
    """_handle_http_error must raise SystemExit(1) and notify for 401 responses."""
    from requests import HTTPError

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    err = HTTPError()
    err.response = MagicMock()
    err.response.status_code = 401

    with pytest.raises(SystemExit) as exc_info:
        runner._handle_http_error(err)

    assert exc_info.value.code == 1
    notifier.authentication_required.assert_called_once()


def test_handle_http_error_non_401_notifies_error(tmp_path):
    """_handle_http_error must call notifier.error and return for non-401 responses."""
    from requests import HTTPError

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    err = HTTPError()
    err.response = MagicMock()
    err.response.status_code = 500

    runner._handle_http_error(err)

    notifier.error.assert_called_once_with(err)
    notifier.authentication_required.assert_not_called()


def test_handle_http_error_none_response_notifies_error(tmp_path):
    """_handle_http_error must handle exc.response being None without crashing."""
    from requests import HTTPError

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    err = HTTPError()
    err.response = None

    runner._handle_http_error(err)

    notifier.error.assert_called_once_with(err)


def test_fetch_events_success_returns_events():
    from unittest.mock import patch

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime.now(UTC)
    fake_events = [{"id": "e1"}, {"id": "e2"}]

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.fetch_timeline_events.return_value = fake_events
        result = runner.fetch_events(since)

    assert result == fake_events


# ---------------------------------------------------------------------------
# SyncRunner.process_results
# ---------------------------------------------------------------------------


def test_process_results_marks_successful_events(tmp_path):
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0}]  # no "error" key → success
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = runner.process_results(results, [event], event_record_indices, repo)
        unprocessed = repo.filter_unprocessed([event])

    assert counts.synced == 1
    assert counts.failed == 0
    assert unprocessed == []  # was marked processed


def test_process_results_counts_failures(tmp_path):
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0, "error": "bad record"}]
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = runner.process_results(results, [event], event_record_indices, repo)
        unprocessed = repo.filter_unprocessed([event])

    assert counts.synced == 0
    assert counts.failed == 1
    assert unprocessed == [event]  # NOT marked processed


def test_process_results_skips_events_with_no_records(tmp_path):
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    results = []
    event_record_indices = [[]]  # event produced no records

    with EventRepository(tmp_path / "test.db") as repo:
        counts = runner.process_results(results, [event], event_record_indices, repo)

    assert counts.synced == 0
    assert counts.failed == 0


def test_process_results_preserves_excluded_count(tmp_path):
    """process_results must propagate excluded_count into the returned _SyncCounts.

    Regression: previously the caller had to re-assign counts.excluded after
    calling process_results, which was fragile and easy to delete silently.
    """
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0}]
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = runner.process_results(
            results, [event], event_record_indices, repo, excluded_count=3
        )

    assert counts.excluded == 3


# ---------------------------------------------------------------------------
# run() — zero-amount events are committed even when no records are posted
# ---------------------------------------------------------------------------


def test_run_excluded_events_not_reprocessed_on_next_run(tmp_path):
    """Regression: repo.commit() must be called even when all events are excluded.

    Before the fix, mark_processed() writes for zero-amount events were left
    uncommitted when batch.records was empty, causing them to be reprocessed
    on the next run.
    """
    from unittest.mock import patch

    from app.main import run

    excluded_event = {
        "id": "ev-zero-persist",
        "timestamp": "2024-01-01T00:00:00Z",
        "eventType": "SAVINGS_PLAN_EXECUTED",
        "amount": "0.00",
    }

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[excluded_event]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = [excluded_event]

        # Simulate build_batch marking the event as processed in a real repo
        # and returning an empty batch (all excluded)
        def fake_build_batch(new_events, repo, *, wallet_client=None):
            from app.sync_runner import _Batch

            for ev in new_events:
                repo.mark_processed(ev)
            return _Batch(records=[], event_record_indices=[[]], excluded_count=1)

        runner.build_batch.side_effect = fake_build_batch

        run()

    # After run(), the event must be persisted (committed) in the DB
    with EventRepository(tmp_path / "sync.db") as repo:
        assert repo.is_processed("ev-zero-persist"), (
            "Excluded event was not committed — it will be reprocessed on the next run"
        )


# ---------------------------------------------------------------------------
# run() — orchestrator
# ---------------------------------------------------------------------------


def test_run_returns_zero_on_success(tmp_path):
    from unittest.mock import patch

    from app.main import run

    fake_events = [
        {
            "id": "e1",
            "timestamp": "2024-01-01T00:00:00Z",
            "amount": "10.00",
            "eventType": "PAYMENT",
        }
    ]

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = fake_events

        mock_counts = MagicMock()
        mock_counts.synced = 1
        mock_counts.failed = 0
        mock_counts.excluded = 0
        runner.process_results.return_value = mock_counts

        batch = MagicMock()
        batch.records = [{"amount": 10}]
        batch.excluded_count = 0
        batch.event_record_indices = [[0]]
        runner.build_batch.return_value = batch

        with (
            patch("app.main.filter_by_lookback", return_value=fake_events),
            patch("app.main.WalletClient") as mock_wallet,
        ):
            mock_wallet.return_value.post_records.return_value = [{}]
            result = run()

    assert result == 0


def test_run_returns_zero_when_no_new_events(tmp_path):
    from unittest.mock import patch

    from app.main import run

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []

        batch = MagicMock()
        batch.records = []
        batch.excluded_count = 0
        batch.event_record_indices = []
        runner.build_batch.return_value = batch

        result = run()

    assert result == 0


def test_run_authentication_error_exits(tmp_path):
    """except AuthenticationError branch in fetch_events — simulate via patching."""
    from unittest.mock import patch

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime.now(UTC)

    import app.main as main_module

    AuthErr = main_module.AuthenticationError

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = AuthErr("auth required")
        with pytest.raises(SystemExit) as exc_info:
            runner.fetch_events(since)

    assert exc_info.value.code == 1
    notifier.authentication_required.assert_called_once()


def test_run_wallet_error_notifies_and_reraises(tmp_path):
    """except Exception in run() when wallet post fails."""
    from unittest.mock import patch

    from app.main import run

    boom = RuntimeError("wallet down")

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []
        runner.build_batch.side_effect = boom  # simulate error during batch build

        notifier_instance = mock_notifier_cls.return_value

        with pytest.raises(RuntimeError):
            run()

    notifier_instance.error.assert_called_once_with(boom)


def test_run_logs_warning_when_sync_complete_not_sent(tmp_path):
    """sync_complete returns False → log.warning branch."""
    from unittest.mock import patch

    from app.main import run

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []

        batch = MagicMock()
        batch.records = []
        batch.excluded_count = 0
        batch.event_record_indices = []
        runner.build_batch.return_value = batch

        notifier_instance = mock_notifier_cls.return_value
        notifier_instance.sync_complete.return_value = False  # simulate not sent

        result = run()

    assert result == 0
    notifier_instance.sync_complete.assert_called_once()


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
# SyncRunner.process_results — passes wallet_record_id to mark_processed
# ---------------------------------------------------------------------------


def test_process_results_passes_wallet_record_id(tmp_path):
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev-wrid", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0, "id": "wallet-record-1"}]
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        runner.process_results(results, [event], event_record_indices, repo)
        wallet_id = repo.get_wallet_record_id(event)

    assert wallet_id == "wallet-record-1"


def test_process_results_stores_joined_ids_for_multi_record_event(tmp_path):
    """An event that maps to 2 Wallet records stores both IDs joined by comma."""
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev-multi", "timestamp": "2024-01-01T00:00:00Z"}
    results = [
        {"inputIndex": 0, "id": "wid-1"},
        {"inputIndex": 1, "id": "wid-2"},
    ]
    event_record_indices = [[0, 1]]

    with EventRepository(tmp_path / "test.db") as repo:
        runner.process_results(results, [event], event_record_indices, repo)
        wallet_id = repo.get_wallet_record_id(event)

    assert wallet_id == "wid-1,wid-2"


def test_process_results_no_wallet_id_when_result_has_no_id(tmp_path):
    """If the API result has no 'id' field, wallet_record_id is stored as None."""
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev-noid", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0}]  # no "id" field
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        runner.process_results(results, [event], event_record_indices, repo)
        wallet_id = repo.get_wallet_record_id(event)

    assert wallet_id is None


def test_process_results_missing_index_counts_as_failure(tmp_path):
    """An event whose record index is absent from API results is not marked processed."""
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev-missing", "timestamp": "2024-01-01T00:00:00Z"}
    results = []  # API returned no results at all
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = runner.process_results(results, [event], event_record_indices, repo)
        unprocessed = repo.filter_unprocessed([event])

    assert counts.failed == 1
    assert unprocessed == [event]


def test_process_results_missing_index_notifies(tmp_path):
    """A missing result index triggers a warning via notifier."""
    from app.notifier import Notifier
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.instance = "david"
    cfg.data_dir = tmp_path
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {"id": "ev-notify", "timestamp": "2024-01-01T00:00:00Z"}
    results = []
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        runner.process_results(results, [event], event_record_indices, repo)

    notifier.missing_api_result.assert_called_once()


# ---------------------------------------------------------------------------
# Config.from_env().label_ids — LABEL_* env var parsing (public API)
# ---------------------------------------------------------------------------


def test_read_label_ids_returns_empty_when_no_env(monkeypatch):
    _set_sync_env(monkeypatch)
    for event_type in LABELABLE_EVENT_TYPES:
        monkeypatch.delenv(f"LABEL_{event_type}", raising=False)
    assert Config.from_env().label_ids == {}


def test_read_label_ids_picks_up_set_vars(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LABEL_BANK_TRANSACTION_INCOMING", "label-abc-123")
    monkeypatch.setenv("LABEL_BUY_ORDER", "label-xyz-456")

    label_ids = Config.from_env().label_ids
    assert label_ids["BANK_TRANSACTION_INCOMING"] == "label-abc-123"
    assert label_ids["BUY_ORDER"] == "label-xyz-456"


def test_read_label_ids_ignores_blank_values(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LABEL_BANK_TRANSACTION_INCOMING", "   ")

    assert "BANK_TRANSACTION_INCOMING" not in Config.from_env().label_ids


def test_sync_complete_receives_excluded_count_even_when_post_fails(tmp_path):
    """excluded_count must be reported in sync_complete even if post_records raises.

    Regression test for bug where counts.excluded was assigned *after*
    _process_results, so a failure there would report excluded=0 in the
    finally block.
    """
    from unittest.mock import patch

    from app.main import run

    fake_events = [
        {
            "id": "e1",
            "timestamp": "2024-01-01T00:00:00Z",
            "amount": "10.00",
            "eventType": "PAYMENT",
        }
    ]

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=fake_events),
        patch("app.main.WalletClient") as mock_wallet,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = fake_events

        batch = MagicMock()
        batch.records = [{"amount": 10}]
        batch.excluded_count = 3  # excluded events present
        batch.event_record_indices = [[0]]
        runner.build_batch.return_value = batch

        mock_wallet.return_value.post_records.side_effect = RuntimeError("wallet down")

        notifier_instance = mock_notifier_cls.return_value

        with pytest.raises(RuntimeError):
            run()

    # sync_complete must be called (via finally) with the correct excluded count
    notifier_instance.sync_complete.assert_called_once()
    _, kwargs = notifier_instance.sync_complete.call_args
    assert kwargs["excluded"] == 3


# ---------------------------------------------------------------------------
# run_login — on-demand re-authentication
# ---------------------------------------------------------------------------


def test_run_login_connects_and_returns_zero(tmp_path):
    from unittest.mock import patch

    from app.main import run_login

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.http_client.configure"),
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.sync_runner.TRClient") as MockTR,
        patch("app.sync_runner._build_code_provider", return_value=MagicMock()),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        mock_cfg_cls.return_value = cfg
        notifier_instance = mock_notifier_cls.return_value

        result = run_login()

    assert result == 0
    MockTR.return_value.connect.assert_called_once()
    kwargs = MockTR.return_value.connect.call_args.kwargs
    assert kwargs["on_login_success"] == notifier_instance.login_success


def test_run_login_session_expired_notifies_and_exits(tmp_path):
    from unittest.mock import patch

    from app.main import run_login
    from app.tr_client import SessionExpiredError

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.http_client.configure"),
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.sync_runner.TRClient") as MockTR,
        patch("app.sync_runner._build_code_provider", return_value=None),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        mock_cfg_cls.return_value = cfg
        MockTR.return_value.connect.side_effect = SessionExpiredError("no provider")
        notifier_instance = mock_notifier_cls.return_value

        result = run_login()

    assert result == 1
    notifier_instance.authentication_required.assert_called_once()


def test_run_login_login_failed_notifies_and_exits(tmp_path):
    from unittest.mock import patch

    from app.main import run_login
    from app.tr_client import LoginFailedError

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.http_client.configure"),
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.sync_runner.TRClient") as MockTR,
        patch("app.sync_runner._build_code_provider", return_value=MagicMock()),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        mock_cfg_cls.return_value = cfg
        MockTR.return_value.connect.side_effect = LoginFailedError("bad code")
        notifier_instance = mock_notifier_cls.return_value

        result = run_login()

    assert result == 1
    notifier_instance.login_failed.assert_called_once()


def test_run_login_unexpected_error_notifies_and_exits(tmp_path):
    from unittest.mock import patch

    from app.main import run_login

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.http_client.configure"),
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.sync_runner.TRClient") as MockTR,
        patch("app.sync_runner._build_code_provider", return_value=MagicMock()),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        mock_cfg_cls.return_value = cfg
        MockTR.return_value.connect.side_effect = RuntimeError("boom")
        notifier_instance = mock_notifier_cls.return_value

        result = run_login()

    assert result == 1
    notifier_instance.error.assert_called_once()


# ---------------------------------------------------------------------------
# _prepare — shared bootstrap
# ---------------------------------------------------------------------------


def test_prepare_configures_environment_and_returns_notifier(tmp_path):
    from unittest.mock import patch

    from app.main import _prepare

    cfg = MagicMock()
    cfg.data_dir = tmp_path / "data"
    cfg.allow_insecure_ssl = True
    cfg.telegram_bot_token = "tok"
    cfg.telegram_chat_id = "chat"
    cfg.owner_name = "David"

    with (
        patch("app.main.http_client.configure") as mock_configure,
        patch("app.main.setup_logging") as mock_setup,
        patch("app.main.Notifier") as mock_notifier_cls,
    ):
        notifier = _prepare(cfg)

    assert cfg.data_dir.is_dir()
    mock_configure.assert_called_once_with(allow_insecure_ssl=True)
    mock_setup.assert_called_once_with(cfg.data_dir)
    mock_notifier_cls.assert_called_once_with("tok", "chat", "David")
    assert notifier is mock_notifier_cls.return_value


# ---------------------------------------------------------------------------
# SyncRunner.connect — shared TR client creation + login
# ---------------------------------------------------------------------------


def test_connect_builds_client_and_calls_connect_with_code_provider():
    from unittest.mock import patch

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.phone_number = "+49123"
    cfg.pin = "1234"
    cfg.data_dir = "/data"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    provider = MagicMock()

    with (
        patch("app.sync_runner.TRClient") as MockTR,
        patch(
            "app.sync_runner._build_code_provider", return_value=provider
        ) as mock_build,
    ):
        result = runner.connect()

    MockTR.assert_called_once_with("+49123", "1234", "/data")
    mock_build.assert_called_once_with(cfg, notifier)
    kwargs = MockTR.return_value.connect.call_args.kwargs
    assert kwargs["on_login_required"] == notifier.login_required
    assert kwargs["on_login_success"] == notifier.login_success
    assert kwargs["code_provider"] is provider
    assert result is MockTR.return_value


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
# SyncRunner.resync_day — resync orchestration for a single day
# ---------------------------------------------------------------------------


def _make_runner_with_mocks():
    """Return a (runner, cfg, notifier) tuple with MagicMock deps."""
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.wallet_cash_account_id = "cash-id"
    cfg.wallet_portfolio_account_id = "portfolio-id"
    cfg.label_ids = {}
    notifier = MagicMock()
    return SyncRunner(cfg, notifier), cfg, notifier


def test_resync_day_fetches_events_for_exact_date():
    """resync_day must call fetch_events with since=date 00:00 UTC."""
    from datetime import date
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()
    repo = MagicMock()
    wallet_client = MagicMock()

    with patch.object(runner, "fetch_events", return_value=[]) as mock_fetch:
        runner.resync_day("2026-07-15", repo, wallet_client)

    since = mock_fetch.call_args[0][0]
    assert since.date() == date(2026, 7, 15)
    assert since.hour == 0 and since.minute == 0


def test_resync_day_skips_dedup_filter(tmp_path):
    """resync_day must NOT call filter_unprocessed — all events are re-processed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "already-done", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        # Pre-mark the event as processed
        repo.mark_processed(event, wallet_record_id="existing-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.return_value = {"id": "new-wid", "success": True}

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch(
                "app.sync_runner.filter_by_lookback",
                return_value=[event],
            ),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[{"accountId": "cash-id"}],
            ),
        ):
            runner.resync_day("2026-07-15", repo, wallet_client)

        # Event must have been re-processed (wallet_record_id updated)
        assert repo.get_wallet_record_id(event) == "new-wid"


def test_resync_day_updates_existing_records_via_put(tmp_path):
    """Events with an existing wallet_record_id should be updated via PUT, not POST."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-update", "timestamp": "2026-07-15T09:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wallet-id")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.return_value = {"id": "old-wallet-id", "success": True}

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[{"accountId": "cash-id"}],
            ),
        ):
            runner.resync_day("2026-07-15", repo, wallet_client)

        # PUT must have been called with the existing wallet record ID
        wallet_client.put_record.assert_called_once_with(
            "old-wallet-id", {"accountId": "cash-id"}
        )
        # POST must NOT have been called for this event
        wallet_client.post_records.assert_not_called()


def test_resync_day_inserts_new_events_via_post(tmp_path):
    """Events without a wallet_record_id (never synced) should be POSTed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-new", "timestamp": "2026-07-15T11:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "fresh-wid", "success": True}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[{"accountId": "cash-id"}],
            ),
        ):
            runner.resync_day("2026-07-15", repo, wallet_client)

        wallet_client.post_records.assert_called_once()
        assert repo.get_wallet_record_id(event) == "fresh-wid"


def test_resync_day_multi_record_event_put_called_for_each_id(tmp_path):
    """For events with multiple comma-separated wallet IDs, PUT is called for each."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-multi", "timestamp": "2026-07-15T08:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-a,wid-b")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.return_value = {"id": "wid-a", "success": True}

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[
                    {"accountId": "cash-id"},
                    {"accountId": "portfolio-id"},
                ],
            ),
        ):
            runner.resync_day("2026-07-15", repo, wallet_client)

        # PUT called for each sub-record
        assert wallet_client.put_record.call_count == 2
        ids_called = {c.args[0] for c in wallet_client.put_record.call_args_list}
        assert ids_called == {"wid-a", "wid-b"}


def test_resync_day_excluded_zero_amount_events_marked_force(tmp_path):
    """Zero-amount events (empty records list) must be force-marked processed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-zero", "timestamp": "2026-07-15T07:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch("app.sync_runner.build_records_for_event", return_value=[]),
        ):
            runner.resync_day("2026-07-15", repo, wallet_client)

        wallet_client.post_records.assert_not_called()
        wallet_client.put_record.assert_not_called()
        assert repo.is_processed("ev-zero")


def test_resync_day_returns_counts(tmp_path):
    """resync_day must return a _SyncCounts-like object with synced/excluded/failed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event_synced = {"id": "ev-s", "timestamp": "2026-07-15T06:00:00Z"}
    event_excluded = {"id": "ev-x", "timestamp": "2026-07-15T07:00:00Z"}
    events = [event_synced, event_excluded]

    def _fake_build_records(event, **_kwargs):
        if event["id"] == "ev-s":
            return [{"accountId": "cash-id"}]
        return []

    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "wid-s", "success": True}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=events),
            patch("app.sync_runner.filter_by_lookback", return_value=events),
            patch(
                "app.sync_runner.build_records_for_event",
                side_effect=_fake_build_records,
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

    assert counts.synced == 1
    assert counts.excluded == 1
    assert counts.failed == 0


# ---------------------------------------------------------------------------
# run_resync — entry point
# ---------------------------------------------------------------------------


def test_run_resync_returns_zero_on_success(tmp_path):
    from unittest.mock import patch

    from app.main import run_resync

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.WalletClient"),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        counts = MagicMock()
        counts.synced = 1
        counts.failed = 0
        counts.excluded = 0
        runner.resync_day.return_value = counts

        result = run_resync("2026-07-15")

    assert result == 0


def test_run_resync_invalid_date_returns_one():
    from app.main import run_resync

    result = run_resync("not-a-date")

    assert result == 1


def test_run_resync_calls_resync_day_with_date(tmp_path):
    from unittest.mock import patch

    from app.main import run_resync

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.WalletClient"),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        counts = MagicMock()
        counts.synced = 0
        counts.failed = 0
        counts.excluded = 0
        runner.resync_day.return_value = counts

        run_resync("2026-07-15")

    runner.resync_day.assert_called_once()
    call_args = runner.resync_day.call_args
    assert call_args[0][0] == "2026-07-15"


def test_run_resync_exception_returns_one(tmp_path):
    """run_resync must return 1 when resync_day raises."""
    from unittest.mock import patch

    from app.main import run_resync

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.setup_logging"),
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.WalletClient"),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        mock_cfg_cls.return_value = cfg

        runner = mock_runner_cls.return_value
        runner.resync_day.side_effect = RuntimeError("boom")

        result = run_resync("2026-07-15")

    assert result == 1


def test_resync_day_unknown_event_type_notifies(tmp_path):
    """resync_day must notify on unknown event types (same as build_batch)."""
    from unittest.mock import patch

    runner, _cfg, notifier = _make_runner_with_mocks()

    event = {
        "id": "ev-unk",
        "eventType": "SUPER_UNKNOWN_TYPE",
        "timestamp": "2026-07-15T10:00:00Z",
    }
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch("app.sync_runner.build_records_for_event", return_value=[]),
        ):
            runner.resync_day("2026-07-15", repo, wallet_client)

    notifier.unknown_event_type.assert_called_once_with("SUPER_UNKNOWN_TYPE")


def test_resync_day_put_failure_increments_failed(tmp_path):
    """A PUT error should count the event as failed and not call mark_processed_force."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-put-fail", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.side_effect = RuntimeError("API down")

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[{"accountId": "cash-id"}],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.failed == 1
        assert counts.synced == 0
        # wallet_record_id must remain unchanged (not force-updated on failure)
        assert repo.get_wallet_record_id(event) == "old-wid"


def test_resync_day_post_failure_for_new_event_increments_failed(tmp_path):
    """A POST error for a new event should count as failed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-post-fail", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()
        wallet_client.post_records.side_effect = RuntimeError("API down")

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[{"accountId": "cash-id"}],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.failed == 1
        assert counts.synced == 0


def test_resync_day_post_item_error_field_increments_failed(tmp_path):
    """A per-item error field in POST response for a new event counts as failed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-post-item-err", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "error": "invalid account"}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[{"accountId": "cash-id"}],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.failed == 1
        assert counts.synced == 0
        assert repo.get_wallet_record_id(event) is None


def test_resync_day_post_missing_result_increments_failed(tmp_path):
    """Missing result for a record index in POST response counts as failed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-post-missing", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()
        # Response has only 1 result but 2 records were submitted
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "wid-a"},
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[
                    {"accountId": "cash-id"},
                    {"accountId": "portfolio-id"},
                ],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.failed == 1
        assert counts.synced == 0
        assert repo.get_wallet_record_id(event) is None


def test_resync_day_extra_subrecord_post_item_error_increments_failed(tmp_path):
    """A per-item error field in extra-record POST response counts as failed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-extra-item-err", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-1")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.return_value = {"id": "wid-1", "success": True}
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "error": "bad payload"}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[
                    {"accountId": "cash-id"},
                    {"accountId": "portfolio-id"},
                ],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.failed == 1
        assert counts.synced == 0


def test_resync_day_extra_subrecord_empty_post_response_increments_failed(tmp_path):
    """An empty POST response for an extra sub-record counts as failed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-extra-empty", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-1")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.return_value = {"id": "wid-1", "success": True}
        wallet_client.post_records.return_value = []

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[
                    {"accountId": "cash-id"},
                    {"accountId": "portfolio-id"},
                ],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.failed == 1
        assert counts.synced == 0


def test_run_resync_rejects_datetime_string():
    """run_resync must reject full datetime strings — only YYYY-MM-DD is valid."""
    from app.main import run_resync

    result = run_resync("2026-07-15T12:00:00")

    assert result == 1


def test_resync_day_post_preserves_inputindex_order(tmp_path):
    """POST results returned out of API order must be re-ordered by inputIndex."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-order", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()
        # API returns the two sub-records in reverse inputIndex order.
        wallet_client.post_records.return_value = [
            {"inputIndex": 1, "id": "wid-b"},
            {"inputIndex": 0, "id": "wid-a"},
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[
                    {"accountId": "cash-id"},
                    {"accountId": "portfolio-id"},
                ],
            ),
        ):
            runner.resync_day("2026-07-15", repo, wallet_client)

        # Wallet IDs must be stored in record order (inputIndex 0 first).
        assert repo.get_wallet_record_id(event) == "wid-a,wid-b"


def test_resync_day_extra_subrecord_falls_back_to_post(tmp_path):
    """When there are more sub-records than stored wallet IDs, extras are POSTed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-extra", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        # Only one wallet ID stored, but event now produces 2 sub-records
        repo.mark_processed(event, wallet_record_id="wid-1")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.return_value = {"id": "wid-1", "success": True}
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "wid-2", "success": True}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[
                    {"accountId": "cash-id"},
                    {"accountId": "portfolio-id"},
                ],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.synced == 1
        wallet_client.put_record.assert_called_once_with(
            "wid-1", {"accountId": "cash-id"}
        )
        wallet_client.post_records.assert_called_once()
        new_id = repo.get_wallet_record_id(event)
        assert "wid-1" in new_id
        assert "wid-2" in new_id


def test_resync_day_extra_subrecord_post_failure_increments_failed(tmp_path):
    """POST failure for an extra sub-record (beyond stored IDs) counts as failed."""
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "ev-extra-fail", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-1")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.put_record.return_value = {"id": "wid-1", "success": True}
        wallet_client.post_records.side_effect = RuntimeError("extra POST failed")

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
            patch(
                "app.sync_runner.build_records_for_event",
                return_value=[
                    {"accountId": "cash-id"},
                    {"accountId": "portfolio-id"},
                ],
            ),
        ):
            counts = runner.resync_day("2026-07-15", repo, wallet_client)

        assert counts.failed == 1
        assert counts.synced == 0


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
# SyncRunner.connect — persists auth_state to DB
# ---------------------------------------------------------------------------


def test_connect_writes_ok_auth_state_on_success(tmp_path):
    """connect() must write status='ok' to auth_state when login succeeds."""
    from unittest.mock import patch

    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    with patch("app.sync_runner.TRClient"):
        runner.connect()

    with EventRepository(tmp_path / "sync.db") as repo:
        assert repo.get_auth_state("david") == "ok"


def test_connect_writes_failed_auth_state_on_login_failed(tmp_path):
    """connect() must write status='failed' when LoginFailedError is raised."""
    from unittest.mock import patch

    from app.sync_runner import SyncRunner
    from app.tr_client import LoginFailedError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = LoginFailedError("bad pin")
        with pytest.raises(LoginFailedError):
            runner.connect()

    with EventRepository(tmp_path / "sync.db") as repo:
        assert repo.get_auth_state("david") == "failed"


def test_connect_writes_expired_auth_state_on_session_expired(tmp_path):
    """connect() must write status='expired' when SessionExpiredError is raised."""
    from unittest.mock import patch

    from app.sync_runner import SyncRunner
    from app.tr_client import SessionExpiredError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = SessionExpiredError("expired")
        with pytest.raises(SessionExpiredError):
            runner.connect()

    with EventRepository(tmp_path / "sync.db") as repo:
        assert repo.get_auth_state("david") == "expired"


def test_connect_writes_failed_auth_state_on_authentication_error(tmp_path):
    """connect() must write status='failed' when AuthenticationError is raised."""
    from unittest.mock import patch

    from app.sync_runner import AuthenticationError, SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = AuthenticationError("auth error")
        with pytest.raises(AuthenticationError):
            runner.connect()

    with EventRepository(tmp_path / "sync.db") as repo:
        assert repo.get_auth_state("david") == "failed"


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


# ---------------------------------------------------------------------------
# SyncRunner — persists sync_run after process_results
# ---------------------------------------------------------------------------


def test_process_results_persists_sync_run_success(tmp_path):
    """process_results must write a success sync_run row when all events succeed."""
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    event = {"id": "e1", "amount": 100}
    results = [{"inputIndex": 0, "id": "w1"}]

    with EventRepository(tmp_path / "sync.db") as repo:
        runner.process_results(
            results,
            [event],
            [[0]],
            repo,
            excluded_count=1,
        )
        run = repo.get_sync_run("david")

    assert run is not None
    assert run["status"] == "success"
    assert run["saved"] == 1
    assert run["failed"] == 0
    assert run["excluded"] == 1


def test_process_results_persists_sync_run_partial(tmp_path):
    """process_results must write a partial sync_run when some events fail."""
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    event_ok = {"id": "e1", "amount": 100}
    event_fail = {"id": "e2", "amount": 50}
    results = [
        {"inputIndex": 0, "id": "w1"},
        {"inputIndex": 1, "error": "bad"},
    ]

    with EventRepository(tmp_path / "sync.db") as repo:
        runner.process_results(
            results,
            [event_ok, event_fail],
            [[0], [1]],
            repo,
        )
        run = repo.get_sync_run("david")

    assert run["status"] == "partial"
    assert run["saved"] == 1
    assert run["failed"] == 1


def test_process_results_persists_sync_run_failed(tmp_path):
    """process_results must write a failed sync_run when all events fail."""
    from app.sync_runner import SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    event = {"id": "e1", "amount": 100}
    results = [{"inputIndex": 0, "error": "oops"}]

    with EventRepository(tmp_path / "sync.db") as repo:
        runner.process_results(
            results,
            [event],
            [[0]],
            repo,
        )
        run = repo.get_sync_run("david")

    assert run["status"] == "failed"
    assert run["saved"] == 0
    assert run["failed"] == 1


def test_fetch_events_persists_failed_sync_run_on_login_error(tmp_path):
    """fetch_events must write a failed sync_run when LoginFailedError is raised."""
    from unittest.mock import patch

    from app.sync_runner import SyncRunner
    from app.tr_client import LoginFailedError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime(2024, 1, 1, tzinfo=UTC)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = LoginFailedError("fail")
        with pytest.raises(SystemExit):
            runner.fetch_events(since)

    with EventRepository(tmp_path / "sync.db") as repo:
        run = repo.get_sync_run("david")
    assert run is not None
    assert run["status"] == "failed"


def test_fetch_events_persists_failed_sync_run_on_auth_error(tmp_path):
    """fetch_events must write a failed sync_run when AuthenticationError is raised."""
    from unittest.mock import patch

    from app.sync_runner import AuthenticationError, SyncRunner

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)
    since = datetime(2024, 1, 1, tzinfo=UTC)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = AuthenticationError("auth")
        with pytest.raises(SystemExit):
            runner.fetch_events(since)

    with EventRepository(tmp_path / "sync.db") as repo:
        run = repo.get_sync_run("david")
    assert run is not None
    assert run["status"] == "failed"
