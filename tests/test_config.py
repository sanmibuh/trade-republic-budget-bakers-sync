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


def test_read_instance_uses_instance_env_var_when_set(monkeypatch):
    """read_instance() returns INSTANCE as-is when the env var is set."""
    monkeypatch.setenv("INSTANCE", "my-account")
    monkeypatch.setenv("OWNER_NAME", "David")

    assert read_instance() == "my-account"


# ---------------------------------------------------------------------------
# InstancesConfig — YAML config file loader
# ---------------------------------------------------------------------------

_MINIMAL_YAML = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key-user1"
    wallet_cash_account_id: "cash-user1"
    wallet_portfolio_account_id: "portfolio-user1"
"""

_FULL_YAML = """\
data_dir: /custom/data
telegram_bot_token: "bot-token"
telegram_chat_id: "chat-id"
allow_insecure_ssl: true

instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key-user1"
    wallet_cash_account_id: "cash-user1"
    wallet_portfolio_account_id: "portfolio-user1"
    owner_name: "User1"
    lookback_days: 14
    dedup_ttl_days: 90
    category_strategy: history
    labels:
      BANK_TRANSACTION_INCOMING: label-abc
  - name: user2
    phone: "+34611111111"
    pin: "5678"
    wallet_api_key: "key-user2"
    wallet_cash_account_id: "cash-user2"
    wallet_portfolio_account_id: "portfolio-user2"
"""


def test_instances_config_load_minimal(tmp_path):
    """Load a minimal YAML with one instance and only required fields."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_MINIMAL_YAML)

    cfg = InstancesConfig.load(cfg_file)

    assert len(cfg.instances) == 1
    inst = cfg.instances[0]
    assert inst.name == "user1"
    assert inst.phone == "+34600000000"
    assert inst.pin == "1234"
    assert inst.wallet_api_key == "key-user1"
    assert inst.wallet_cash_account_id == "cash-user1"
    assert inst.wallet_portfolio_account_id == "portfolio-user1"


def test_instances_config_load_global_defaults(tmp_path):
    """Global fields default to safe values when not set."""
    from pathlib import Path

    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_MINIMAL_YAML)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.data_dir == Path("/app/data")
    assert cfg.telegram_bot_token is None
    assert cfg.telegram_chat_id is None
    assert cfg.allow_insecure_ssl is False


def test_instances_config_load_full_yaml(tmp_path):
    """All global and per-instance fields are parsed correctly."""
    from pathlib import Path

    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_FULL_YAML)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.data_dir == Path("/custom/data")
    assert cfg.telegram_bot_token == "bot-token"
    assert cfg.telegram_chat_id == "chat-id"
    assert cfg.allow_insecure_ssl is True
    assert len(cfg.instances) == 2

    david = cfg.instances[0]
    assert david.owner_name == "User1"
    assert david.lookback_days == 14
    assert david.dedup_ttl_days == 90
    assert david.category_strategy == "history"
    assert david.label_ids == {"BANK_TRANSACTION_INCOMING": "label-abc"}


