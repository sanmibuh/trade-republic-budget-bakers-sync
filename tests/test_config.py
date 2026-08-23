from __future__ import annotations

import pytest

from app.config import (
    BackupConfig,
    InstanceConfig,
    InstancesConfig,
    SyncConfig,
    has_valid_session,
)

# ---------------------------------------------------------------------------
# BackupConfig.from_instances_yaml
# ---------------------------------------------------------------------------


def _make_instances_yaml(
    wallet_api_key: str = "yamlkey",
    telegram_bot_token: str | None = "yamltoken",
    telegram_chat_id: str | None = "yamlchat",
    allow_insecure_ssl: bool = False,
    data_dir: str = "/app/data",
    empty: bool = False,
) -> InstancesConfig:
    from pathlib import Path

    instances = (
        []
        if empty
        else [
            InstanceConfig(
                name="user1",
                phone="+34600000000",
                pin="1234",
                wallet_api_key=wallet_api_key,
                wallet_cash_account_id="cash1",
                wallet_portfolio_account_id="port1",
                owner_name=None,
                lookback_days=7,
                dedup_ttl_days=60,
                label_ids={},
                category_strategy="none",
            )
        ]
    )
    return InstancesConfig(
        sync=SyncConfig(instances=instances),
        data_dir=Path(data_dir),
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        allow_insecure_ssl=allow_insecure_ssl,
    )


def test_backup_config_from_instances_yaml_uses_first_instance_wallet_key():
    yaml = _make_instances_yaml(wallet_api_key="yamlkey")
    cfg = BackupConfig.from_instances_yaml(yaml)
    assert cfg is not None
    assert cfg.wallet_api_key == "yamlkey"


def test_backup_config_from_instances_yaml_empty_instances_returns_none():
    yaml = _make_instances_yaml(empty=True)
    assert BackupConfig.from_instances_yaml(yaml) is None


def test_backup_config_from_instances_yaml_data_dir_is_instances_data_dir():
    """BackupConfig.data_dir must equal instances_yaml.data_dir (the root data dir)."""
    yaml = _make_instances_yaml(data_dir="/app/data")
    cfg = BackupConfig.from_instances_yaml(yaml)
    assert cfg is not None
    assert str(cfg.data_dir) == "/app/data"


def test_backup_config_from_instances_yaml_uses_yaml_telegram_creds():
    yaml = _make_instances_yaml(telegram_bot_token="tok", telegram_chat_id="chat")
    cfg = BackupConfig.from_instances_yaml(yaml)
    assert cfg is not None
    assert cfg.telegram_bot_token == "tok"
    assert cfg.telegram_chat_id == "chat"


def test_backup_config_from_instances_yaml_propagates_allow_insecure_ssl():
    yaml = _make_instances_yaml(allow_insecure_ssl=True)
    cfg = BackupConfig.from_instances_yaml(yaml)
    assert cfg is not None
    assert cfg.allow_insecure_ssl is True


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
# InstancesConfig — YAML config file loader
# ---------------------------------------------------------------------------

