from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.persistence import EventRepository
from app.sync_runner import SyncRunner, _Batch

# ---------------------------------------------------------------------------
# Module-level DB initialisation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _init_test_dbs(tmp_path):
    """Pre-initialise all DB paths used by tests in this module.

    Tests open ``EventRepository`` directly against these paths without going
    through the CLI entry point, so ``init_db`` must be called explicitly here
    to create the schema before the first repository is opened.
    """
    from app.persistence import init_db

    for name in ("test.db", "sync.db", "db"):
        init_db(tmp_path / name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner(cfg=None, notifier=None):
    """Return a SyncRunner with minimal MagicMock dependencies."""
    cfg = cfg or MagicMock()
    notifier = notifier or MagicMock()
    return SyncRunner(cfg, notifier), cfg, notifier


def _make_runner_with_mocks():
    """Return a (runner, cfg, notifier) tuple configured for resync tests."""
    cfg = MagicMock()
    cfg.wallet_cash_account_id = "cash-id"
    cfg.wallet_portfolio_account_id = "portfolio-id"
    cfg.label_ids = {}
    notifier = MagicMock()
    return SyncRunner(cfg, notifier), cfg, notifier


# ---------------------------------------------------------------------------
# SyncRunner.build_batch — unknown event type triggers notifier
# ---------------------------------------------------------------------------


def test_build_batch_notifies_on_unknown_event_type(tmp_path):
    from app.config import Config
    from app.notifier import Notifier

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

    from app.tr_client import LoginFailedError

    runner, _cfg, notifier = _make_runner()
    since = datetime.now(UTC)

    with patch("app.sync_runner.TRClient") as MockTR:
        MockTR.return_value.connect.side_effect = LoginFailedError("bad pin")
        with pytest.raises(SystemExit) as exc_info:
            runner.fetch_events(since)

    assert exc_info.value.code == 1
    notifier.login_failed.assert_called_once()


def test_fetch_events_session_expired_exits():
    from unittest.mock import patch

    from app.tr_client import SessionExpiredError

    runner, _cfg, notifier = _make_runner()
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

    runner, _cfg, notifier = _make_runner()
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

    runner, _cfg, notifier = _make_runner()
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

    runner, _cfg, notifier = _make_runner()
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

    runner, _cfg, _notifier = _make_runner()
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
# SyncRunner.process_results — passes wallet_record_id to mark_processed
# ---------------------------------------------------------------------------


def test_process_results_passes_wallet_record_id(tmp_path):
    from app.notifier import Notifier

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
# SyncRunner.connect — creates TRClient and delegates to code provider
# ---------------------------------------------------------------------------


def test_connect_builds_client_and_calls_connect_with_code_provider():
    from unittest.mock import patch

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
# SyncRunner.connect — persists auth_state to DB
# ---------------------------------------------------------------------------


def test_connect_writes_ok_auth_state_on_success(tmp_path):
    """connect() must write status='ok' to auth_state when login succeeds."""
    from unittest.mock import patch

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
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

    from app.tr_client import LoginFailedError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
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

    from app.tr_client import SessionExpiredError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
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

    from app.sync_runner import AuthenticationError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
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
# SyncRunner.resync_day — resync orchestration for a single day
# ---------------------------------------------------------------------------


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
    assert since.hour == 0
    assert since.minute == 0


def test_resync_day_skips_dedup_filter(tmp_path):
    """resync_day must NOT call filter_unprocessed — all events are re-processed.

    The pipeline uses POST (never PUT) and force-upserts the wallet_record_id so
    that even an already-processed event ends up with the new ID from the API.
    """
    from unittest.mock import patch

    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {"id": "already-done", "timestamp": "2026-07-15T10:00:00Z"}
    with EventRepository(tmp_path / "db") as repo:
        # Pre-mark the event as processed
        repo.mark_processed(event, wallet_record_id="existing-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "new-wid", "success": True}
        ]

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

        # PUT must never be called — resync always uses POST
        wallet_client.put_record.assert_not_called()
        # Event must have been re-processed (wallet_record_id updated via upsert)
        assert repo.get_wallet_record_id(event) == "new-wid"


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


# ---------------------------------------------------------------------------
# SyncRunner — persists sync_run after process_results
# ---------------------------------------------------------------------------


def test_process_results_persists_sync_run_success(tmp_path):
    """process_results must write a success sync_run row when all events succeed."""
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


def test_process_results_logs_warning_when_set_sync_run_raises(tmp_path, caplog):
    """process_results must log a warning and not raise when set_sync_run fails."""
    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.instance = "david"
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    event = {"id": "e1", "amount": 100}
    results = [{"inputIndex": 0, "id": "w1"}]

    with (
        EventRepository(tmp_path / "sync.db") as repo,
        patch.object(repo, "set_sync_run", side_effect=RuntimeError("db locked")),
        caplog.at_level(logging.WARNING, logger="app.sync_runner"),
    ):
        counts = runner.process_results(results, [event], [[0]], repo)

    assert counts.synced == 1
    assert any("Failed to persist sync_run" in r.message for r in caplog.records)


def test_fetch_events_persists_failed_sync_run_on_login_error(tmp_path):
    """fetch_events must write a failed sync_run when LoginFailedError is raised."""
    from unittest.mock import patch

    from app.tr_client import LoginFailedError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
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

    from app.sync_runner import AuthenticationError

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
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


# ---------------------------------------------------------------------------
# SyncRunner._notify_fetch_summary
# ---------------------------------------------------------------------------


def test_notify_fetch_summary_returns_skipped_count():
    """_notify_fetch_summary returns len(recent) - len(new)."""
    runner, _cfg, _notifier = _make_runner()
    since = datetime(2024, 1, 1, tzinfo=UTC)
    recent = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    new = [{"id": "a"}]

    skipped = runner.notify_fetch_summary(since, recent, new)

    assert skipped == 2


def test_notify_fetch_summary_calls_notifier_with_correct_counts():
    """_notify_fetch_summary delegates to notifier.fetch_summary with correct counts."""
    runner, _cfg, notifier = _make_runner()

    since = datetime(2024, 3, 15, tzinfo=UTC)
    recent = [{"id": "x"}, {"id": "y"}]
    new = [{"id": "x"}]

    runner.notify_fetch_summary(since, recent, new)

    notifier.fetch_summary.assert_called_once()
    call_kwargs = notifier.fetch_summary.call_args.kwargs
    assert call_kwargs["since"] == "2024-03-15"
    assert call_kwargs["fetched"] == 2
    assert call_kwargs["new"] == 1
    assert call_kwargs["skipped"] == 1


def test_notify_fetch_summary_all_new_skipped_zero():
    """_notify_fetch_summary returns 0 when all events are new."""
    runner, _cfg, notifier = _make_runner()

    since = datetime(2024, 1, 1, tzinfo=UTC)
    events = [{"id": "a"}, {"id": "b"}]

    skipped = runner.notify_fetch_summary(since, events, events)

    assert skipped == 0
    call_kwargs = notifier.fetch_summary.call_args.kwargs
    assert call_kwargs["skipped"] == 0


# ---------------------------------------------------------------------------
# SyncRunner._submit_batch
# ---------------------------------------------------------------------------


def test_submit_batch_empty_records_commits_repo(tmp_path):
    """_submit_batch with no records calls repo.commit() and returns excluded count."""
    runner, _cfg, _notifier = _make_runner()

    repo = MagicMock()
    wallet_client = MagicMock()
    batch = _Batch(records=[], event_record_indices=[], excluded_count=3)

    counts = runner.submit_batch(batch, wallet_client, repo, new_events=[])

    repo.commit.assert_called_once()
    wallet_client.post_records.assert_not_called()
    assert counts.excluded == 3
    assert counts.synced == 0
    assert counts.failed == 0


def test_submit_batch_posts_records_and_returns_counts(tmp_path):
    """_submit_batch posts records, retries failures, and returns process_results counts."""
    cfg = MagicMock()
    cfg.instance = "tst"
    cfg.data_dir = tmp_path
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    record = {"note": "dividend", "amount": 10}
    event = {"id": "ev1", "timestamp": "2024-01-01T00:00:00Z"}
    batch = _Batch(
        records=[record],
        event_record_indices=[[0]],
        excluded_count=0,
        categorizer=None,
    )

    api_results = [{"inputIndex": 0, "id": "wid1"}]
    wallet_client = MagicMock()
    wallet_client.post_records.return_value = api_results

    with EventRepository(tmp_path / "test.db") as repo:
        counts = runner.submit_batch(batch, wallet_client, repo, new_events=[event])

    wallet_client.post_records.assert_called_once_with([record])
    assert counts.synced == 1
    assert counts.failed == 0
    assert counts.excluded == 0


def test_submit_batch_excluded_count_carried_through_when_records_present(tmp_path):
    """_submit_batch passes excluded_count from batch through process_results."""
    cfg = MagicMock()
    cfg.instance = "tst"
    cfg.data_dir = tmp_path
    notifier = MagicMock()
    runner = SyncRunner(cfg, notifier)

    record = {"note": "div", "amount": 5}
    event = {"id": "e2", "timestamp": "2024-01-01T00:00:00Z"}
    batch = _Batch(
        records=[record],
        event_record_indices=[[0]],
        excluded_count=7,
        categorizer=None,
    )

    wallet_client = MagicMock()
    wallet_client.post_records.return_value = [{"inputIndex": 0, "id": "wid2"}]

    with EventRepository(tmp_path / "test.db") as repo:
        counts = runner.submit_batch(batch, wallet_client, repo, new_events=[event])

    assert counts.excluded == 7


# ---------------------------------------------------------------------------
# SyncRunner.build_batch — CANCELED events are excluded without a wallet call
# ---------------------------------------------------------------------------


def test_build_batch_excludes_canceled_event(tmp_path):
    """CANCELED events must be marked excluded (same as zero-amount) without any
    wallet API call."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {
        "eventType": "CARD_TRANSACTION",
        "id": "ev-canceled",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "test.db") as repo:
        batch = runner.build_batch([event], repo)

    assert batch.excluded_count == 1
    assert batch.records == []


def test_build_batch_canceled_event_marked_processed(tmp_path):
    """CANCELED events excluded during build_batch must be persisted so subsequent
    syncs do not revisit them."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    event = {
        "eventType": "CARD_TRANSACTION",
        "id": "ev-canceled-mark",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "test.db") as repo:
        runner.build_batch([event], repo)
        repo.commit()
        assert repo.is_processed("ev-canceled-mark")


