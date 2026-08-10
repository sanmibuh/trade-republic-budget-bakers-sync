from __future__ import annotations

import pytest

from app.config import BackupConfig, Config

BASE_ENV = {
    "PHONE_NUMBER": "+34600000000",
    "PIN": "1234",
    "WALLET_API_KEY": "key",
    "WALLET_CASH_ACCOUNT_ID": "cash-id",
    "WALLET_PORTFOLIO_ACCOUNT_ID": "portfolio-id",
}

BACKUP_ENV = {
    "WALLET_API_KEY": "key",
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

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


def test_instance_defaults_to_lowercased_owner_name(monkeypatch):
    """INSTANCE not set → derived from OWNER_NAME lowercased."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OWNER_NAME", "David")
    monkeypatch.delenv("INSTANCE", raising=False)

    cfg = Config.from_env()

    assert cfg.instance == "david"


def test_instance_explicit(monkeypatch):
    """INSTANCE set explicitly is used as-is."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OWNER_NAME", "David")
    monkeypatch.setenv("INSTANCE", "david-account")

    cfg = Config.from_env()

    assert cfg.instance == "david-account"


def test_missing_required_env_raises(monkeypatch):
    """Missing a required env var raises ValueError."""
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("WALLET_API_KEY", raising=False)

    with pytest.raises(ValueError, match="WALLET_API_KEY"):
        Config.from_env()


# ---------------------------------------------------------------------------
# BackupConfig
# ---------------------------------------------------------------------------

def test_backup_config_from_env_minimal(monkeypatch):
    """BackupConfig only requires WALLET_API_KEY."""
    for key, value in BACKUP_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("PHONE_NUMBER", raising=False)
    monkeypatch.delenv("PIN", raising=False)
    monkeypatch.delenv("WALLET_CASH_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("WALLET_PORTFOLIO_ACCOUNT_ID", raising=False)

    cfg = BackupConfig.from_env()

    assert cfg.wallet_api_key == "key"


def test_backup_config_does_not_require_phone_number(monkeypatch):
    """BackupConfig must not fail when PHONE_NUMBER is absent."""
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.delenv("PHONE_NUMBER", raising=False)

    cfg = BackupConfig.from_env()  # must not raise

    assert cfg is not None


def test_backup_config_does_not_require_wallet_account_ids(monkeypatch):
    """BackupConfig must not fail when account IDs are absent."""
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.delenv("WALLET_CASH_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("WALLET_PORTFOLIO_ACCOUNT_ID", raising=False)

    cfg = BackupConfig.from_env()  # must not raise

    assert cfg is not None


def test_backup_config_missing_wallet_api_key_raises(monkeypatch):
    """BackupConfig raises ValueError when WALLET_API_KEY is missing."""
    monkeypatch.delenv("WALLET_API_KEY", raising=False)

    with pytest.raises(ValueError, match="WALLET_API_KEY"):
        BackupConfig.from_env()


def test_backup_config_owner_name_defaults_to_backup(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.delenv("OWNER_NAME", raising=False)

    cfg = BackupConfig.from_env()

    assert cfg.owner_name == "Backup"


def test_backup_config_owner_name_explicit(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.setenv("OWNER_NAME", "Eli")

    cfg = BackupConfig.from_env()

    assert cfg.owner_name == "Eli"


def test_backup_config_telegram_fields_optional(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    cfg = BackupConfig.from_env()

    assert cfg.telegram_bot_token is None
    assert cfg.telegram_chat_id is None


# ---------------------------------------------------------------------------
# ALLOW_INSECURE_SSL parsing — tested via BackupConfig (minimal config)
# ---------------------------------------------------------------------------

def test_allow_insecure_ssl_default_false(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)
    assert BackupConfig.from_env().allow_insecure_ssl is False


def test_allow_insecure_ssl_true_values(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    for value in ("true", "True", "TRUE", "1", "yes", "YES"):
        monkeypatch.setenv("ALLOW_INSECURE_SSL", value)
        assert BackupConfig.from_env().allow_insecure_ssl is True, f"failed for {value!r}"


def test_allow_insecure_ssl_false_values(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    for value in ("false", "False", "FALSE", "0", "no", "NO"):
        monkeypatch.setenv("ALLOW_INSECURE_SSL", value)
        assert BackupConfig.from_env().allow_insecure_ssl is False, f"failed for {value!r}"


def test_allow_insecure_ssl_invalid_raises(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.setenv("ALLOW_INSECURE_SSL", "maybe")
    with pytest.raises(ValueError, match="ALLOW_INSECURE_SSL"):
        BackupConfig.from_env()


# ---------------------------------------------------------------------------
# allow_insecure_ssl in Config and BackupConfig
# ---------------------------------------------------------------------------

def test_config_allow_insecure_ssl_defaults_to_false(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)

    assert Config.from_env().allow_insecure_ssl is False


def test_config_allow_insecure_ssl_true_when_set(monkeypatch):
    for key, value in BASE_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ALLOW_INSECURE_SSL", "true")

    assert Config.from_env().allow_insecure_ssl is True


def test_backup_config_allow_insecure_ssl_defaults_to_false(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)

    assert BackupConfig.from_env().allow_insecure_ssl is False


def test_backup_config_allow_insecure_ssl_true_when_set(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    monkeypatch.setenv("ALLOW_INSECURE_SSL", "true")

    assert BackupConfig.from_env().allow_insecure_ssl is True
