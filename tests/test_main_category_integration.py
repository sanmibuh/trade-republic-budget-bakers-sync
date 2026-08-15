"""Integration tests for category assignment in SyncRunner.build_batch."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import Config
from app.main import SyncRunner
from app.notifier import Notifier
from app.persistence import EventRepository

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
        patch("app.main.HistoryCategorizer", return_value=mock_categorizer),
        patch("app.main.WalletClient") as mock_wallet_cls,
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
        patch("app.main.HistoryCategorizer", return_value=mock_categorizer),
        patch("app.main.WalletClient") as mock_wallet_cls,
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
        patch("app.main.HistoryCategorizer", return_value=mock_categorizer) as mock_cls,
        patch("app.main.WalletClient") as mock_wallet_cls,
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
        patch("app.main.HistoryCategorizer", return_value=mock_categorizer),
        patch("app.main.WalletClient") as mock_wallet_cls,
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