# ---------------------------------------------------------------------------
# SyncRunner._resync_single_event — CANCELED with no wallet_record_id → excluded
# ---------------------------------------------------------------------------


def test_resync_day_canceled_event_without_wallet_id_is_excluded(tmp_path):
    """A CANCELED event that was never synced must be marked excluded (not posted)."""
    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {
        "id": "ev-canceled-new",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "db") as repo:
        wallet_client = MagicMock()

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
        ):
            counts = runner.resync_day("2026-08-24", repo, wallet_client)

        wallet_client.post_records.assert_not_called()
        wallet_client.put_record.assert_not_called()
        assert counts.excluded == 1
        assert counts.synced == 0
        assert repo.is_processed("ev-canceled-new")


# ---------------------------------------------------------------------------
# SyncRunner._resync_single_event — CANCELED with existing wallet_record_id → reversal
# ---------------------------------------------------------------------------


def test_resync_day_canceled_event_with_wallet_id_posts_reversal(tmp_path):
    """A CANCELED event that was previously synced must trigger a POST of a
    reversal record (not a PUT of the original)."""
    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {
        "id": "ev-canceled-existing",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "reversal-wid", "success": True}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
        ):
            counts = runner.resync_day("2026-08-24", repo, wallet_client)

        wallet_client.put_record.assert_not_called()
        wallet_client.post_records.assert_called_once()
        assert counts.synced == 1
        assert counts.failed == 0


