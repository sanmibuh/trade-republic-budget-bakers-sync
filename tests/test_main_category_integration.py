"""Integration tests for category assignment in SyncRunner.build_batch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.categorizer import HistoryCategorizer
from app.config import Config
from app.notifier import Notifier
from app.persistence import EventRepository
from app.sync_runner import SyncRunner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(**overrides) -> Config:
    defaults: dict = {
        "owner_name": "Test",
        "phone_number": "+49123",
        "pin": "1234",
        "wallet_api_key": "key",
        "wallet_cash_account_id": "cash-1",
        "wallet_portfolio_account_id": "port-1",
        "telegram_bot_token": None,
        "telegram_chat_id": None,
        "lookback_days": 7,
        "dedup_ttl_days": 60,
        "data_dir": MagicMock(),
        "instance": "test",
        "allow_insecure_ssl": False,
        "label_ids": {},
        "category_strategy": "none",
    }
    defaults.update(overrides)
    return Config(**defaults)


def _make_notifier() -> Notifier:
    n = MagicMock(spec=Notifier)
    n.unknown_event_type = MagicMock()
    return n


def _make_repo() -> MagicMock:
    repo = MagicMock(spec=EventRepository)
    repo.mark_processed = MagicMock()
    return repo


def _card_event(note: str = "Starbucks", amount: float = -5.0) -> dict:
    return {
        "eventType": "CARD_TRANSACTION",
        "amount": {"value": amount},
        "timestamp": "2026-08-15T12:00:00+00:00",
        "title": note,
    }


# ---------------------------------------------------------------------------
# category_strategy = "none" (default) — no categorization
# ---------------------------------------------------------------------------


def test_build_batch_strategy_none_does_not_assign_category():
    cfg = _make_cfg(category_strategy="none")
    runner = SyncRunner(cfg, _make_notifier())
    repo = _make_repo()

    batch = runner.build_batch([_card_event()], repo)

    assert len(batch.records) == 1
    assert "categoryId" not in batch.records[0]


# ---------------------------------------------------------------------------
# category_strategy = "history" — category assigned from HistoryCategorizer
# ---------------------------------------------------------------------------


def test_build_batch_strategy_history_assigns_category():
    cfg = _make_cfg(category_strategy="history")
    runner = SyncRunner(cfg, _make_notifier())
    repo = _make_repo()

    mock_categorizer = MagicMock()
    mock_categorizer.get_category_id.return_value = "cat-food"

    with (
        patch("app.sync_runner.HistoryCategorizer", return_value=mock_categorizer),
        patch("app.sync_runner.WalletClient") as mock_wallet_cls,
    ):
        batch = runner.build_batch(
            [_card_event("Starbucks")],
            repo,
            wallet_client=mock_wallet_cls.return_value,
        )

    assert batch.records[0]["categoryId"] == "cat-food"
    mock_categorizer.get_category_id.assert_called_once_with("Starbucks")


def test_build_batch_strategy_history_no_match_omits_category():
    cfg = _make_cfg(category_strategy="history")
    runner = SyncRunner(cfg, _make_notifier())
    repo = _make_repo()

    mock_categorizer = MagicMock()
    mock_categorizer.get_category_id.return_value = None

    with (
        patch("app.sync_runner.HistoryCategorizer", return_value=mock_categorizer),
        patch("app.sync_runner.WalletClient") as mock_wallet_cls,
    ):
        batch = runner.build_batch(
            [_card_event("NewMerchant")],
            repo,
            wallet_client=mock_wallet_cls.return_value,
        )

    assert "categoryId" not in batch.records[0]


def test_build_batch_strategy_history_categorizer_reused_across_events():
    """A single HistoryCategorizer is constructed once per build_batch call."""
    cfg = _make_cfg(category_strategy="history")
    runner = SyncRunner(cfg, _make_notifier())
    repo = _make_repo()

    mock_categorizer = MagicMock()
    mock_categorizer.get_category_id.return_value = "cat-food"

    events = [_card_event("Starbucks"), _card_event("Costa")]

    with (
        patch(
            "app.sync_runner.HistoryCategorizer", return_value=mock_categorizer
        ) as mock_cls,
        patch("app.sync_runner.WalletClient") as mock_wallet_cls,
    ):
        runner.build_batch(events, repo, wallet_client=mock_wallet_cls.return_value)

    mock_cls.assert_called_once()
    assert mock_categorizer.get_category_id.call_count == 2


def test_build_batch_strategy_history_zero_amount_not_categorized():
    """Zero-amount events are excluded before categorization."""
    cfg = _make_cfg(category_strategy="history")
    runner = SyncRunner(cfg, _make_notifier())
    repo = _make_repo()

    mock_categorizer = MagicMock()
    mock_categorizer.get_category_id.return_value = "cat-food"

    with (
        patch("app.sync_runner.HistoryCategorizer", return_value=mock_categorizer),
        patch("app.sync_runner.WalletClient") as mock_wallet_cls,
    ):
        batch = runner.build_batch(
            [_card_event("Zero", amount=0.0)],
            repo,
            wallet_client=mock_wallet_cls.return_value,
        )

    assert batch.records == []
    mock_categorizer.get_category_id.assert_not_called()


# ---------------------------------------------------------------------------
# Config: category_strategy env var validation
# ---------------------------------------------------------------------------


def test_config_category_strategy_default_is_none(monkeypatch):
    for var in (
        "PHONE_NUMBER",
        "PIN",
        "WALLET_API_KEY",
        "WALLET_CASH_ACCOUNT_ID",
        "WALLET_PORTFOLIO_ACCOUNT_ID",
    ):
        monkeypatch.setenv(var, "x")
    monkeypatch.delenv("CATEGORY_STRATEGY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)

    cfg = Config.from_env()
    assert cfg.category_strategy == "none"


def test_config_category_strategy_history(monkeypatch):
    for var in (
        "PHONE_NUMBER",
        "PIN",
        "WALLET_API_KEY",
        "WALLET_CASH_ACCOUNT_ID",
        "WALLET_PORTFOLIO_ACCOUNT_ID",
    ):
        monkeypatch.setenv(var, "x")
    monkeypatch.setenv("CATEGORY_STRATEGY", "history")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)

    cfg = Config.from_env()
    assert cfg.category_strategy == "history"


def test_config_category_strategy_invalid_raises(monkeypatch):
    for var in (
        "PHONE_NUMBER",
        "PIN",
        "WALLET_API_KEY",
        "WALLET_CASH_ACCOUNT_ID",
        "WALLET_PORTFOLIO_ACCOUNT_ID",
    ):
        monkeypatch.setenv(var, "x")
    monkeypatch.setenv("CATEGORY_STRATEGY", "invalid_value")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)

    with pytest.raises(ValueError, match="CATEGORY_STRATEGY"):
        Config.from_env()


# ---------------------------------------------------------------------------
# SyncRunner._retry_category_failures
# ---------------------------------------------------------------------------


def _make_runner_for_retry() -> SyncRunner:
    return SyncRunner(_make_cfg(category_strategy="history"), _make_notifier())


def _make_categorizer_mock() -> MagicMock:
    cat = MagicMock(spec=HistoryCategorizer)
    return cat


def test_retry_no_failures_returns_original_results():
    runner = _make_runner_for_retry()
    wallet = MagicMock()
    records = [{"accountId": "x", "categoryId": "cat-1"}]
    results = [{"inputIndex": 0, "id": "r-1", "success": True}]

    out = runner._retry_category_failures(records, results, wallet, categorizer=None)

    assert out == results
    wallet.post_records.assert_not_called()


def test_retry_no_categorizer_returns_original_results():
    runner = _make_runner_for_retry()
    wallet = MagicMock()
    records = [{"accountId": "x", "categoryId": "cat-1"}]
    results = [{"inputIndex": 0, "error": "invalid category"}]

    out = runner._retry_category_failures(records, results, wallet, categorizer=None)

    assert out == results
    wallet.post_records.assert_not_called()


def test_retry_failed_record_without_category_id_not_retried():
    """Errors on records that never had a categoryId are not retried."""
    runner = _make_runner_for_retry()
    wallet = MagicMock()
    categorizer = _make_categorizer_mock()
    records = [{"accountId": "x"}]  # no categoryId
    results = [{"inputIndex": 0, "error": "some error"}]

    out = runner._retry_category_failures(
        records, results, wallet, categorizer=categorizer
    )

    assert out == results
    wallet.post_records.assert_not_called()
    categorizer.invalidate_cache.assert_not_called()


def test_retry_failed_categorized_record_retried_without_category():
    """A failed record with a categoryId is retried once without it."""
    runner = _make_runner_for_retry()
    wallet = MagicMock()
    wallet.post_records.return_value = [
        {"inputIndex": 0, "id": "r-retry", "success": True}
    ]
    categorizer = _make_categorizer_mock()

    records = [{"accountId": "x", "categoryId": "cat-1", "note": "Coffee"}]
    results = [{"inputIndex": 0, "error": "categoryId not found"}]

    out = runner._retry_category_failures(
        records, results, wallet, categorizer=categorizer
    )

    categorizer.invalidate_cache.assert_called_once()
    wallet.post_records.assert_called_once_with([{"accountId": "x", "note": "Coffee"}])
    assert out[0]["id"] == "r-retry"
    assert out[0]["inputIndex"] == 0


def test_retry_cache_invalidated_on_category_failure():
    runner = _make_runner_for_retry()
    wallet = MagicMock()
    wallet.post_records.return_value = [{"inputIndex": 0, "id": "r-2", "success": True}]
    categorizer = _make_categorizer_mock()

    records = [{"categoryId": "cat-bad"}]
    results = [{"inputIndex": 0, "error": "bad category"}]

    runner._retry_category_failures(records, results, wallet, categorizer=categorizer)

    categorizer.invalidate_cache.assert_called_once()


def test_retry_only_failed_categorized_records_are_retried():
    """Successful records and uncategorized failures are left untouched."""
    runner = _make_runner_for_retry()
    wallet = MagicMock()
    wallet.post_records.return_value = [
        {"inputIndex": 0, "id": "r-retry", "success": True}
    ]
    categorizer = _make_categorizer_mock()

    records = [
        {"accountId": "a", "categoryId": "cat-bad"},  # 0 — failed, has category → retry
        {"accountId": "b", "categoryId": "cat-ok"},  # 1 — success, has category → keep
        {"accountId": "c"},  # 2 — failed, no category → keep error
    ]
    results = [
        {"inputIndex": 0, "error": "bad category"},
        {"inputIndex": 1, "id": "r-1", "success": True},
        {"inputIndex": 2, "error": "other error"},
    ]

    out = runner._retry_category_failures(
        records, results, wallet, categorizer=categorizer
    )

    wallet.post_records.assert_called_once_with([{"accountId": "a"}])
    out_by_index = {r["inputIndex"]: r for r in out}
    assert out_by_index[0]["id"] == "r-retry"
    assert out_by_index[1]["id"] == "r-1"
    assert out_by_index[2]["error"] == "other error"


def test_retry_if_retry_also_fails_retry_error_is_returned():
    """When the retry itself fails, the retry error result is used (not the original)."""
    runner = _make_runner_for_retry()
    wallet = MagicMock()
    wallet.post_records.return_value = [{"inputIndex": 0, "error": "still failing"}]
    categorizer = _make_categorizer_mock()

    records = [{"categoryId": "cat-bad", "note": "Coffee"}]
    results = [{"inputIndex": 0, "error": "bad category"}]

    out = runner._retry_category_failures(
        records, results, wallet, categorizer=categorizer
    )

    assert out[0]["error"] == "still failing"
    assert out[0]["inputIndex"] == 0


def test_build_batch_exposes_categorizer_in_batch():
    """build_batch stores the HistoryCategorizer in the returned _Batch."""
    cfg = _make_cfg(category_strategy="history")
    runner = SyncRunner(cfg, _make_notifier())
    repo = _make_repo()

    mock_categorizer = MagicMock(spec=HistoryCategorizer)
    mock_categorizer.get_category_id.return_value = None

    with (
        patch("app.sync_runner.HistoryCategorizer", return_value=mock_categorizer),
        patch("app.sync_runner.WalletClient") as mock_wallet_cls,
    ):
        batch = runner.build_batch(
            [_card_event()], repo, wallet_client=mock_wallet_cls.return_value
        )

    assert batch.categorizer is mock_categorizer


def test_build_batch_strategy_none_categorizer_is_none():
    cfg = _make_cfg(category_strategy="none")
    runner = SyncRunner(cfg, _make_notifier())
    repo = _make_repo()
    batch = runner.build_batch([_card_event()], repo)
    assert batch.categorizer is None