_MINIMAL_YAML = """\
sync:
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

sync:
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
sync:
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


def test_instances_config_load_invalid_yaml_syntax_raises_value_error(tmp_path):
    """Malformed YAML raises ValueError with a clear message, not a yaml.YAMLError traceback."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("key: [unclosed\n")

    with pytest.raises(ValueError, match=r"[Ii]nvalid YAML"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_name_dot_raises(tmp_path):
    """Instance name '.' is rejected to prevent path traversal via normalization."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  instances:
    - name: "."
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match=r"\.\.|\."):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_name_dotdot_raises(tmp_path):
    """Instance name '..' is rejected to prevent path traversal via normalization."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  instances:
    - name: ".."
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match=r"\.\.|\."):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_labels_numeric_keys_coerced_to_strings(tmp_path):
    """YAML integer label keys/values are coerced to strings."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      labels:
        BANK_TRANSACTION_INCOMING: 12345
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file)

    assert cfg.instances[0].label_ids == {"BANK_TRANSACTION_INCOMING": "12345"}


def test_instances_config_load_labels_blank_value_raises(tmp_path):
    """Blank label ID values are rejected."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      labels:
        BANK_TRANSACTION_INCOMING: ""
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="label"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_labels_null_value_raises(tmp_path):
    """Null label ID values are rejected."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      labels:
        BANK_TRANSACTION_INCOMING: ~
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="label"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_missing_name_raises(tmp_path):
    """ValueError is raised when an instance has no name."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
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
sync:
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
    cfg_file.write_text("sync:\n  instances: []\n")

    with pytest.raises(ValueError, match="instances"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_duplicate_instance_names(tmp_path):
    """ValueError is raised when two instances share the same name."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
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
    """to_config() sets data_dir to {root_data_dir}/tr_session_{instance_name}/."""
    from pathlib import Path

    from app.config import InstancesConfig

    yaml_content = f"""\
data_dir: {tmp_path}
sync:
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

    assert cfg.data_dir == Path(tmp_path) / "tr_session_user1"


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
    cfg_file.write_text("sync:\n  instances:\n    - just_a_string\n")

    with pytest.raises(ValueError, match="mapping"):
        InstancesConfig.load(cfg_file)


def test_instances_config_load_name_with_path_separator_raises(tmp_path):
    """ValueError when instance name contains a path separator (path traversal guard)."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
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

    with pytest.raises(ValueError, match=r"invalid characters|path separator"):
        InstancesConfig.load(cfg_file)


@pytest.mark.parametrize(
    "bad_name",
    [
        "david'",  # single quote — shell injection
        "eli;drop",  # semicolon — command separator
        "name`cmd`",  # backtick — command substitution
        "name*x",  # glob metachar
        "na me",  # space — splits shell tokens
        "name:colon",  # colon — not in allowlist (was accepted by old denylist)
        "name,comma",  # comma — not in allowlist (was accepted by old denylist)
        "na\\tme",  # literal backslash+t written to YAML (avoids embedded tab)
        "na\\nme",  # literal backslash+n written to YAML (avoids embedded newline)
    ],
)
def test_instances_config_load_name_with_invalid_chars_raises(tmp_path, bad_name):
    """ValueError when instance name contains characters outside the allowlist
    [A-Za-z0-9._-].  Covers both injection vectors and chars the old denylist missed."""
    from app.config import InstancesConfig

    yaml_content = f"""\
sync:
  instances:
    - name: "{bad_name}"
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    with pytest.raises(ValueError, match="invalid characters"):
        InstancesConfig.load(cfg_file)


@pytest.mark.parametrize(
    "good_name",
    ["david", "eli", "user1", "my-account", "my_account", "my.account", "User1.2"],
)
def test_instances_config_load_valid_names_accepted(tmp_path, good_name):
    """Instance names composed of [A-Za-z0-9._-] are accepted."""
    from app.config import InstancesConfig

    yaml_content = f"""\
sync:
  instances:
    - name: "{good_name}"
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file)
    assert cfg.instances[0].name == good_name


def test_instances_config_load_zero_lookback_days_raises(tmp_path):
    """ValueError when lookback_days is zero."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
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
sync:
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
sync:
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
sync:
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
sync:
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
sync:
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


def test_instances_config_load_name_is_integer_coerced_to_string(tmp_path):
    """name: 123 (YAML integer) is coerced to the string '123', not a TypeError."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
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
sync:
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
sync:
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
sync:
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


# ---------------------------------------------------------------------------
# SyncConfig — nested sync section with global defaults and per-instance overrides
# ---------------------------------------------------------------------------

_SYNC_SECTION_YAML = """\
data_dir: /app/data
telegram_bot_token: "tok"
telegram_chat_id: "chat"

backup_schedule: "0 3 * * *"

sync:
  schedule: "0 8,14,21 * * *"
  wallet_api_key: "shared-key"
  lookback_days: 14
  category_strategy: "history"
  instances:
    - name: david
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash-david"
      wallet_portfolio_account_id: "port-david"
      owner_name: "David"
    - name: eli
      phone: "+34611111111"
      pin: "5678"
      wallet_cash_account_id: "cash-eli"
      wallet_portfolio_account_id: "port-eli"
      owner_name: "Eli"
      wallet_api_key: "eli-own-key"
      lookback_days: 30
      schedule: "5 8,14,21 * * *"
"""


def test_sync_section_instances_are_accessible(tmp_path):
    """instances come from sync.instances when the sync section is used."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert [i.name for i in cfg.instances] == ["david", "eli"]


def test_sync_section_global_wallet_key_inherited(tmp_path):
    """An instance without wallet_api_key inherits it from sync.*."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances[0].wallet_api_key == "shared-key"


def test_sync_section_instance_overrides_wallet_key(tmp_path):
    """An instance with its own wallet_api_key overrides the global default."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances[1].wallet_api_key == "eli-own-key"


def test_sync_section_global_lookback_days_inherited(tmp_path):
    """An instance without lookback_days inherits it from sync.*."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances[0].lookback_days == 14


def test_sync_section_instance_overrides_lookback_days(tmp_path):
    """An instance with its own lookback_days overrides the global default."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances[1].lookback_days == 30


def test_sync_section_global_category_strategy_inherited(tmp_path):
    """An instance without category_strategy inherits it from sync.*."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances[0].category_strategy == "history"


def test_sync_section_global_schedule(tmp_path):
    """sync.schedule is exposed on InstancesConfig and each instance."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.sync_schedule == "0 8,14,21 * * *"
    assert cfg.instances[0].schedule == "0 8,14,21 * * *"


def test_sync_section_instance_overrides_schedule(tmp_path):
    """An instance with its own schedule overrides the global sync.schedule."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances[1].schedule == "5 8,14,21 * * *"


def test_backup_schedule_from_yaml(tmp_path):
    """backup_schedule is read from the YAML root."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_SYNC_SECTION_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.backup_schedule == "0 3 * * *"


def test_backup_schedule_defaults_to_none(tmp_path):
    """backup_schedule is None when not set in YAML."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_MINIMAL_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.backup_schedule is None


def test_sync_schedule_defaults_to_none(tmp_path):
    """sync_schedule is None when not set anywhere."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text(_MINIMAL_YAML)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.sync_schedule is None
    assert cfg.instances[0].schedule is None


def test_sync_section_missing_wallet_key_and_no_global_raises(tmp_path):
    """When neither instance nor sync.* provides wallet_api_key, raise ValueError."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  schedule: "0 8 * * *"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "port"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="wallet_api_key"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# _resolve_category_strategy — unit tests for the extracted helper
# ---------------------------------------------------------------------------


def test_resolve_category_strategy_defaults_to_none():
    """No per-instance value and no global → 'none'."""
    from app.config import _resolve_category_strategy

    assert _resolve_category_strategy("u", {}, None) == "none"


def test_resolve_category_strategy_inherits_global():
    """No per-instance value but global is set → inherits global."""
    from app.config import _resolve_category_strategy

    assert _resolve_category_strategy("u", {}, "history") == "history"


def test_resolve_category_strategy_per_instance_overrides_global():
    """Per-instance value takes precedence over global."""
    from app.config import _resolve_category_strategy

    assert (
        _resolve_category_strategy("u", {"category_strategy": "none"}, "history")
        == "none"
    )


def test_resolve_category_strategy_invalid_raises():
    """An unrecognised strategy raises ValueError mentioning the instance name."""
    from app.config import _resolve_category_strategy

    with pytest.raises(ValueError, match="category_strategy"):
        _resolve_category_strategy("u", {"category_strategy": "bad"}, None)


def test_sync_section_no_instances_raises(tmp_path):
    """sync.instances must define at least one instance."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  schedule: "0 8 * * *"
  instances: []
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="instance"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# global category_strategy validation — independent of per-instance overrides
# ---------------------------------------------------------------------------


def test_instances_config_load_invalid_global_category_strategy_raises(tmp_path):
    """An invalid sync.category_strategy must raise even when every instance overrides it."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  category_strategy: bad_value
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      category_strategy: history
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="category_strategy"):
        InstancesConfig.load(tmp_path / "i.yml")


def test_instances_config_load_blank_global_category_strategy_raises(tmp_path):
    """A present-but-blank sync.category_strategy must raise, not silently become None."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  category_strategy: "   "
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      category_strategy: history
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="category_strategy"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# SyncConfig — nested sync section exposed on InstancesConfig
# ---------------------------------------------------------------------------


def test_instances_config_exposes_sync_field(tmp_path):
    """InstancesConfig.sync must expose the global sync defaults and instance list."""
    from app.config import InstancesConfig, SyncConfig

    yaml_content = """\
sync:
  wallet_api_key: "globalkey"
  lookback_days: 14
  category_strategy: history
  schedule: "0 8 * * *"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert isinstance(cfg.sync, SyncConfig)
    assert cfg.sync.wallet_api_key == "globalkey"
    assert cfg.sync.lookback_days == 14
    assert cfg.sync.category_strategy == "history"
    assert cfg.sync.schedule == "0 8 * * *"
    assert len(cfg.sync.instances) == 1
    assert cfg.sync.instances[0].name == "user1"


def test_instances_config_instances_property_delegates_to_sync(tmp_path):
    """InstancesConfig.instances must be a backward-compat property over .sync.instances."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  wallet_api_key: "key"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances is cfg.sync.instances


# ---------------------------------------------------------------------------
# wallet_api_key blank-check — per-instance and global
# ---------------------------------------------------------------------------


def test_instances_config_load_blank_per_instance_wallet_api_key_raises(tmp_path):
    """A blank per-instance wallet_api_key must raise ValueError, not silently pass."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "   "
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="wallet_api_key"):
        InstancesConfig.load(tmp_path / "i.yml")


def test_instances_config_load_blank_per_instance_wallet_api_key_with_global_raises(
    tmp_path,
):
    """A present-but-blank per-instance wallet_api_key must raise, not fall back to global key."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  wallet_api_key: "globalkey"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "   "
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="wallet_api_key"):
        InstancesConfig.load(tmp_path / "i.yml")


def test_instances_config_load_blank_global_wallet_api_key_raises(tmp_path):
    """A present-but-blank sync.wallet_api_key must raise even when every instance has its own key."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  wallet_api_key: "   "
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "per-instance-key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="wallet_api_key"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# sync.instances type validation
# ---------------------------------------------------------------------------


def test_instances_config_load_instances_not_a_list_raises(tmp_path):
    """sync.instances with a non-list value must raise ValueError, not TypeError."""
    from app.config import InstancesConfig

    (tmp_path / "i.yml").write_text("sync:\n  instances: 1\n")

    with pytest.raises(ValueError, match="instances"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# null wallet_api_key — per-instance and global
# ---------------------------------------------------------------------------


def test_instances_config_load_null_per_instance_wallet_api_key_raises(tmp_path):
    """wallet_api_key: null per-instance must raise, not silently inherit global key."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  wallet_api_key: "globalkey"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: null
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="wallet_api_key"):
        InstancesConfig.load(tmp_path / "i.yml")