def test_resync_day_canceled_reversal_amount_is_positive(tmp_path):
    """The reversal record posted for a CANCELED debit must have a positive amount."""
    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {
        "id": "ev-canceled-amount",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "reversal-wid", "success": True}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
        ):
            runner.resync_day("2026-08-24", repo, wallet_client)

        posted = wallet_client.post_records.call_args[0][0]
        assert len(posted) == 1
        assert posted[0]["amount"]["value"] == pytest.approx(7.62)


def test_resync_day_canceled_reversal_note_contains_canceled_prefix(tmp_path):
    """The reversal note must start with [Cancelada] so it is identifiable."""
    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {
        "id": "ev-canceled-note",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "reversal-wid", "success": True}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
        ):
            runner.resync_day("2026-08-24", repo, wallet_client)

        posted = wallet_client.post_records.call_args[0][0]
        assert posted[0]["note"].startswith("[Cancelada]")


def test_resync_day_canceled_reversal_post_failure_increments_failed(tmp_path):
    """If the reversal POST fails, the event must count as failed."""
    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {
        "id": "ev-canceled-fail",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "error": "server error"}
        ]

        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
        ):
            counts = runner.resync_day("2026-08-24", repo, wallet_client)

    assert counts.failed == 1
    assert counts.synced == 0


