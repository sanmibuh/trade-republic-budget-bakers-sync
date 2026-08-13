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
    event = {
        "id": "evt-json",
        "eventType": "SELL_ORDER",
        "timestamp": "2024-01-01T00:00:00Z",
    }
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
        row = repo._conn.execute(
            "SELECT raw FROM processed_events WHERE event_id='evt-bad'"
        ).fetchone()
    assert row is not None
    # raw should be the str() fallback, not valid JSON
    assert "Unserializable" in row[0]


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

    event = {
        "eventType": "TOTALLY_NEW_TYPE",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "5.00",
    }
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

    event = {
        "eventType": "BUY_ORDER",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "100.00",
    }
    with EventRepository(tmp_path / "test.db") as repo:
        _build_batch([event], cfg, repo, notifier)

    notifier.unknown_event_type.assert_not_called()


# ---------------------------------------------------------------------------
# _build_batch — zero-amount events are excluded
# ---------------------------------------------------------------------------


def test_build_batch_excludes_zero_amount_event(tmp_path):
    from app.config import Config
    from app.main import _build_batch
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}

    notifier = MagicMock(spec=Notifier)

    # A zero-amount event produces no records → should be excluded
    event = {
        "eventType": "SAVINGS_PLAN_EXECUTED",
        "id": "ev-zero",
        "timestamp": "2024-01-01T00:00:00Z",
        "amount": "0.00",
    }
    with EventRepository(tmp_path / "test.db") as repo:
        batch = _build_batch([event], cfg, repo, notifier)

    assert batch.excluded_count == 1
    assert batch.records == []


# ---------------------------------------------------------------------------
# _fetch_events — error branches
# ---------------------------------------------------------------------------


