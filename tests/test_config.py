from __future__ import annotations

import pytest

from app.config import Config

BASE_ENV = {
    "PHONE_NUMBER": "+34600000000",
    "PIN": "1234",
    "WALLET_API_KEY": "key",
    "WALLET_CASH_ACCOUNT_ID": "cash-id",
    "WALLET_PORTFOLIO_ACCOUNT_ID": "portfolio-id",
}


def test_owner_name_default_is_backup(monkeypatch):
    """OWNER_NAME not set → defaults to 'Backup' (for the backup service)."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("OWNER_NAME", raising=False)

    cfg = Config.from_env()

    assert cfg.owner_name == "Backup"


def test_owner_name_explicit(monkeypatch):
    """OWNER_NAME set explicitly is used as-is."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OWNER_NAME", "David")

    cfg = Config.from_env()

    assert cfg.owner_name == "David"


def test_missing_required_env_raises(monkeypatch):
    """Missing a required env var raises ValueError."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("WALLET_API_KEY", raising=False)

    with pytest.raises(ValueError, match="WALLET_API_KEY"):
        Config.from_env()