def test_resync_day_canceled_reversal_is_idempotent(tmp_path):
    """Running resync twice on a CANCELED event that already had a reversal posted
    must not post a second reversal — only the first run should POST."""
    runner, _cfg, _notifier = _make_runner_with_mocks()

    event = {
        "id": "ev-canceled-idem",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()

        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "id": "reversal-wid", "success": True}
        ]

        # First resync — should post the reversal
        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
        ):
            counts_first = runner.resync_day("2026-08-24", repo, wallet_client)

        assert counts_first.synced == 1
        assert wallet_client.post_records.call_count == 1

        # Second resync — must not post again
        wallet_client.reset_mock()
        with (
            patch.object(runner, "fetch_events", return_value=[event]),
            patch("app.sync_runner.filter_by_lookback", return_value=[event]),
        ):
            counts_second = runner.resync_day("2026-08-24", repo, wallet_client)

        wallet_client.post_records.assert_not_called()
        wallet_client.put_record.assert_not_called()
        assert counts_second.excluded == 1


# ---------------------------------------------------------------------------
# SyncRunner.build_batch — cancellation_events produce reversal records
# ---------------------------------------------------------------------------


def test_build_batch_cancellation_events_produce_reversal_records(tmp_path):
    """cancellation_events passed to build_batch must yield reversal records
    (negated amount, [Cancelada] note) in the batch."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    canceled_event = {
        "id": "ev-cancel-batch",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[canceled_event])

    assert len(batch.records) == 1
    assert batch.records[0]["amount"]["value"] == pytest.approx(7.62)
    assert batch.records[0]["note"].startswith("[Cancelada]")


def test_build_batch_cancellation_plus_new_events_combined(tmp_path):
    """build_batch must combine new-event records and cancellation reversal records
    in a single batch."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    new_event = {
        "id": "ev-new",
        "eventType": "PAYMENT_INBOUND",
        "timestamp": "2026-08-24T08:00:00Z",
        "title": "Salary",
        "amount": {"currency": "EUR", "value": 1000.0},
    }
    canceled_event = {
        "id": "ev-cancel-combo",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch(
            [new_event], repo, cancellation_events=[canceled_event]
        )

    assert len(batch.records) == 2


def test_build_batch_cancellation_wallet_id_cleared_after_submit(tmp_path):
    """After a successful submit_batch for a cancellation event, the stored
    wallet_record_id must be cleared to prevent re-reversal on the next sync."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    canceled_event = {
        "id": "ev-cancel-clear",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    wallet_client = MagicMock()
    wallet_client.post_records.return_value = [
        {"inputIndex": 0, "id": "reversal-wid", "success": True}
    ]
    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[canceled_event])
        runner.submit_batch(batch, wallet_client, repo, new_events=[])
        repo.commit()
        assert repo.get_wallet_record_id(canceled_event) is None


def test_build_batch_zero_amount_cancellation_is_excluded_and_id_cleared(tmp_path):
    """A cancellation event with zero amount must be counted as excluded and its
    wallet_record_id cleared so filter_cancellation_pending never returns it again."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    zero_canceled_event = {
        "id": "ev-cancel-zero",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": 0.0},
    }
    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(zero_canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[zero_canceled_event])
        # Simulate the commit that submit_batch / process_results would do
        repo.commit()

    # No reversal record should be generated for a zero-amount cancellation
    assert len(batch.records) == 0
    # The excluded count must reflect this event
    assert batch.excluded_count == 1
    # And the wallet_record_id must have been cleared to break the loop
    with EventRepository(tmp_path / "test.db") as repo:
        assert repo.get_wallet_record_id(zero_canceled_event) is None


