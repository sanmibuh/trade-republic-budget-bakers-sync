from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from app.persistence import EventRepository

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

        # Simulate _submit_batch committing the repo when records is empty
        def fake_submit_batch(batch, wallet_client, repo, *, new_events):
            from app.sync_runner import _SyncCounts

            if not batch.records:
                repo.commit()
            return _SyncCounts(excluded=batch.excluded_count)

        runner._submit_batch.side_effect = fake_submit_batch

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
    from datetime import UTC, datetime
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


def test_sync_complete_receives_excluded_count_even_when_post_fails(tmp_path):
    """excluded_count must be reported in sync_complete even if _submit_batch raises.

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
        patch("app.main.WalletClient"),
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

        # _submit_batch raises after excluded_count has been stamped onto counts
        runner._submit_batch.side_effect = RuntimeError("wallet down")

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
        patch("app.main.setup_logging", return_value=[]) as mock_setup,
        patch("app.main.Notifier") as mock_notifier_cls,
        _prepare(cfg) as notifier,
    ):
        assert cfg.data_dir.is_dir()
        mock_configure.assert_called_once_with(allow_insecure_ssl=True)
        mock_setup.assert_called_once_with(cfg.data_dir)
        mock_notifier_cls.assert_called_once_with("tok", "chat", "David")
        assert notifier is mock_notifier_cls.return_value


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


def test_run_resync_rejects_datetime_string():
    """run_resync must reject full datetime strings — only YYYY-MM-DD is valid."""
    from app.main import run_resync

    result = run_resync("2026-07-15T12:00:00")

    assert result == 1


# ---------------------------------------------------------------------------
# Handler lifecycle — _prepare must clean up logging handlers after each run
# ---------------------------------------------------------------------------


def test_run_removes_logging_handlers_after_completion(tmp_path):
    """Handlers added by _prepare must be removed once run() finishes.

    Ensures that repeated calls (as in the bot) do not accumulate file handlers
    on the root logger, preventing cross-instance log contamination.
    """
    import logging
    from unittest.mock import patch

    from app.main import run

    root = logging.getLogger()
    handlers_before = set(root.handlers)

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
        patch("app.main.http_client"),
    ):
        cfg = mock_cfg_cls.return_value
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []
        runner._notify_fetch_summary.return_value = 0
        runner.build_batch.return_value = MagicMock(excluded_count=0)
        runner._submit_batch.return_value = MagicMock(synced=0, excluded=0, failed=0)

        run()

    assert set(root.handlers) == handlers_before, (
        "root logger must have the same handlers after run() as before"
    )


def test_run_removes_logging_handlers_on_exception(tmp_path):
    """Handlers added by _prepare must be removed even when run() raises."""
    import logging
    from unittest.mock import patch

    from app.main import run

    root = logging.getLogger()
    handlers_before = set(root.handlers)

    with (
        patch("app.main.Config.from_env") as mock_cfg_cls,
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
        patch("app.main.http_client"),
    ):
        cfg = mock_cfg_cls.return_value
        cfg.data_dir = tmp_path
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60
        runner = mock_runner_cls.return_value
        runner.fetch_events.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            run()

    assert set(root.handlers) == handlers_before, (
        "root logger must have the same handlers after run() raises"
    )


def test_setup_logging_returns_added_handlers(tmp_path):
    """setup_logging must return the list of handlers it registered."""
    import logging

    from app.logging_setup import setup_logging

    root = logging.getLogger()
    before = set(root.handlers)
    try:
        added = setup_logging(tmp_path)
        assert isinstance(added, list)
        assert len(added) >= 1
        for h in added:
            assert h in root.handlers
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()


# ---------------------------------------------------------------------------
# _prepare — serializes concurrent in-process runs
# ---------------------------------------------------------------------------


def test_prepare_serializes_concurrent_runs(tmp_path):
    """Two concurrent _prepare calls must be serialized so handlers never overlap.

    The test verifies that the second call only enters _prepare after the first
    has fully exited.  Without a lock, the order would be
    [run1_start, run2_start, run1_end]; with a lock it must be
    [run1_start, run1_end, run2_start].
    """
    import time
    from unittest.mock import patch

    from app.main import _prepare

    inside_first = threading.Event()
    second_ready = threading.Event()
    order: list[str] = []

    def _make_cfg(subdir: str) -> MagicMock:
        cfg = MagicMock()
        cfg.data_dir = tmp_path / subdir
        cfg.allow_insecure_ssl = False
        cfg.telegram_bot_token = None
        cfg.telegram_chat_id = None
        cfg.owner_name = subdir
        return cfg

    def run1() -> None:
        with (
            patch("app.main.setup_logging", return_value=[]),
            patch("app.main.http_client"),
            patch("app.main.Notifier"),
            _prepare(_make_cfg("a")),
        ):
            order.append("run1_start")
            inside_first.set()  # signal: run1 is inside _prepare holding the lock
            second_ready.wait()  # wait until run2 is about to call _prepare
            time.sleep(0.05)  # stay inside a bit so run2 is definitely waiting
            order.append("run1_end")

    def run2() -> None:
        inside_first.wait()  # wait until run1 holds the lock
        second_ready.set()
        with (
            patch("app.main.setup_logging", return_value=[]),
            patch("app.main.http_client"),
            patch("app.main.Notifier"),
            _prepare(_make_cfg("b")),
        ):
            order.append("run2_start")

    t1 = threading.Thread(target=run1)
    t2 = threading.Thread(target=run2)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert order == ["run1_start", "run1_end", "run2_start"], (
        f"runs were not serialized; got order {order}"
    )


def test_prepare_http_configure_is_inside_lock(tmp_path):
    """http_client.configure() must be called while _RUN_LOCK is held.

    If configure() runs before the lock is acquired, a second concurrent run
    could overwrite the SSL policy of the first run after the lock is taken.
    This test holds the lock externally, starts _prepare in a thread, and
    asserts that configure() is NOT called until the lock is released.
    """
    import time
    from unittest.mock import patch

    from app.main import _RUN_LOCK, _prepare

    configure_called = threading.Event()

    cfg = MagicMock()
    cfg.data_dir = tmp_path / "inst"
    cfg.allow_insecure_ssl = False
    cfg.telegram_bot_token = None
    cfg.telegram_chat_id = None
    cfg.owner_name = "test"

    def _run() -> None:
        with (
            patch("app.main.setup_logging", return_value=[]),
            patch("app.main.http_client") as mock_http,
            patch("app.main.Notifier"),
        ):
            mock_http.configure.side_effect = lambda **_: configure_called.set()
            with _prepare(cfg):
                pass

    with _RUN_LOCK:
        t = threading.Thread(target=_run)
        t.start()
        time.sleep(
            0.05
        )  # give the thread a chance to race if the lock is not respected
        assert not configure_called.is_set(), (
            "http_client.configure() was called before _RUN_LOCK was released"
        )

    t.join(timeout=5)
    assert configure_called.is_set(), "http_client.configure() was never called"
