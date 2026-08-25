from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.persistence import EventRepository

# ---------------------------------------------------------------------------
# Module-level DB initialisation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _init_test_dbs(tmp_path):
    """Pre-initialise DB paths used by tests in this module.

    Tests call ``main.run()`` / ``main.run_resync()`` directly (bypassing the
    CLI entry point), so ``init_db`` must be called explicitly here.
    """
    from app.persistence import init_db

    init_db(tmp_path / "sync.db")


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
        def fake_build_batch(
            new_events, repo, *, wallet_client=None, cancellation_events=None
        ):
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

        runner.submit_batch.side_effect = fake_submit_batch

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
        runner.submit_batch.side_effect = RuntimeError("wallet down")

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


# ---------------------------------------------------------------------------
# run_check_day — dry-run check for a specific day
# ---------------------------------------------------------------------------


def _make_event(event_id: str, timestamp: str, amount: str) -> dict:
    return {
        "id": event_id,
        "timestamp": timestamp,
        "eventType": "PAYMENT_INBOUND",
        "amount": amount,
        "title": "Test",
    }


def test_run_check_day_invalid_date_returns_none():
    """run_check_day returns None on an invalid date string."""
    from app.main import run_check_day

    cfg = MagicMock()
    result = run_check_day("not-a-date", cfg=cfg)
    assert result is None


def test_run_check_day_rejects_datetime_string():
    """run_check_day returns None for a full datetime string."""
    from app.main import run_check_day

    cfg = MagicMock()
    result = run_check_day("2026-08-20T12:00:00", cfg=cfg)
    assert result is None


def test_run_check_day_classifies_processed_and_not_processed(tmp_path):
    """run_check_day must classify events as processed / not_processed via DB lookup."""
    from unittest.mock import patch

    from app.main import run_check_day
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    # Pre-mark one event as processed
    event_processed = _make_event("ev-proc", "2026-08-20T08:00:00+00:00", "2.34")
    event_new = _make_event("ev-new", "2026-08-20T21:00:00+00:00", "-100.00")

    with EventRepository(db_path, instance="test") as repo:
        repo.mark_processed(event_processed, wallet_record_id="wid-1")
        repo.commit()

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = db_path
    cfg.instance = "test"

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = [event_processed, event_new]

        result = run_check_day("2026-08-20", cfg=cfg)

    assert result is not None
    assert result.date == "2026-08-20"
    assert len(result.processed) == 1
    assert result.processed[0].event_id == "ev-proc"
    assert len(result.not_processed) == 1
    assert result.not_processed[0].event_id == "ev-new"


def test_run_check_day_no_writes_to_db(tmp_path):
    """run_check_day must not insert anything into the processed_events table."""
    from unittest.mock import patch

    from app.main import run_check_day
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    event = _make_event("ev-only", "2026-08-20T09:00:00+00:00", "5.00")

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = db_path
    cfg.instance = "test"

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = [event]

        run_check_day("2026-08-20", cfg=cfg)

    # The event should NOT be in the DB after a check-day
    with EventRepository(db_path, instance="test") as repo:
        assert not repo.is_processed("ev-only")


def test_run_check_day_event_summary_fields(tmp_path):
    """EventSummary must expose event_id, timestamp, amount, currency, description."""
    from unittest.mock import patch

    from app.main import run_check_day
    from app.persistence import init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    event = {
        "id": "ev-fields",
        "timestamp": "2026-08-20T14:05:00+00:00",
        "eventType": "PAYMENT_INBOUND",
        "amount": {"value": 12.0, "currency": "EUR"},
        "title": "Card payment",
    }

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = db_path
    cfg.instance = "test"

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = [event]

        result = run_check_day("2026-08-20", cfg=cfg)

    assert result is not None
    assert len(result.not_processed) == 1
    summary = result.not_processed[0]
    assert summary.event_id == "ev-fields"
    assert "2026-08-20" in summary.timestamp
    assert summary.description  # non-empty
    # amount and currency come from the raw event amount field
    assert summary.amount is not None