def test_build_batch_cancellation_wallet_id_cleared_without_success_field(tmp_path):
    """wallet_record_id must be cleared even when the API response has no 'success'
    field — a result without 'error' is considered successful, matching process_results."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    canceled_event = {
        "id": "ev-cancel-no-success",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    wallet_client = MagicMock()
    # Real API response: no 'success' field, just 'inputIndex' and 'id'
    wallet_client.post_records.return_value = [{"inputIndex": 0, "id": "reversal-wid"}]
    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[canceled_event])
        runner.submit_batch(batch, wallet_client, repo, new_events=[])
        repo.commit()
        assert repo.get_wallet_record_id(canceled_event) is None


# ---------------------------------------------------------------------------
# submit_batch — cancellation reversal counts included in SyncCounts
# ---------------------------------------------------------------------------


def test_submit_batch_successful_reversal_counts_as_synced(tmp_path):
    """A successfully posted reversal must increment synced, not be silently dropped."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    canceled_event = {
        "id": "ev-count-synced",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    wallet_client = MagicMock()
    wallet_client.post_records.return_value = [{"inputIndex": 0, "id": "rev-wid"}]

    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[canceled_event])
        counts = runner.submit_batch(batch, wallet_client, repo, new_events=[])

    assert counts.synced == 1
    assert counts.failed == 0


def test_submit_batch_failed_reversal_counts_as_failed(tmp_path):
    """A reversal that the API rejects must increment failed, not be silently dropped."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    canceled_event = {
        "id": "ev-count-failed",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    wallet_client = MagicMock()
    wallet_client.post_records.return_value = [
        {"inputIndex": 0, "error": "Bad request"}
    ]

    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[canceled_event])
        counts = runner.submit_batch(batch, wallet_client, repo, new_events=[])

    assert counts.failed == 1
    assert counts.synced == 0


def test_submit_batch_sync_run_includes_reversal_counts(tmp_path):
    """set_sync_run must reflect the final counts including cancellation reversals,
    not the intermediate counts from process_results alone."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    canceled_event = {
        "id": "ev-syncrun",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    wallet_client = MagicMock()
    wallet_client.post_records.return_value = [{"inputIndex": 0, "id": "rev-wid"}]

    with EventRepository(tmp_path / "test.db", instance="tst") as repo:
        repo.mark_processed(canceled_event, wallet_record_id="old-wid")
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[canceled_event])
        runner.submit_batch(batch, wallet_client, repo, new_events=[])
        run = repo.get_sync_run("tst")

    assert run is not None
    assert run["saved"] == 1  # reversal counted
    assert run["failed"] == 0
    assert run["status"] == "success"


