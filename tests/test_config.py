from __future__ import annotations

import pytest

from app.config import BackupConfig, Config, has_valid_session, read_instance

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
        assert BackupConfig.from_env().allow_insecure_ssl is True, (
            f"failed for {value!r}"
        )


def test_allow_insecure_ssl_false_values(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "key")
    for value in ("false", "False", "FALSE", "0", "no", "NO"):
        monkeypatch.setenv("ALLOW_INSECURE_SSL", value)
        assert BackupConfig.from_env().allow_insecure_ssl is False, (
            f"failed for {value!r}"
        )


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


# ---------------------------------------------------------------------------
# has_valid_session
# ---------------------------------------------------------------------------

_FAR_FUTURE = 9_999_999_999
_PAST = 1_000_000_000


def _write_cookies(path, expires: int, name: str = "tr_session") -> None:
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".api.traderepublic.com\tTRUE\t/\tTRUE\t{expires}\t{name}\tval\n"
    )


def test_has_valid_session_false_when_no_file(tmp_path):
    assert has_valid_session(tmp_path) is False


def test_has_valid_session_false_when_file_empty(tmp_path):
    (tmp_path / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    assert has_valid_session(tmp_path) is False


def test_has_valid_session_false_when_all_expired(tmp_path):
    _write_cookies(tmp_path / "cookies.txt", _PAST)
    assert has_valid_session(tmp_path) is False


def test_has_valid_session_true_when_valid_cookie(tmp_path):
    _write_cookies(tmp_path / "cookies.txt", _FAR_FUTURE)
    assert has_valid_session(tmp_path) is True


def test_has_valid_session_true_when_mixed_cookies(tmp_path):
    (tmp_path / "cookies.txt").write_text(
        "# Netscape HTTP Cookie File\n"
        f".api.traderepublic.com\tTRUE\t/\tTRUE\t{_PAST}\told\texpired\n"
        f".api.traderepublic.com\tTRUE\t/\tTRUE\t{_FAR_FUTURE}\ttr_session\tvalid\n"
    )
    assert has_valid_session(tmp_path) is True


def test_has_valid_session_false_when_file_corrupt(tmp_path):
    (tmp_path / "cookies.txt").write_text("not a cookie jar at all\x00\xff")
    assert has_valid_session(tmp_path) is False


# ---------------------------------------------------------------------------
# read_data_dir
# ---------------------------------------------------------------------------


def test_read_data_dir_returns_env_value(monkeypatch):
    """DATA_DIR env var is reflected in the returned Path."""
    from app.config import read_data_dir

    monkeypatch.setenv("DATA_DIR", "/custom/data")
    assert read_data_dir() == __import__("pathlib").Path("/custom/data")


def test_read_data_dir_defaults_to_app_data(monkeypatch):
    """When DATA_DIR is unset, the default /app/data is returned."""
    from app.config import read_data_dir

    monkeypatch.delenv("DATA_DIR", raising=False)
    assert read_data_dir() == __import__("pathlib").Path("/app/data")


# ---------------------------------------------------------------------------
# Config.from_env — required / positive-int env var parsing
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


def test_required_env_present(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "my-key")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)
    cfg = BackupConfig.from_env()
    assert cfg.wallet_api_key == "my-key"


def test_required_env_missing(monkeypatch):
    monkeypatch.delenv("WALLET_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)
    with pytest.raises(ValueError, match="WALLET_API_KEY"):
        BackupConfig.from_env()


def test_required_env_blank(monkeypatch):
    monkeypatch.setenv("WALLET_API_KEY", "   ")
    monkeypatch.delenv("ALLOW_INSECURE_SSL", raising=False)
    with pytest.raises(ValueError, match="WALLET_API_KEY"):
        BackupConfig.from_env()


def test_positive_int_env_uses_default(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.delenv("LOOKBACK_DAYS", raising=False)
    assert Config.from_env().lookback_days == 7


def test_positive_int_env_reads_env(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LOOKBACK_DAYS", "14")
    assert Config.from_env().lookback_days == 14


def test_positive_int_env_rejects_non_integer(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LOOKBACK_DAYS", "abc")
    with pytest.raises(ValueError, match="integer"):
        Config.from_env()


def test_positive_int_env_rejects_zero(monkeypatch):
    _set_sync_env(monkeypatch)
    monkeypatch.setenv("LOOKBACK_DAYS", "0")
    with pytest.raises(ValueError, match="positive"):
        Config.from_env()


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
# Config.from_env().label_ids — LABEL_* env var parsing
# ---------------------------------------------------------------------------


def test_read_label_ids_returns_empty_when_no_env(monkeypatch):
    from app.config import LABELABLE_EVENT_TYPES

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


def test_read_instance_falls_back_to_default_owner_name(monkeypatch):
    """read_instance() with no INSTANCE and no OWNER_NAME returns lowercased default."""
    monkeypatch.delenv("INSTANCE", raising=False)
    monkeypatch.delenv("OWNER_NAME", raising=False)

    result = read_instance()

    assert result == "backup"