def test_instances_config_load_instance_defaults(tmp_path):
    """Per-instance optional fields fall back to sensible defaults."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_MINIMAL_YAML)

    inst = InstancesConfig.load(cfg_file).instances[0]

    assert inst.owner_name is None  # resolved later via to_config
    assert inst.lookback_days == 7
    assert inst.dedup_ttl_days == 60
    assert inst.category_strategy == "none"
    assert inst.label_ids == {}


def test_instances_config_load_file_not_found():
    """FileNotFoundError is raised when the config file does not exist."""
    from pathlib import Path

    from app.config import InstancesConfig

    missing = Path("/nonexistent/path/instances.yml")
    with pytest.raises(FileNotFoundError):
        InstancesConfig.load(missing)


def test_instances_config_load_missing_required_instance_field(tmp_path):
    """ValueError is raised when a required instance field is missing."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="pin"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_missing_name_raises(tmp_path):
    """ValueError is raised when an instance has no name."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="name"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_invalid_category_strategy_raises(tmp_path):
    """ValueError is raised when category_strategy has an unsupported value."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
    category_strategy: invalid_strategy
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="category_strategy"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_no_instances(tmp_path):
    """ValueError is raised when the instances list is empty."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("instances: []\n")

    with pytest.raises(ValueError, match="instances"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_duplicate_instance_names(tmp_path):
    """ValueError is raised when two instances share the same name."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "k1"
    wallet_cash_account_id: "c1"
    wallet_portfolio_account_id: "p1"
  - name: user1
    phone: "+34611111111"
    pin: "5678"
    wallet_api_key: "k2"
    wallet_cash_account_id: "c2"
    wallet_portfolio_account_id: "p2"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="user1"):
        InstancesConfig.load(cfg_file)


def test_instances_config_get_instance_found(tmp_path):
    """get_instance returns the correct InstanceConfig by name."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_FULL_YAML)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.get_instance("user2").wallet_api_key == "key-user2"


def test_instances_config_get_instance_not_found(tmp_path):
    """get_instance raises ValueError for an unknown instance name."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_MINIMAL_YAML)

    cfg = InstancesConfig.load(cfg_file)
    with pytest.raises(ValueError, match="unknown"):
        cfg.get_instance("unknown")


def test_instances_config_to_config_data_dir_is_subdirectory(tmp_path):
    """to_config() sets data_dir to {root_data_dir}/{instance_name}/."""
    from pathlib import Path

    from app.config import InstancesConfig

    yaml_content = f"""\
data_dir: {tmp_path}
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.data_dir == Path(tmp_path) / "user1"


def test_instances_config_to_config_inherits_global_telegram(tmp_path):
    """to_config() copies global telegram credentials into Config."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_FULL_YAML)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.telegram_bot_token == "bot-token"
    assert cfg.telegram_chat_id == "chat-id"
    assert cfg.allow_insecure_ssl is True


def test_instances_config_to_config_instance_name_used_as_instance_field(tmp_path):
    """to_config() sets Config.instance to the instance name."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_MINIMAL_YAML)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.instance == "user1"


def test_instances_config_to_config_owner_name_defaults_to_capitalized_name(tmp_path):
    """When owner_name is not set, to_config() defaults to name.capitalize()."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_MINIMAL_YAML)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.owner_name == "User1"


def test_instances_config_to_config_explicit_owner_name(tmp_path):
    """When owner_name is set explicitly, to_config() uses it as-is."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_FULL_YAML)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.owner_name == "User1"


def test_instances_config_to_config_credentials(tmp_path):
    """to_config() propagates all TR and Wallet credentials correctly."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(_MINIMAL_YAML)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.phone_number == "+34600000000"
    assert cfg.pin == "1234"
    assert cfg.wallet_api_key == "key-user1"
    assert cfg.wallet_cash_account_id == "cash-user1"
    assert cfg.wallet_portfolio_account_id == "portfolio-user1"


# ---------------------------------------------------------------------------
# InstancesConfig.load() — robustness and validation (PR review comments)
# ---------------------------------------------------------------------------


