from __future__ import annotations

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
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[excluded_event]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.shared_db_path = tmp_path / "sync.db"
        cfg.instance = ""
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60

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

        run(cfg=cfg)

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
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.shared_db_path = tmp_path / "sync.db"
        cfg.instance = ""
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60

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
            result = run(cfg=cfg)

    assert result == 0


def test_run_returns_zero_when_no_new_events(tmp_path):
    from unittest.mock import patch

    from app.main import run

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.shared_db_path = tmp_path / "sync.db"
        cfg.instance = ""
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []

        batch = MagicMock()
        batch.records = []
        batch.excluded_count = 0
        batch.event_record_indices = []
        runner.build_batch.return_value = batch

        result = run(cfg=cfg)

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
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.shared_db_path = tmp_path / "sync.db"
        cfg.instance = ""
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []
        runner.build_batch.side_effect = boom  # simulate error during batch build

        notifier_instance = mock_notifier_cls.return_value

        with pytest.raises(RuntimeError):
            run(cfg=cfg)

    notifier_instance.error.assert_called_once_with(boom)


def test_run_logs_warning_when_sync_complete_not_sent(tmp_path):
    """sync_complete returns False → log.warning branch."""
    from unittest.mock import patch

    from app.main import run

    with (
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=[]),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.shared_db_path = tmp_path / "sync.db"
        cfg.instance = ""
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []

        batch = MagicMock()
        batch.records = []
        batch.excluded_count = 0
        batch.event_record_indices = []
        runner.build_batch.return_value = batch

        notifier_instance = mock_notifier_cls.return_value
        notifier_instance.sync_complete.return_value = False  # simulate not sent

        result = run(cfg=cfg)

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
        patch("app.main.Notifier") as mock_notifier_cls,
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.filter_by_lookback", return_value=fake_events),
        patch("app.main.WalletClient"),
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.shared_db_path = tmp_path / "sync.db"
        cfg.instance = ""
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60

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
            run(cfg=cfg)

    # sync_complete must be called (via finally) with the correct excluded count
    notifier_instance.sync_complete.assert_called_once()
    _, kwargs = notifier_instance.sync_complete.call_args
    assert kwargs["excluded"] == 3


# ---------------------------------------------------------------------------
# _prepare — shared bootstrap (plain function, no context manager)
# ---------------------------------------------------------------------------


def test_prepare_creates_data_dir_and_returns_notifier(tmp_path):
    from unittest.mock import patch

    from app.main import _prepare

    cfg = MagicMock()
    cfg.data_dir = tmp_path / "data"
    cfg.shared_db_path = tmp_path / "sync.db"
    cfg.telegram_bot_token = "tok"
    cfg.telegram_chat_id = "chat"
    cfg.owner_name = "David"

    with patch("app.main.Notifier") as mock_notifier_cls:
        notifier = _prepare(cfg)

    assert cfg.data_dir.is_dir()
    mock_notifier_cls.assert_called_once_with("tok", "chat", "David")
    assert notifier is mock_notifier_cls.return_value


# ---------------------------------------------------------------------------
# run_resync — entry point
# ---------------------------------------------------------------------------


def test_run_resync_returns_zero_on_success(tmp_path):
    from unittest.mock import patch

    from app.main import run_resync

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
    cfg.instance = ""

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.WalletClient"),
    ):
        runner = mock_runner_cls.return_value
        counts = MagicMock()
        counts.synced = 1
        counts.failed = 0
        counts.excluded = 0
        runner.resync_day.return_value = counts

        result = run_resync("2026-07-15", cfg=cfg)

    assert result == 0


def test_run_resync_invalid_date_returns_one():
    from app.main import run_resync

    cfg = MagicMock()
    result = run_resync("not-a-date", cfg=cfg)

    assert result == 1


def test_run_resync_calls_resync_day_with_date(tmp_path):
    from unittest.mock import patch

    from app.main import run_resync

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
    cfg.instance = ""

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.WalletClient"),
    ):
        runner = mock_runner_cls.return_value
        counts = MagicMock()
        counts.synced = 0
        counts.failed = 0
        counts.excluded = 0
        runner.resync_day.return_value = counts

        run_resync("2026-07-15", cfg=cfg)

    runner.resync_day.assert_called_once()
    call_args = runner.resync_day.call_args
    assert call_args[0][0] == "2026-07-15"


def test_run_resync_exception_returns_one(tmp_path):
    """run_resync must return 1 when resync_day raises."""
    from unittest.mock import patch

    from app.main import run_resync

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = tmp_path / "sync.db"
    cfg.instance = ""

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
        patch("app.main.WalletClient"),
    ):
        runner = mock_runner_cls.return_value
        runner.resync_day.side_effect = RuntimeError("boom")

        result = run_resync("2026-07-15", cfg=cfg)

    assert result == 1


def test_run_resync_rejects_datetime_string():
    """run_resync must reject full datetime strings — only YYYY-MM-DD is valid."""
    from app.main import run_resync

    cfg = MagicMock()
    result = run_resync("2026-07-15T12:00:00", cfg=cfg)

    assert result == 1