def test_fetch_events_login_failed_exits():
    from unittest.mock import patch

    from app.main import _fetch_events
    from app.tr_client import LoginFailedError

    cfg = MagicMock()
    notifier = MagicMock()
    since = datetime.now(timezone.utc)

    with patch("app.main.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = LoginFailedError("bad pin")
        with pytest.raises(SystemExit) as exc_info:
            _fetch_events(cfg, notifier, since)

    assert exc_info.value.code == 1
    notifier.login_failed.assert_called_once()


def test_fetch_events_session_expired_exits():
    from unittest.mock import patch

    from app.main import _fetch_events
    from app.tr_client import SessionExpiredError

    cfg = MagicMock()
    notifier = MagicMock()
    since = datetime.now(timezone.utc)

    with patch("app.main.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = SessionExpiredError("needs bootstrap")
        with pytest.raises(SystemExit) as exc_info:
            _fetch_events(cfg, notifier, since)

    assert exc_info.value.code == 1
    notifier.authentication_required.assert_called_once()
    notifier.login_failed.assert_not_called()


def test_fetch_events_http_401_exits():
    from unittest.mock import patch

    from requests import HTTPError

    from app.main import _fetch_events

    cfg = MagicMock()
    notifier = MagicMock()
    since = datetime.now(timezone.utc)

    err = HTTPError()
    err.response = MagicMock()
    err.response.status_code = 401

    with patch("app.main.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = err
        with pytest.raises(SystemExit) as exc_info:
            _fetch_events(cfg, notifier, since)

    assert exc_info.value.code == 1
    notifier.authentication_required.assert_called_once()


def test_fetch_events_http_non_401_reraises():
    from unittest.mock import patch

    from requests import HTTPError

    from app.main import _fetch_events

    cfg = MagicMock()
    notifier = MagicMock()
    since = datetime.now(timezone.utc)

    err = HTTPError()
    err.response = MagicMock()
    err.response.status_code = 500

    with patch("app.main.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = err
        with pytest.raises(HTTPError):
            _fetch_events(cfg, notifier, since)

    notifier.error.assert_called_once_with(err)


def test_fetch_events_unexpected_exception_reraises():
    from unittest.mock import patch

    from app.main import _fetch_events

    cfg = MagicMock()
    notifier = MagicMock()
    since = datetime.now(timezone.utc)

    boom = RuntimeError("unexpected")
    with patch("app.main.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = boom
        with pytest.raises(RuntimeError):
            _fetch_events(cfg, notifier, since)

    notifier.error.assert_called_once_with(boom)


def test_fetch_events_success_returns_events():
    from unittest.mock import patch

    from app.main import _fetch_events

    cfg = MagicMock()
    notifier = MagicMock()
    since = datetime.now(timezone.utc)
    fake_events = [{"id": "e1"}, {"id": "e2"}]

    with patch("app.main.TRClient") as MockTR:
        MockTR.return_value.fetch_timeline_events.return_value = fake_events
        result = _fetch_events(cfg, notifier, since)

    assert result == fake_events


# ---------------------------------------------------------------------------
# _process_results
# ---------------------------------------------------------------------------


def test_process_results_marks_successful_events(tmp_path):
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0}]  # no "error" key → success
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = _process_results(
            results, [event], event_record_indices, repo, MagicMock(spec=Notifier)
        )
        unprocessed = repo.filter_unprocessed([event])

    assert counts.synced == 1
    assert counts.failed == 0
    assert unprocessed == []  # was marked processed


def test_process_results_counts_failures(tmp_path):
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0, "error": "bad record"}]
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = _process_results(
            results, [event], event_record_indices, repo, MagicMock(spec=Notifier)
        )
        unprocessed = repo.filter_unprocessed([event])

    assert counts.synced == 0
    assert counts.failed == 1
    assert unprocessed == [event]  # NOT marked processed


def test_process_results_skips_events_with_no_records(tmp_path):
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    results = []
    event_record_indices = [[]]  # event produced no records

    with EventRepository(tmp_path / "test.db") as repo:
        counts = _process_results(
            results, [event], event_record_indices, repo, MagicMock(spec=Notifier)
        )

    assert counts.synced == 0
    assert counts.failed == 0


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
        patch("app.main._fetch_events", return_value=fake_events),
        patch("app.main.filter_by_lookback", return_value=fake_events),
        patch("app.main._build_batch") as mock_batch,
        patch("app.main.WalletClient") as mock_wallet,
        patch("app.main._process_results") as mock_results,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        mock_cfg_cls.return_value = cfg

        batch = MagicMock()
        batch.records = [{"amount": 10}]
        batch.excluded_count = 0
        batch.event_record_indices = [[0]]
        mock_batch.return_value = batch

        from app.main import _SyncCounts

        mock_results.return_value = _SyncCounts(synced=1, failed=0)
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
        patch("app.main._fetch_events", return_value=[]),
        patch("app.main.filter_by_lookback", return_value=[]),
        patch("app.main._build_batch") as mock_batch,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        mock_cfg_cls.return_value = cfg

        batch = MagicMock()
        batch.records = []
        batch.excluded_count = 0
        batch.event_record_indices = []
        mock_batch.return_value = batch

        result = run()

    assert result == 0


def test_run_authentication_error_exits(tmp_path):
    """except AuthenticationError branch in _fetch_events — simulate via patching."""
    from unittest.mock import patch

    from app.main import _fetch_events

    cfg = MagicMock()
    notifier = MagicMock()
    since = datetime.now(timezone.utc)

    # Import the sentinel class the module uses and raise it
    import app.main as main_module

    AuthErr = main_module.AuthenticationError

    with patch("app.main.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = AuthErr("auth required")
        with pytest.raises(SystemExit) as exc_info:
            _fetch_events(cfg, notifier, since)

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
        patch("app.main._fetch_events", return_value=[]),
        patch("app.main.filter_by_lookback", return_value=[]),
        patch("app.main._build_batch") as mock_batch,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        mock_cfg_cls.return_value = cfg

        batch = MagicMock()
        batch.records = [{"amount": 10}]
        batch.excluded_count = 0
        batch.event_record_indices = [[0]]
        mock_batch.side_effect = boom  # simulate error during batch build

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
        patch("app.main._fetch_events", return_value=[]),
        patch("app.main.filter_by_lookback", return_value=[]),
        patch("app.main._build_batch") as mock_batch,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        mock_cfg_cls.return_value = cfg

        batch = MagicMock()
        batch.records = []
        batch.excluded_count = 0
        batch.event_record_indices = []
        mock_batch.return_value = batch

        notifier_instance = mock_notifier_cls.return_value
        notifier_instance.sync_complete.return_value = False  # simulate not sent

        result = run()

    assert result == 0
    notifier_instance.sync_complete.assert_called_once()


# ---------------------------------------------------------------------------
# EventRepository — wallet_record_id schema & migration
# ---------------------------------------------------------------------------


def test_repo_schema_has_wallet_record_id_column(tmp_path):
    with EventRepository(tmp_path / "db") as repo:
        cols = [
            row[1]
            for row in repo._conn.execute(
                "PRAGMA table_info(processed_events)"
            ).fetchall()
        ]
    assert "wallet_record_id" in cols


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

    with EventRepository(db) as repo:
        cols = [
            row[1]
            for row in repo._conn.execute(
                "PRAGMA table_info(processed_events)"
            ).fetchall()
        ]
    assert "wallet_record_id" in cols


# ---------------------------------------------------------------------------
# EventRepository — wallet_record_id read / write
# ---------------------------------------------------------------------------


def test_mark_processed_stores_wallet_record_id(tmp_path):
    event = {"id": "evt-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="wid-abc")
        repo.commit()
        row = repo._conn.execute(
            "SELECT wallet_record_id FROM processed_events WHERE event_id='evt-wr'"
        ).fetchone()
    assert row is not None
    assert row[0] == "wid-abc"


def test_mark_processed_without_wallet_record_id_stores_null(tmp_path):
    event = {"id": "evt-no-wr", "timestamp": "2024-01-01T00:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event)
        repo.commit()
        row = repo._conn.execute(
            "SELECT wallet_record_id FROM processed_events WHERE event_id='evt-no-wr'"
        ).fetchone()
    assert row is not None
    assert row[0] is None


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
# _process_results — passes wallet_record_id to mark_processed
# ---------------------------------------------------------------------------


def test_process_results_passes_wallet_record_id(tmp_path):
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev-wrid", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0, "id": "wallet-record-1"}]
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        _process_results(
            results, [event], event_record_indices, repo, MagicMock(spec=Notifier)
        )
        wallet_id = repo.get_wallet_record_id(event)

    assert wallet_id == "wallet-record-1"


def test_process_results_stores_joined_ids_for_multi_record_event(tmp_path):
    """An event that maps to 2 Wallet records stores both IDs joined by comma."""
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev-multi", "timestamp": "2024-01-01T00:00:00Z"}
    results = [
        {"inputIndex": 0, "id": "wid-1"},
        {"inputIndex": 1, "id": "wid-2"},
    ]
    event_record_indices = [[0, 1]]

    with EventRepository(tmp_path / "test.db") as repo:
        _process_results(
            results, [event], event_record_indices, repo, MagicMock(spec=Notifier)
        )
        wallet_id = repo.get_wallet_record_id(event)

    assert wallet_id == "wid-1,wid-2"


def test_process_results_no_wallet_id_when_result_has_no_id(tmp_path):
    """If the API result has no 'id' field, wallet_record_id is stored as None."""
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev-noid", "timestamp": "2024-01-01T00:00:00Z"}
    results = [{"inputIndex": 0}]  # no "id" field
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        _process_results(
            results, [event], event_record_indices, repo, MagicMock(spec=Notifier)
        )
        wallet_id = repo.get_wallet_record_id(event)

    assert wallet_id is None


def test_process_results_missing_index_counts_as_failure(tmp_path):
    """An event whose record index is absent from API results is not marked processed."""
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev-missing", "timestamp": "2024-01-01T00:00:00Z"}
    results = []  # API returned no results at all
    event_record_indices = [[0]]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = _process_results(
            results, [event], event_record_indices, repo, MagicMock(spec=Notifier)
        )
        unprocessed = repo.filter_unprocessed([event])

    assert counts.failed == 1
    assert unprocessed == [event]


def test_process_results_missing_index_notifies(tmp_path):
    """A missing result index triggers a warning via notifier."""
    from app.main import _process_results
    from app.notifier import Notifier

    event = {"id": "ev-notify", "timestamp": "2024-01-01T00:00:00Z"}
    results = []
    event_record_indices = [[0]]
    notifier = MagicMock(spec=Notifier)

    with EventRepository(tmp_path / "test.db") as repo:
        _process_results(results, [event], event_record_indices, repo, notifier)

    notifier.missing_api_result.assert_called_once()


# ---------------------------------------------------------------------------
# _read_label_ids
# ---------------------------------------------------------------------------


def test_read_label_ids_returns_empty_when_no_env(monkeypatch):
    from app.config import LABELABLE_EVENT_TYPES, _read_label_ids

    for et in LABELABLE_EVENT_TYPES:
        monkeypatch.delenv(f"LABEL_{et}", raising=False)

    result = _read_label_ids()
    assert result == {}


def test_read_label_ids_picks_up_set_vars(monkeypatch):
    from app.config import _read_label_ids

    monkeypatch.setenv("LABEL_BANK_TRANSACTION_INCOMING", "label-abc-123")
    monkeypatch.setenv("LABEL_BUY_ORDER", "label-xyz-456")

    result = _read_label_ids()
    assert result["BANK_TRANSACTION_INCOMING"] == "label-abc-123"
    assert result["BUY_ORDER"] == "label-xyz-456"


def test_read_label_ids_ignores_blank_values(monkeypatch):
    from app.config import _read_label_ids

    monkeypatch.setenv("LABEL_BANK_TRANSACTION_INCOMING", "   ")

    result = _read_label_ids()
    assert "BANK_TRANSACTION_INCOMING" not in result


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
        patch("app.main._fetch_events", return_value=fake_events),
        patch("app.main.filter_by_lookback", return_value=fake_events),
        patch("app.main._build_batch") as mock_batch,
        patch("app.main.WalletClient") as mock_wallet,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        mock_cfg_cls.return_value = cfg

        batch = MagicMock()
        batch.records = [{"amount": 10}]
        batch.excluded_count = 3  # excluded events present
        batch.event_record_indices = [[0]]
        mock_batch.return_value = batch

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
        patch("app.main.TRClient") as MockTR,
        patch("app.main._build_code_provider", return_value=MagicMock()),
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
        patch("app.main.TRClient") as MockTR,
        patch("app.main._build_code_provider", return_value=None),
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
        patch("app.main.TRClient") as MockTR,
        patch("app.main._build_code_provider", return_value=MagicMock()),
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
        patch("app.main.TRClient") as MockTR,
        patch("app.main._build_code_provider", return_value=MagicMock()),
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
# _connect — shared TR client creation + login
# ---------------------------------------------------------------------------


def test_connect_builds_client_and_calls_connect_with_code_provider():
    from unittest.mock import patch

    from app.main import _connect

    cfg = MagicMock()
    cfg.phone_number = "+49123"
    cfg.pin = "1234"
    cfg.data_dir = "/data"
    notifier = MagicMock()
    provider = MagicMock()

    with (
        patch("app.main.TRClient") as MockTR,
        patch("app.main._build_code_provider", return_value=provider) as mock_build,
    ):
        result = _connect(cfg, notifier)

    MockTR.assert_called_once_with("+49123", "1234", "/data")
    mock_build.assert_called_once_with(cfg, notifier)
    kwargs = MockTR.return_value.connect.call_args.kwargs
    assert kwargs["on_login_required"] == notifier.login_required
    assert kwargs["on_login_success"] == notifier.login_success
    assert kwargs["code_provider"] is provider
    assert result is MockTR.return_value