def test_instances_config_load_null_global_wallet_api_key_raises(tmp_path):
    """sync.wallet_api_key: null must raise, not be treated as absent."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  wallet_api_key: null
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "per-instance-key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="wallet_api_key"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# global lookback_days positive-value constraint
# ---------------------------------------------------------------------------


def test_instances_config_load_global_lookback_days_zero_raises(tmp_path):
    """sync.lookback_days: 0 must raise even when every instance overrides it."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  lookback_days: 0
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      lookback_days: 7
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="lookback_days"):
        InstancesConfig.load(tmp_path / "i.yml")


def test_instances_config_load_global_lookback_days_negative_raises(tmp_path):
    """sync.lookback_days: -1 must raise even when every instance overrides it."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  lookback_days: -1
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      lookback_days: 7
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="lookback_days"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# global category_strategy normalization
# ---------------------------------------------------------------------------


def test_instances_config_load_global_category_strategy_uppercase_accepted(tmp_path):
    """sync.category_strategy: HISTORY must be normalized and accepted like per-instance values."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  category_strategy: HISTORY
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.sync.category_strategy == "history"
    assert cfg.instances[0].category_strategy == "history"


# ---------------------------------------------------------------------------
# schedule blank-check — global and per-instance
# ---------------------------------------------------------------------------