def test_instances_config_load_yaml_root_is_list_raises(tmp_path):
    """ValueError when the YAML root is a list instead of a mapping."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("- foo\n- bar\n")

    with pytest.raises(ValueError, match="mapping"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_yaml_root_is_scalar_raises(tmp_path):
    """ValueError when the YAML root is a scalar instead of a mapping."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("just a string\n")

    with pytest.raises(ValueError, match="mapping"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_instance_entry_is_not_mapping_raises(tmp_path):
    """ValueError when an instance entry is a scalar instead of a mapping."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("instances:\n  - just_a_string\n")

    with pytest.raises(ValueError, match="mapping"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_name_with_path_separator_raises(tmp_path):
    """ValueError when instance name contains a path separator (path traversal guard)."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: "../tmp"
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="path separator"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_zero_lookback_days_raises(tmp_path):
    """ValueError when lookback_days is zero."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
    lookback_days: 0
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="lookback_days"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_negative_dedup_ttl_days_raises(tmp_path):
    """ValueError when dedup_ttl_days is negative."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
    dedup_ttl_days: -1
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="dedup_ttl_days"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_labels_not_mapping_raises(tmp_path):
    """ValueError when labels is not a mapping."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
    labels: "not-a-mapping"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="labels"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_allow_insecure_ssl_string_false(tmp_path):
    """allow_insecure_ssl: 'false' (quoted string) must be parsed as False, not True."""
    from app.config import InstancesConfig

    yaml_content = """\
allow_insecure_ssl: "false"
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.allow_insecure_ssl is False


def test_instances_config_load_allow_insecure_ssl_string_true(tmp_path):
    """allow_insecure_ssl: 'true' (quoted string) must be parsed as True."""
    from app.config import InstancesConfig

    yaml_content = """\
allow_insecure_ssl: "true"
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.allow_insecure_ssl is True


def test_instances_config_load_allow_insecure_ssl_invalid_string_raises(tmp_path):
    """allow_insecure_ssl with an unrecognised string value raises ValueError."""
    from app.config import InstancesConfig

    yaml_content = """\
allow_insecure_ssl: "maybe"
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="allow_insecure_ssl"):
        InstancesConfig.load(cfg_file)


# ---------------------------------------------------------------------------
# read_instances_config_path — INSTANCES_CONFIG env var (read in config.py)
# ---------------------------------------------------------------------------


def test_read_instances_config_path_returns_path(monkeypatch, tmp_path):
    """Returns a Path when INSTANCES_CONFIG is set."""
    from app.config import read_instances_config_path

    cfg_file = tmp_path / "instances.yml"
    monkeypatch.setenv("INSTANCES_CONFIG", str(cfg_file))

    assert read_instances_config_path() == cfg_file


def test_read_instances_config_path_missing_raises(monkeypatch):
    """ValueError when INSTANCES_CONFIG is not set."""
    from app.config import read_instances_config_path

    monkeypatch.delenv("INSTANCES_CONFIG", raising=False)

    with pytest.raises(ValueError, match="INSTANCES_CONFIG"):
        read_instances_config_path()


def test_read_instances_config_path_blank_raises(monkeypatch):
    """ValueError when INSTANCES_CONFIG is blank."""
    from app.config import read_instances_config_path

    monkeypatch.setenv("INSTANCES_CONFIG", "   ")

    with pytest.raises(ValueError, match="INSTANCES_CONFIG"):
        read_instances_config_path()


def test_instances_config_load_name_is_integer_coerced_to_string(tmp_path):
    """name: 123 (YAML integer) is coerced to the string '123', not a TypeError."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: 123
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.instances[0].name == "123"


def test_instances_config_load_name_with_leading_trailing_whitespace_stripped(tmp_path):
    """Instance names with surrounding whitespace are stripped to avoid surprising dirs."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: "  user1  "
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.instances[0].name == "user1"


def test_instances_config_load_lookback_days_non_integer_raises(tmp_path):
    """A non-integer lookback_days value raises ValueError with instance/field context."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
    lookback_days: seven
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="lookback_days"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_dedup_ttl_days_non_integer_raises(tmp_path):
    """A non-integer dedup_ttl_days value raises ValueError with instance/field context."""
    from app.config import InstancesConfig

    yaml_content = """\
instances:
  - name: user1
    phone: "+34600000000"
    pin: "1234"
    wallet_api_key: "key"
    wallet_cash_account_id: "cash"
    wallet_portfolio_account_id: "portfolio"
    dedup_ttl_days: sixty
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="dedup_ttl_days"):
        InstancesConfig.load(cfg_file)