def test_build_batch_cancellation_skipped_when_wallet_id_already_cleared(tmp_path):
    """If wallet_record_id was already cleared (previous reversal posted successfully),
    build_batch must not build another reversal record for the same event."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"

    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)

    canceled_event = {
        "id": "ev-already-cleared",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    with EventRepository(tmp_path / "test.db") as repo:
        # Simulate a previous reversal: event is in DB but wallet_record_id is NULL
        repo.mark_processed(canceled_event, wallet_record_id=None)
        repo.commit()
        batch = runner.build_batch([], repo, cancellation_events=[canceled_event])

    assert len(batch.records) == 0
    assert batch.excluded_count == 1


# ---------------------------------------------------------------------------
# process_results force=True — uses mark_processed_force (upsert)
# ---------------------------------------------------------------------------


def test_process_results_force_updates_existing_wallet_record_id(tmp_path):
    """With force=True, process_results must overwrite an existing wallet_record_id
    (INSERT OR REPLACE), not silently skip it (INSERT OR IGNORE)."""
    cfg = MagicMock()
    cfg.instance = "tst"
    runner = SyncRunner(cfg, MagicMock())

    event = {
        "id": "ev-force",
        "timestamp": "2026-08-24T08:00:00Z",
        "amount": {"currency": "EUR", "value": 10.0},
        "eventType": "PAYMENT_INBOUND",
    }
    results = [{"inputIndex": 0, "id": "new-wid"}]

    with EventRepository(tmp_path / "test.db", instance="tst") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()
        runner.process_results(results, [event], [[0]], repo, force=True)
        assert repo.get_wallet_record_id(event) == "new-wid"


# ---------------------------------------------------------------------------
# submit_batch — reversal failure logging and notification
# ---------------------------------------------------------------------------


def _make_cancellation_runner(tmp_path):
    """Helper: runner + canceled event pre-marked in DB."""
    from app.config import Config
    from app.notifier import Notifier

    cfg = MagicMock(spec=Config)
    cfg.wallet_cash_account_id = "cash"
    cfg.wallet_portfolio_account_id = "port"
    cfg.label_ids = {}
    cfg.category_strategy = "none"
    cfg.instance = "tst"
    notifier = MagicMock(spec=Notifier)
    runner = SyncRunner(cfg, notifier)
    event = {
        "id": "ev-reversal-fail",
        "eventType": "CARD_TRANSACTION",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"currency": "EUR", "value": -7.62},
    }
    return runner, notifier, event


def test_submit_batch_reversal_api_error_is_logged(tmp_path):
    """When a reversal POST returns a per-item error, the failure must be logged
    at ERROR level so it is visible in the container logs."""
    runner, _notifier, event = _make_cancellation_runner(tmp_path)

    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()
        wallet_client = MagicMock()
        wallet_client.post_records.return_value = [
            {"inputIndex": 0, "error": "account not found"}
        ]
        batch = runner.build_batch([], repo, cancellation_events=[event])

        with patch("app.sync_runner.log") as mock_log:
            runner.submit_batch(batch, wallet_client, repo, new_events=[])

    mock_log.error.assert_called()
    error_call_args = " ".join(str(a) for a in mock_log.error.call_args_list[0][0])
    assert "ev-reversal-fail" in error_call_args


def test_submit_batch_reversal_missing_result_is_logged_and_notified(tmp_path):
    """When a reversal POST result is missing (no entry for the record index),
    the failure must be logged at ERROR level and surfaced via notifier.missing_api_result."""
    runner, notifier, event = _make_cancellation_runner(tmp_path)

    with EventRepository(tmp_path / "test.db") as repo:
        repo.mark_processed(event, wallet_record_id="old-wid")
        repo.commit()
        wallet_client = MagicMock()
        # Return an empty list — no result for the reversal record
        wallet_client.post_records.return_value = []
        batch = runner.build_batch([], repo, cancellation_events=[event])

        with patch("app.sync_runner.log") as mock_log:
            counts = runner.submit_batch(batch, wallet_client, repo, new_events=[])

    mock_log.error.assert_called()
    notifier.missing_api_result.assert_called_once()
    assert counts.failed == 1