def test_instances_config_load_blank_global_schedule_raises(tmp_path):
    """A present-but-blank sync.schedule must raise, not silently become None."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  schedule: "   "
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


def test_instances_config_load_blank_per_instance_schedule_raises(tmp_path):
    """A present-but-blank per-instance schedule must raise, not silently disable scheduling."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  schedule: "0 8 * * *"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      schedule: "   "
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# schedule newline injection — global, per-instance, backup_schedule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_schedule", ["0 8 * * *\\n0 9 * * *", "0 8 * * *\\r0 9 * * *"]
)
def test_instances_config_load_newline_in_global_schedule_raises(
    tmp_path, bad_schedule
):
    """sync.schedule containing \\n or \\r must raise to prevent cron-line injection."""
    from app.config import InstancesConfig

    yaml_content = f"""\
sync:
  wallet_api_key: "key"
  schedule: "{bad_schedule}"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


@pytest.mark.parametrize(
    "bad_schedule", ["0 8 * * *\\n0 9 * * *", "0 8 * * *\\r0 9 * * *"]
)
def test_instances_config_load_newline_in_per_instance_schedule_raises(
    tmp_path, bad_schedule
):
    """Per-instance schedule containing \\n or \\r must raise."""
    from app.config import InstancesConfig

    yaml_content = f"""\