def test_run_check_day_empty_day_returns_empty_lists(tmp_path):
    """run_check_day returns empty lists when there are no events for the day."""
    from unittest.mock import patch

    from app.main import run_check_day
    from app.persistence import init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = db_path
    cfg.instance = "test"

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = []

        result = run_check_day("2026-08-20", cfg=cfg)

    assert result is not None
    assert result.processed == []
    assert result.not_processed == []


def test_run_check_day_event_summary_includes_status(tmp_path):
    """EventSummary must expose the TR status of the event."""
    from unittest.mock import patch

    from app.main import run_check_day
    from app.persistence import init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    event = {
        "id": "ev-status",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "eventType": "CARD_TRANSACTION",
        "title": "Amazon",
        "status": "CANCELED",
        "amount": {"value": -7.62, "currency": "EUR"},
    }

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = db_path
    cfg.instance = "test"

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = [event]

        result = run_check_day("2026-08-24", cfg=cfg)

    assert result is not None
    assert len(result.not_processed) == 1
    assert result.not_processed[0].status == "CANCELED"


def test_run_check_day_event_summary_status_empty_when_absent(tmp_path):
    """EventSummary.status must be empty string when the event has no status field."""
    from unittest.mock import patch

    from app.main import run_check_day
    from app.persistence import init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    event = {
        "id": "ev-no-status",
        "timestamp": "2026-08-24T05:43:38.971+0000",
        "eventType": "PAYMENT_INBOUND",
        "title": "Transfer",
        "amount": {"value": 100.0, "currency": "EUR"},
    }

    cfg = MagicMock()
    cfg.data_dir = tmp_path
    cfg.shared_db_path = db_path
    cfg.instance = "test"

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = [event]

        result = run_check_day("2026-08-24", cfg=cfg)

    assert result is not None
    assert result.not_processed[0].status == ""


# ---------------------------------------------------------------------------
# run() — cancellation events are detected and passed to build_batch
# ---------------------------------------------------------------------------


def test_run_passes_cancellation_events_to_build_batch(tmp_path):
    """run() must call filter_cancellation_pending on recent events and pass
    the result to build_batch as cancellation_events."""
    from unittest.mock import patch

    from app.main import run

    normal_event = {
        "id": "ev-normal",
        "timestamp": "2026-08-24T08:00:00Z",
        "amount": {"currency": "EUR", "value": 10.0},
        "eventType": "PAYMENT_INBOUND",
    }
    canceled_event = {
        "id": "ev-canceled",
        "timestamp": "2026-08-24T05:00:00Z",
        "amount": {"currency": "EUR", "value": -7.62},
        "eventType": "CARD_TRANSACTION",
        "status": "CANCELED",
    }

    with (
        patch("app.main.Notifier"),
        patch("app.main.SyncRunner") as mock_runner_cls,
    ):
        cfg = MagicMock()
        cfg.data_dir = tmp_path
        cfg.shared_db_path = tmp_path / "sync.db"
        cfg.instance = "tst"
        cfg.lookback_days = 7
        cfg.dedup_ttl_days = 60

        runner = mock_runner_cls.return_value
        runner.fetch_events.return_value = [normal_event, canceled_event]

        batch = MagicMock()
        batch.records = []
        batch.excluded_count = 1
        runner.build_batch.return_value = batch

        mock_counts = MagicMock()
        mock_counts.synced = 0
        mock_counts.failed = 0
        mock_counts.excluded = 1
        runner.submit_batch.return_value = mock_counts

        with (
            patch(
                "app.main.filter_by_lookback",
                return_value=[normal_event, canceled_event],
            ),
            patch("app.main.WalletClient"),
        ):
            # Pre-seed the DB so the canceled event has a wallet_record_id
            from app.persistence import EventRepository

            with EventRepository(tmp_path / "sync.db", instance="tst") as repo:
                repo.mark_processed(canceled_event, wallet_record_id="old-wid")
                repo.commit()

            run(cfg=cfg)

    # build_batch must have been called with cancellation_events containing the canceled event
    call_kwargs = runner.build_batch.call_args.kwargs
    assert "cancellation_events" in call_kwargs
    assert any(e["id"] == "ev-canceled" for e in call_kwargs["cancellation_events"])