sync:
  wallet_api_key: "key"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      schedule: "{bad_schedule}"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


@pytest.mark.parametrize(
    "bad_schedule", ["0 3 * * *\\n0 4 * * *", "0 3 * * *\\r0 4 * * *"]
)
def test_instances_config_load_newline_in_backup_schedule_raises(
    tmp_path, bad_schedule
):
    """backup_schedule containing \\n or \\r must raise to prevent cron-line injection."""
    from app.config import InstancesConfig

    yaml_content = f"""\
sync:
  wallet_api_key: "key"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
backup_schedule: "{bad_schedule}"
"""
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="backup_schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# lookback_days: null in instance — must apply global default, not silently use 7
# ---------------------------------------------------------------------------


def test_instances_config_load_null_lookback_days_applies_global(tmp_path):
    """lookback_days: null in an instance must apply the global default, not the hardcoded 7."""
    from app.config import InstancesConfig

    yaml_content = """\
sync:
  lookback_days: 30
  wallet_api_key: "key"
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
      lookback_days: null
"""
    (tmp_path / "i.yml").write_text(yaml_content)
    cfg = InstancesConfig.load(tmp_path / "i.yml")

    assert cfg.instances[0].lookback_days == 30


# ---------------------------------------------------------------------------
# _validate_cron_schedule — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "* * * * *",
        "0 8 * * *",
        "0 8,20 * * *",
        "*/15 * * * *",
        "0 8-18/2 * * 1-5",
        "30 3 1,15 * *",
        "0 0 * * 0",
        "59 23 31 12 7",
    ],
)
def test_validate_cron_schedule_valid(expr):
    """Valid five-field cron expressions must not raise."""
    from app.config import _validate_cron_schedule

    _validate_cron_schedule("schedule", expr)  # should not raise


@pytest.mark.parametrize(
    "expr",
    [
        # Classic cron-field injection (extra fields after schedule)
        "* * * * * root touch /tmp/pwned #",
        "0 8 * * * /bin/bash -c 'id'",
        # Fewer or more pure numeric fields
        "* * * *",
        "* * * * * *",
        # Empty string
        "",
        # Letters / shell metacharacters inside a field
        "* * * * MON",
        "0 8 * JAN *",
        "$(id) * * * *",
        "`id` * * * *",
        "0;touch /tmp/x * * * *",
        # Newline / carriage-return (regression: old check)
        "0 8 * * *\\n0 9 * * *",
    ],
)
def test_validate_cron_schedule_invalid(expr):
    """Invalid or injected schedule strings must raise ValueError."""
    from app.config import _validate_cron_schedule

    with pytest.raises(ValueError, match="schedule"):
        _validate_cron_schedule("schedule", expr)


# ---------------------------------------------------------------------------
# InstancesConfig.load — cron injection via schedule fields
# ---------------------------------------------------------------------------

_INSTANCE_BLOCK = """\
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "portfolio"
"""


@pytest.mark.parametrize(
    "bad_schedule",
    [
        "* * * * * root touch /tmp/pwned #",
        "0 8 * * * /bin/bash -c id",
        "* * * *",
        "* * * * * *",
    ],
)
def test_instances_config_load_injection_in_global_schedule_raises(
    tmp_path, bad_schedule
):
    """sync.schedule with injection payload or wrong field count must raise."""
    from app.config import InstancesConfig

    yaml_content = f"sync:\n  wallet_api_key: key\n  schedule: '{bad_schedule}'\n  instances:\n{_INSTANCE_BLOCK}"
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


@pytest.mark.parametrize(
    "bad_schedule",
    [
        "* * * * * root touch /tmp/pwned #",
        "0 8 * * * /bin/bash -c id",
    ],
)
def test_instances_config_load_injection_in_per_instance_schedule_raises(
    tmp_path, bad_schedule
):
    """Per-instance schedule with injection payload must raise."""
    from app.config import InstancesConfig

    inst_block = _INSTANCE_BLOCK.rstrip("\n") + f"\n      schedule: '{bad_schedule}'\n"
    yaml_content = f"sync:\n  wallet_api_key: key\n  instances:\n{inst_block}"
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


@pytest.mark.parametrize(
    "bad_schedule",
    [
        "* * * * * root touch /tmp/pwned #",
        "0 3 * * * /bin/bash -c id",
    ],
)
def test_instances_config_load_injection_in_backup_schedule_raises(
    tmp_path, bad_schedule
):
    """backup_schedule with injection payload must raise."""
    from app.config import InstancesConfig

    yaml_content = f"sync:\n  wallet_api_key: key\n  instances:\n{_INSTANCE_BLOCK}backup_schedule: '{bad_schedule}'\n"
    (tmp_path / "i.yml").write_text(yaml_content)

    with pytest.raises(ValueError, match="schedule"):
        InstancesConfig.load(tmp_path / "i.yml")


# ---------------------------------------------------------------------------
# INSTANCES_CONFIG_PATH constant (issue #162 — hardcoded path)
# ---------------------------------------------------------------------------


def test_instances_config_path_constant_value():
    """INSTANCES_CONFIG_PATH must be the hardcoded Docker path."""
    from pathlib import Path

    from app.config import INSTANCES_CONFIG_PATH

    assert Path("/app/config/instances.yml") == INSTANCES_CONFIG_PATH


# ---------------------------------------------------------------------------
# Config.shared_db_path property (issue #173)
# ---------------------------------------------------------------------------


def test_config_shared_db_path_is_root_sync_db(tmp_path):
    """Config.shared_db_path must point to {root_data_dir}/sync.db."""
    from app.config import Config

    root = tmp_path / "data"
    instance_data_dir = root / "tr_session_alice"
    cfg = Config(
        owner_name="Alice",
        phone_number="+34600000000",
        pin="1234",
        wallet_api_key="key",
        wallet_cash_account_id="cash",
        wallet_portfolio_account_id="port",
        telegram_bot_token=None,
        telegram_chat_id=None,
        lookback_days=7,
        dedup_ttl_days=60,
        data_dir=instance_data_dir,
        instance="alice",
    )

    assert cfg.shared_db_path == root / "sync.db"


def test_instances_config_to_config_shared_db_path(tmp_path):
    """to_config() must set shared_db_path to {root_data_dir}/sync.db."""
    import textwrap

    from app.config import InstancesConfig

    yaml_content = textwrap.dedent(f"""\
        data_dir: {tmp_path}
        telegram_bot_token: "tok"
        telegram_chat_id: "cid"
        sync:
          wallet_api_key: "key"
          instances:
            - name: user1
              phone: "+34600000000"
              pin: "1234"
              wallet_cash_account_id: "cash"
              wallet_portfolio_account_id: "port"
    """)
    cfg_file = tmp_path / "i.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.shared_db_path == tmp_path / "sync.db"


# ---------------------------------------------------------------------------
# Config.twofa_code_file / twofa_pending_file properties (issue #174)
# ---------------------------------------------------------------------------


def _make_config_for_instance(root, instance: str):
    from app.config import Config

    return Config(
        owner_name=instance.capitalize(),
        phone_number="+34600000000",
        pin="1234",
        wallet_api_key="key",
        wallet_cash_account_id="cash",
        wallet_portfolio_account_id="port",
        telegram_bot_token=None,
        telegram_chat_id=None,
        lookback_days=7,
        dedup_ttl_days=60,
        data_dir=root / f"tr_session_{instance}",
        instance=instance,
    )


def test_config_twofa_code_file_at_root_with_instance_suffix(tmp_path):
    """twofa_code_file must be {root}/.tr_2fa_code_{instance}."""
    cfg = _make_config_for_instance(tmp_path, "alice")
    assert cfg.twofa_code_file == tmp_path / ".tr_2fa_code_alice"


def test_config_twofa_pending_file_at_root_with_instance_suffix(tmp_path):
    """twofa_pending_file must be {root}/.tr_2fa_pending_{instance}."""
    cfg = _make_config_for_instance(tmp_path, "alice")
    assert cfg.twofa_pending_file == tmp_path / ".tr_2fa_pending_alice"


def test_config_twofa_files_different_instances_do_not_collide(tmp_path):
    """Two different instances must have different 2FA file paths."""
    cfg_a = _make_config_for_instance(tmp_path, "alice")
    cfg_b = _make_config_for_instance(tmp_path, "bob")
    assert cfg_a.twofa_code_file != cfg_b.twofa_code_file
    assert cfg_a.twofa_pending_file != cfg_b.twofa_pending_file


def test_instances_config_to_config_twofa_files_at_root(tmp_path):
    """to_config() must produce 2FA files at root level with instance suffix."""
    import textwrap

    from app.config import InstancesConfig

    yaml_content = textwrap.dedent(f"""\
        data_dir: {tmp_path}
        telegram_bot_token: "tok"
        telegram_chat_id: "cid"
        sync:
          wallet_api_key: "key"
          instances:
            - name: user1
              phone: "+34600000000"
              pin: "1234"
              wallet_cash_account_id: "cash"
              wallet_portfolio_account_id: "port"
    """)
    cfg_file = tmp_path / "i.yml"
    cfg_file.write_text(yaml_content)

    cfg = InstancesConfig.load(cfg_file).to_config("user1")

    assert cfg.twofa_code_file == tmp_path / ".tr_2fa_code_user1"
    assert cfg.twofa_pending_file == tmp_path / ".tr_2fa_pending_user1"
