from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.bot import (
    _BACKUP_ICONS,
    BotConfig,
    InstanceConfig,
    InstanceStatus,
    TelegramBot,
    _auth_icon,
    _check_session_direct,
    _format_sync_timestamp,
    _instance_status_direct,
    _last_sync_summary_direct,
)
from app.config import BackupConfig, Config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(data_dir: Path, instance: str = "user1") -> Config:
    return Config(
        owner_name=instance.capitalize(),
        phone_number="+34600000000",
        pin="1234",
        wallet_api_key="key",
        wallet_cash_account_id="cash",
        wallet_portfolio_account_id="portfolio",
        telegram_bot_token=None,
        telegram_chat_id=None,
        lookback_days=7,
        dedup_ttl_days=60,
        data_dir=data_dir,
        instance=instance,
    )


def _make_backup_config(data_dir: Path) -> BackupConfig:
    return BackupConfig(
        owner_name="Backup",
        wallet_api_key="backup-key",
        telegram_bot_token=None,
        telegram_chat_id=None,
        data_dir=data_dir,
    )


def _cfg(
    instances: dict[str, InstanceConfig] | None = None,
    backup_cfg: BackupConfig | None = ...,  # type: ignore[assignment]
    tmp_path: Path | None = None,
) -> BotConfig:
    if tmp_path is None:
        tmp_path = Path("/tmp/bot_test")
    if instances is None:
        instances = {
            "user1": InstanceConfig(
                name="User1", config=_make_config(tmp_path / "user1", "user1")
            ),
            "user2": InstanceConfig(
                name="User2", config=_make_config(tmp_path / "user2", "user2")
            ),
        }
    if backup_cfg is ...:
        backup_cfg = _make_backup_config(tmp_path / "backup")
    return BotConfig(
        bot_token="tok",
        chat_id="42",
        instances=instances,
        backup_cfg=backup_cfg,
        log_dir=tmp_path,
    )


def _bot(
    instances: dict[str, InstanceConfig] | None = None,
    backup_cfg: BackupConfig | None = ...,  # type: ignore[assignment]
    tmp_path: Path | None = None,
) -> TelegramBot:
    return TelegramBot(_cfg(instances, backup_cfg, tmp_path))


# ---------------------------------------------------------------------------
# BotConfig.from_env
# ---------------------------------------------------------------------------

_YAML_CONTENT = """
telegram_bot_token: "mytoken"
telegram_chat_id: "123"
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key1"
      wallet_cash_account_id: "cash1"
      wallet_portfolio_account_id: "portfolio1"
    - name: user2
      phone: "+34611111111"
      pin: "5678"
      wallet_api_key: "key2"
      wallet_cash_account_id: "cash2"
      wallet_portfolio_account_id: "portfolio2"
"""

_YAML_NO_TOKEN = """
telegram_chat_id: "123"
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key1"
      wallet_cash_account_id: "cash1"
      wallet_portfolio_account_id: "portfolio1"
"""

_YAML_NO_CHAT_ID = """
telegram_bot_token: "mytoken"
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key1"
      wallet_cash_account_id: "cash1"
      wallet_portfolio_account_id: "portfolio1"
"""

# Unquoted numeric scalars — YAML loads these as int, not str.
_YAML_NUMERIC_CHAT_ID = """
telegram_bot_token: "mytoken"
telegram_chat_id: 123
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "key1"
      wallet_cash_account_id: "cash1"
      wallet_portfolio_account_id: "portfolio1"
"""


def _mock_instances_load(yaml_text: str = _YAML_CONTENT):
    """Return a patcher for InstancesConfig.load that parses *yaml_text*."""
    import tempfile
    from pathlib import Path

    from app.config import InstancesConfig

    _real_load = InstancesConfig.load.__func__  # type: ignore[attr-defined]

    def _load(path):
        with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as fh:
            fh.write(yaml_text)
            tmp = Path(fh.name)
        try:
            result = _real_load(InstancesConfig, tmp)
        finally:
            tmp.unlink(missing_ok=True)
        return result

    return patch("app.bot.InstancesConfig.load", side_effect=_load)


def test_botconfig_from_env_valid():
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert cfg.bot_token == "mytoken"
    assert cfg.chat_id == "123"
    assert "user1" in cfg.instances
    assert "user2" in cfg.instances


def test_botconfig_from_env_instances_have_correct_name():
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert cfg.instances["user1"].name == "user1"
    assert cfg.instances["user2"].name == "user2"


def test_botconfig_from_env_instances_have_config_objects():
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert isinstance(cfg.instances["user1"].config, Config)
    assert cfg.instances["user1"].config.phone_number == "+34600000000"


def test_botconfig_from_env_backup_uses_yaml_wallet_key(monkeypatch):
    """backup_cfg is derived from the first instance's wallet_api_key in YAML."""
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert cfg.backup_cfg is not None
    assert cfg.backup_cfg.wallet_api_key == "key1"


def test_botconfig_from_env_backup_yaml_data_dir_is_backup_subdir(monkeypatch):
    """Backup data_dir derived from YAML uses instances data_dir / 'backup'."""
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert cfg.backup_cfg is not None
    assert cfg.backup_cfg.data_dir.name == "data"


def test_botconfig_from_env_missing_token(monkeypatch):
    with (
        _mock_instances_load(_YAML_NO_TOKEN),
        pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"),
    ):
        BotConfig.from_env()


def test_botconfig_from_env_missing_chat_id(monkeypatch):
    with (
        _mock_instances_load(_YAML_NO_CHAT_ID),
        pytest.raises(ValueError, match="TELEGRAM_CHAT_ID"),
    ):
        BotConfig.from_env()


def test_botconfig_from_env_token_from_yaml(monkeypatch):
    """TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are read from instances.yml."""
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert cfg.bot_token == "mytoken"
    assert cfg.chat_id == "123"


def test_botconfig_from_env_numeric_chat_id_in_yaml(monkeypatch):
    """Unquoted numeric telegram_chat_id in YAML (loaded as int) must not raise AttributeError."""
    with _mock_instances_load(_YAML_NUMERIC_CHAT_ID):
        cfg = BotConfig.from_env()
    assert cfg.chat_id == "123"


def test_botconfig_from_env_invalid_allow_insecure_ssl_raises():
    """A bad allow_insecure_ssl value in the YAML must propagate as ValueError."""
    bad_yaml = _YAML_CONTENT.replace(
        'telegram_bot_token: "mytoken"',
        'allow_insecure_ssl: "not-a-bool"\ntelegram_bot_token: "mytoken"',
    )
    with (
        _mock_instances_load(bad_yaml),
        pytest.raises(ValueError, match="allow_insecure_ssl"),
    ):
        BotConfig.from_env()


def test_botconfig_from_env_uses_instances_config_path():
    from app.config import INSTANCES_CONFIG_PATH

    with _mock_instances_load() as mock_load:
        BotConfig.from_env()
    mock_load.assert_called_once_with(INSTANCES_CONFIG_PATH)


def test_botconfig_from_env_allow_insecure_ssl_defaults_false():
    """BotConfig.allow_insecure_ssl must default to False when YAML has no setting."""
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert cfg.allow_insecure_ssl is False


def test_botconfig_from_env_allow_insecure_ssl_true_from_yaml():
    """BotConfig.allow_insecure_ssl must be True when the YAML sets allow_insecure_ssl: true."""
    yaml_with_ssl = _YAML_CONTENT.rstrip() + "\nallow_insecure_ssl: true\n"
    with _mock_instances_load(yaml_with_ssl):
        cfg = BotConfig.from_env()
    assert cfg.allow_insecure_ssl is True


def test_botconfig_log_dir_default_is_data_dir():
    """BotConfig.log_dir default must be /app/data (not /app/data/logs)."""
    with _mock_instances_load():
        cfg = BotConfig.from_env()
    assert cfg.log_dir == Path("/app/data")


def test_botconfig_from_env_log_dir_uses_instances_data_dir():
    """BotConfig.log_dir must equal instances_yaml.data_dir (not data_dir / 'logs')."""
    yaml_with_data_dir = _YAML_CONTENT.rstrip() + '\ndata_dir: "/custom/data"\n'
    with _mock_instances_load(yaml_with_data_dir):
        cfg = BotConfig.from_env()
    assert cfg.log_dir == Path("/custom/data")


# ---------------------------------------------------------------------------
# TelegramBot — SSL circuit-breaker session
# ---------------------------------------------------------------------------


def test_telegrambot_creates_session_via_build_session(tmp_path):
    """TelegramBot must build its Telegram session through http_client.build_session
    so that allow_insecure_ssl applies to bot traffic too."""
    import requests as req_lib

    from app import http_client

    with patch("app.bot._build_session", wraps=http_client.build_session) as mock_build:
        bot = _bot(tmp_path=tmp_path)
    mock_build.assert_called_once()
    assert isinstance(bot._session, req_lib.Session)


# ---------------------------------------------------------------------------
# TelegramBot._register_commands
# ---------------------------------------------------------------------------


def test_register_commands_includes_backup_when_configured(tmp_path):
    bot = _bot(backup_cfg=_make_backup_config(tmp_path), tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    commands = mock_post.call_args.kwargs["json"]["commands"]
    cmd_names = [c["command"] for c in commands]
    assert "sync" in cmd_names
    assert "backup" in cmd_names
    assert "status" in cmd_names
    assert "help" not in cmd_names
    assert "login" not in cmd_names


def test_register_commands_excludes_backup_when_not_configured(tmp_path):
    bot = _bot(backup_cfg=None, tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    commands = mock_post.call_args.kwargs["json"]["commands"]
    cmd_names = [c["command"] for c in commands]
    assert "backup" not in cmd_names
    assert "sync" in cmd_names


def test_register_commands_logs_description_does_not_mention_instance(tmp_path):
    """The registered /logs command description must reflect the shared log, not an instance."""
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    commands = mock_post.call_args.kwargs["json"]["commands"]
    logs_cmd = next(c for c in commands if c["command"] == "logs")
    assert "instance" not in logs_cmd["description"].lower()


def test_register_commands_does_not_raise_on_failure(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(
        bot._session, "post", side_effect=requests.RequestException("fail")
    ):
        bot._register_commands()  # must not raise


# ---------------------------------------------------------------------------
# TelegramBot._handle_message — authorization
# ---------------------------------------------------------------------------


def test_handle_message_ignores_unauthorized_chat(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 9999}, "text": "/help"})
    mock_send.assert_not_called()


def test_handle_message_non_command_replies_commands_only(tmp_path):
    """Non-command plain text receives a 'commands only' reply (not silently ignored)."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "hello"})
    mock_send.assert_called_once()
    assert "command" in mock_send.call_args.args[0].lower()
    assert "/help" not in mock_send.call_args.args[0]


def test_handle_message_unknown_command_replies(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "/unknown"})
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]
    assert "/help" not in mock_send.call_args.args[0]


def test_handle_message_help_is_unknown_command(tmp_path):
    """/help is no longer a registered command and returns an unknown-command reply."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "/help"})
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]


def test_handle_message_strips_bot_name_suffix(tmp_path):
    """Commands like /sync@MyBot should be treated as /sync."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_cmd_sync") as mock_sync:
        bot._handle_message({"chat": {"id": 42}, "text": "/sync@MyBot"})
    mock_sync.assert_called_once()


def test_handle_message_deletes_code_message_for_privacy(tmp_path):
    """The /code message carries a sensitive 2FA code and must be deleted."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_cmd_code"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message(
            {"chat": {"id": 42}, "message_id": 555, "text": "/code user1 123456"}
        )
    mock_delete.assert_called_once_with(555)


def test_handle_message_does_not_delete_non_code_commands(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_cmd_sync"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "message_id": 555, "text": "/sync"})
    mock_delete.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._delete_message
# ---------------------------------------------------------------------------


def test_delete_message_calls_telegram_api(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot._session, "post") as mock_post:
        bot._delete_message(555)
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["message_id"] == 555
    assert str(payload["chat_id"]) == bot._cfg.chat_id


def test_delete_message_does_not_raise_on_failure(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(
        bot._session, "post", side_effect=requests.RequestException("fail")
    ):
        bot._delete_message(555)  # must not raise


def test_delete_message_ignores_missing_id(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot._session, "post") as mock_post:
        bot._delete_message(None)
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._cmd_backup
# ---------------------------------------------------------------------------


def test_cmd_backup_no_backup_cfg_sends_error(tmp_path):
    bot = _bot(backup_cfg=None, tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup([])
    mock_send.assert_called_once()
    assert "not configured" in mock_send.call_args.args[0]


def test_cmd_backup_without_args_shows_type_keyboard(tmp_path):
    """Without args /backup should show a Monthly / Yearly type-selection keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup([])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any("backup_type:monthly" in d for d in cb_data)
    assert any("backup_type:yearly" in d for d in cb_data)


def test_cmd_backup_monthly_arg_shows_month_keyboard(tmp_path):
    """'/backup monthly' (no period) → show month selection keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup(["monthly"])
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert all(d.startswith("backup_monthly:") for d in cb_data)


def test_cmd_backup_yearly_arg_shows_year_keyboard(tmp_path):
    """'/backup yearly' (no year) → show year selection keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup(["yearly"])
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert all(d.startswith("backup_yearly:") for d in cb_data)


def test_cmd_backup_monthly_with_period_executes_directly(tmp_path):
    """/backup monthly YYYY-MM executes without showing a keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_launch_backup") as mock_launch,
    ):
        bot._cmd_backup(["monthly", "2026-07"])
    mock_launch.assert_called_once_with("monthly", "2026-07")
    # _send_message should not have been called with a keyboard
    for call in mock_send.call_args_list:
        assert call.kwargs.get("keyboard") is None


def test_cmd_backup_yearly_with_year_executes_directly(tmp_path):
    """/backup yearly YYYY executes without showing a keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_launch_backup") as mock_launch,
    ):
        bot._cmd_backup(["yearly", "2025"])
    mock_launch.assert_called_once_with("yearly", "2025")
    # _send_message should not have been called with a keyboard
    for call in mock_send.call_args_list:
        assert call.kwargs.get("keyboard") is None


def test_cmd_backup_unknown_type_sends_error(tmp_path):
    """/backup weekly → error message, no keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup(["weekly"])
    msg = mock_send.call_args.args[0]
    assert "weekly" in msg
    assert mock_send.call_args.kwargs.get("keyboard") is None


def test_handle_message_dispatches_backup(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_cmd_backup") as mock_backup:
        bot._handle_message({"chat": {"id": 42}, "text": "/backup"})
    mock_backup.assert_called_once_with([])


def test_handle_message_dispatches_backup_with_args(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_cmd_backup") as mock_backup:
        bot._handle_message({"chat": {"id": 42}, "text": "/backup monthly 2026-07"})
    mock_backup.assert_called_once_with(["monthly", "2026-07"])


# ---------------------------------------------------------------------------
# backup_type callback — type selection step
# ---------------------------------------------------------------------------


def test_callback_query_backup_type_monthly_shows_month_keyboard(tmp_path):
    """`backup_type:monthly` callback → show month selection keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "backup_type:monthly",
                "message": {"chat": {"id": 42}},
            }
        )
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    assert all(b["callback_data"].startswith("backup_monthly:") for b in all_buttons)


def test_callback_query_backup_type_yearly_shows_year_keyboard(tmp_path):
    """`backup_type:yearly` callback → show year selection keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "backup_type:yearly",
                "message": {"chat": {"id": 42}},
            }
        )
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    assert all(b["callback_data"].startswith("backup_yearly:") for b in all_buttons)


def test_launch_backup_monthly_sends_ack_and_starts_thread(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup("monthly", "2026-07")
    mock_send.assert_called_once()
    assert "2026" in mock_send.call_args.args[0]


def test_launch_backup_sends_error_when_backup_not_configured(tmp_path):
    """Guard: _launch_backup with no backup cfg → clear error, no crash."""
    bot = _bot(backup_cfg=None, tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._launch_backup("monthly", "2026-07")
    mock_send.assert_called_once()
    assert "not configured" in mock_send.call_args.args[0].lower()


def test_callback_backup_monthly_with_no_backup_cfg_sends_error(tmp_path):
    """Stale inline keyboard: backup_monthly callback when backup not configured → error message."""
    bot = _bot(backup_cfg=None, tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "backup_monthly:2026-07",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_send.assert_called_once()
    assert "not configured" in mock_send.call_args.args[0].lower()


def test_callback_backup_yearly_with_no_backup_cfg_sends_error(tmp_path):
    """Stale inline keyboard: backup_yearly callback when backup not configured → error message."""
    bot = _bot(backup_cfg=None, tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "backup_yearly:2025",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_send.assert_called_once()
    assert "not configured" in mock_send.call_args.args[0].lower()


def test_callback_query_backup_monthly_dispatches_launch(tmp_path):
    """backup_monthly:YYYY-MM callback should trigger _launch_backup("monthly", period)."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_backup") as mock_launch,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "backup_monthly:2026-07",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_launch.assert_called_once_with("monthly", "2026-07")


def test_launch_backup_monthly_starts_thread(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup("monthly", "2026-07")
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["daemon"] is True
    mock_thread.return_value.start.assert_called_once()


def test_callback_query_backup_yearly_dispatches_launch(tmp_path):
    """backup_yearly:<year> callback should trigger _launch_backup("yearly", year)."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_backup") as mock_launch,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "backup_yearly:2025",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_launch.assert_called_once_with("yearly", "2025")


def test_launch_backup_yearly_sends_ack_and_starts_thread(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup("yearly", "2025")
    mock_send.assert_called_once()
    assert "2025" in mock_send.call_args.args[0]


# ---------------------------------------------------------------------------
# _BACKUP_ICONS — bot uses the right icon per backup mode
# ---------------------------------------------------------------------------


def test_launch_backup_uses_correct_icon_for_monthly(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup("monthly", "2026-07")
    assert _BACKUP_ICONS["monthly"] in mock_send.call_args.args[0]


def test_launch_backup_uses_correct_icon_for_yearly(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup("yearly", "2025")
    assert _BACKUP_ICONS["yearly"] in mock_send.call_args.args[0]


# ---------------------------------------------------------------------------
# TelegramBot._handle_callback_query
# ---------------------------------------------------------------------------


def test_callback_query_noop_is_acknowledged_and_ignored(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query") as mock_ack,
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "noop", "message": {"chat": {"id": 42}}}
        )
    mock_ack.assert_called_once_with("cq1")
    mock_sync.assert_not_called()


def test_callback_query_unauthorized_chat_ignored(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "sync:user1", "message": {"chat": {"id": 9999}}}
        )
    mock_sync.assert_not_called()


def test_callback_query_sync_dispatches_launch_sync(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "sync:user1", "message": {"chat": {"id": 42}}}
        )
    mock_sync.assert_called_once()
    assert mock_sync.call_args.args[0].name == "User1"


def test_callback_query_legacy_login_routes_to_sync(tmp_path):
    """Legacy ``login:<instance>`` callbacks from old chat history trigger a sync."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "login:user1", "message": {"chat": {"id": 42}}}
        )
    mock_sync.assert_called_once()
    assert mock_sync.call_args.args[0].name == "User1"
    # User should receive a deprecation notice
    assert mock_send.call_count == 1
    assert "/login" in mock_send.call_args.args[0]


def test_callback_query_unknown_instance_replies(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "sync:nobody", "message": {"chat": {"id": 42}}}
        )
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]


def test_callback_query_malformed_data_does_not_raise(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message"),
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "malformed", "message": {"chat": {"id": 42}}}
        )
    # must not raise


def test_callback_query_unknown_cmd_logs_warning_and_does_not_raise(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "badcmd:user1", "message": {"chat": {"id": 42}}}
        )
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._launch_sync
# ---------------------------------------------------------------------------


def test_launch_sync_sends_ack_and_starts_thread(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_sync(inst)
    mock_send.assert_called_once()
    assert "User1" in mock_send.call_args.args[0]
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["daemon"] is True


def test_launch_sync_thread_target_is_run_sync_for_instance(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    with (
        patch.object(bot, "_send_message"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_sync(inst)
    assert mock_thread.call_args.kwargs["target"] == bot._run_sync_for_instance
    assert mock_thread.call_args.kwargs["args"] == (inst,)


def test_run_sync_for_instance_calls_main_run(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    with patch("app.bot._main_run") as mock_run:
        bot._run_sync_for_instance(inst)
    mock_run.assert_called_once_with(cfg=inst.config)


def test_run_sync_for_instance_sends_error_on_exception(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    with (
        patch("app.bot._main_run", side_effect=RuntimeError("boom")),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._run_sync_for_instance(inst)
    mock_send.assert_called_once()
    assert "boom" in mock_send.call_args.args[0]


# ---------------------------------------------------------------------------
# TelegramBot — digit-intercept for 2FA
# ---------------------------------------------------------------------------


def test_handle_message_digit_string_submitted_when_pending_marker_present(tmp_path):
    """A digit-only reply is treated as 2FA code when the twofa_pending_file marker exists."""
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]

    # Create pending marker at the root-level instance-suffixed path
    inst.config.twofa_pending_file.touch()

    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})

    assert inst.config.twofa_code_file.read_text() == "123456"
    mock_delete.assert_called_once_with(77)


def test_handle_message_digit_string_not_deleted_when_no_pending_login_multi_instance(
    tmp_path,
):
    """Digit messages with no pending login and multiple instances send a disambiguation
    prompt, but do not submit code or delete the message."""
    bot = _bot(tmp_path=tmp_path)
    # No pending markers in any data_dir
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    mock_send.assert_called_once()
    assert "/code" in mock_send.call_args.args[0]
    mock_delete.assert_not_called()


def test_handle_message_digit_string_not_deleted_when_multiple_pending_markers(
    tmp_path,
):
    """Digit message is not deleted when multiple instances have twofa_pending_file markers."""
    bot = _bot(tmp_path=tmp_path)
    for inst in bot._cfg.instances.values():
        inst.config.twofa_pending_file.touch()
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    mock_delete.assert_not_called()


def test_handle_message_digit_string_prompts_disambiguation_when_no_pending_login_multi_instance(
    tmp_path,
):
    """Plain digit messages with no pending login and multiple instances send a
    disambiguation prompt asking the user to specify the instance."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456"})
    mock_send.assert_called_once()
    assert "/code" in mock_send.call_args.args[0]


def test_maybe_submit_pending_code_single_instance_no_pending_marker_warns(tmp_path):
    """Single instance but no pending marker → submit_code_to sends warning, returns False."""
    single_instance = {
        "user1": InstanceConfig(
            name="User1", config=_make_config(tmp_path / "user1", "user1")
        )
    }
    bot = _bot(instances=single_instance, tmp_path=tmp_path)
    # No pending marker file created
    with patch.object(bot, "_send_message") as mock_send:
        result = bot._maybe_submit_pending_code("123456")
    assert result is False
    mock_send.assert_called_once()
    assert "No active login" in mock_send.call_args.args[0]


def test_maybe_submit_pending_code_single_instance_with_pending_submits(tmp_path):
    """Single instance with pending marker → code is written to file."""
    single_instance = {
        "user1": InstanceConfig(
            name="User1", config=_make_config(tmp_path / "user1", "user1")
        )
    }
    bot = _bot(instances=single_instance, tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    inst.config.twofa_pending_file.touch()

    with patch.object(bot, "_send_message"):
        result = bot._maybe_submit_pending_code("123456")
    assert result is True
    assert inst.config.twofa_code_file.read_text() == "123456"


def test_handle_message_digit_cron_single_instance_submits_code(tmp_path):
    """Replying with a digit-only code should work for single-instance setups
    (sync-triggered 2FA) and delete the sensitive message."""
    single_instance = {
        "user1": InstanceConfig(
            name="User1", config=_make_config(tmp_path / "user1", "user1")
        )
    }
    bot = _bot(instances=single_instance, tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    inst.config.twofa_pending_file.touch()

    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    assert inst.config.twofa_code_file.read_text() == "123456"
    mock_delete.assert_called_once_with(77)


def test_maybe_submit_pending_code_multi_instance_no_pending_sends_disambiguation(
    tmp_path,
):
    """When no pending markers exist, sends disambiguation prompt."""
    bot = _bot(tmp_path=tmp_path)  # user1 + user2, no pending markers
    with (
        patch.object(bot, "_send_message") as mock_send,
    ):
        result = bot._maybe_submit_pending_code("123456")
    assert result is False
    mock_send.assert_called_once()
    sent = mock_send.call_args.args[0]
    assert "/code" in sent


def test_handle_message_digit_cron_multi_instance_sends_disambiguation(tmp_path):
    """Replying with a digit-only code with multiple instances and no pending markers
    asks the user to disambiguate."""
    bot = _bot(tmp_path=tmp_path)  # user1 + user2, no pending markers
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "123456"})
    mock_send.assert_called_once()
    assert "/code" in mock_send.call_args.args[0]


def test_maybe_submit_pending_code_single_file_pending_submits_directly(tmp_path):
    """When exactly one instance has a pending marker, the code is submitted to that instance."""
    bot = _bot(tmp_path=tmp_path)  # user1 + user2
    user1_inst = bot._cfg.instances["user1"]
    user1_inst.config.twofa_pending_file.touch()

    with patch.object(bot, "_send_message"):
        result = bot._maybe_submit_pending_code("123456")

    assert result is True
    assert user1_inst.config.twofa_code_file.read_text() == "123456"


def test_maybe_submit_pending_code_multiple_file_pending_sends_disambiguation(tmp_path):
    """When _pending_login is empty but multiple instances have pending markers,
    the user is asked to specify with /code <instance> <code>."""
    bot = _bot(tmp_path=tmp_path)  # user1 + user2
    for inst in bot._cfg.instances.values():
        inst.config.twofa_pending_file.touch()

    with (
        patch.object(bot, "_send_message") as mock_send,
    ):
        result = bot._maybe_submit_pending_code("123456")

    assert result is False
    mock_send.assert_called_once()
    sent = mock_send.call_args.args[0]
    assert "/code" in sent


def test_handle_message_digit_single_file_pending_submits_and_deletes(tmp_path):
    """Plain-digit reply on multi-instance setup is submitted and deleted when
    exactly one instance has a pending marker file."""
    bot = _bot(tmp_path=tmp_path)  # user1 + user2
    user1_inst = bot._cfg.instances["user1"]
    user1_inst.config.twofa_pending_file.touch()

    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})

    assert user1_inst.config.twofa_code_file.read_text() == "123456"
    mock_delete.assert_called_once_with(77)


def test_probe_pending_short_circuits_after_two(tmp_path):
    """_probe_pending must stop after finding two pending instances."""
    three_instances = {
        "user1": InstanceConfig(
            name="User1", config=_make_config(tmp_path / "user1", "user1")
        ),
        "user2": InstanceConfig(
            name="User2", config=_make_config(tmp_path / "user2", "user2")
        ),
        "user3": InstanceConfig(
            name="User3", config=_make_config(tmp_path / "user3", "user3")
        ),
    }
    bot = _bot(instances=three_instances, tmp_path=tmp_path)
    # Mark user1 and user2 as pending using instance-suffixed paths
    bot._cfg.instances["user1"].config.twofa_pending_file.touch()
    bot._cfg.instances["user2"].config.twofa_pending_file.touch()
    # Also touch user3 — probe should short-circuit before reaching it
    bot._cfg.instances["user3"].config.twofa_pending_file.touch()

    result = bot._probe_pending(three_instances)
    # Only two are returned (short-circuits after 2 found)
    assert len(result) == 2


def test_handle_message_unknown_plain_text_ignored_from_other_chat(tmp_path):
    """Messages from unauthorized chats are never answered."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 99}, "text": "hello"})
    mock_send.assert_not_called()


def test_cmd_code_writes_file_to_data_dir(tmp_path):
    """_cmd_code writes the authenticator code to the instance-suffixed 2FA path."""
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    inst.config.twofa_pending_file.touch()

    with patch.object(bot, "_send_message"):
        bot._cmd_code(["user1", "123456"])
    assert inst.config.twofa_code_file.read_text() == "123456"


def test_cmd_code_missing_args_sends_usage(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_code(["user1"])
    mock_send.assert_called_once()
    assert "code" in mock_send.call_args.args[0].lower()


def test_cmd_code_unknown_instance_sends_error(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_code(["nobody", "123456"])
    mock_send.assert_called_once()
    assert "nobody" in mock_send.call_args.args[0]


def test_cmd_code_non_digit_code_sends_error(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_code(["user1", "abc123"])
    mock_send.assert_called_once()


def test_handle_message_dispatches_code(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_cmd_code") as mock_code:
        bot._handle_message({"chat": {"id": 42}, "text": "/code user1 123456"})
    mock_code.assert_called_once_with(["user1", "123456"])


def test_cmd_code_no_pending_marker_sends_warning(tmp_path):
    """When pending marker is absent, _cmd_code sends a warning (no active login)."""
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    inst.config.data_dir.mkdir(parents=True, exist_ok=True)
    # No pending marker

    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_code(["user1", "123456"])
    # Two messages: the "Sending code..." message and the warning
    assert mock_send.call_count == 2
    messages = [call.args[0] for call in mock_send.call_args_list]
    assert any("No active login" in m for m in messages)


# ---------------------------------------------------------------------------
# TelegramBot._send_message
# ---------------------------------------------------------------------------


def test_send_message_posts_to_telegram(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._send_message("hello")
    url = mock_post.call_args.args[0]
    assert "sendMessage" in url
    payload = mock_post.call_args.kwargs["json"]
    assert payload["chat_id"] == "42"
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "MarkdownV2"


def test_send_message_with_keyboard_includes_reply_markup(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    keyboard = [[{"text": "A", "callback_data": "a"}]]
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._send_message("pick", keyboard=keyboard)
    payload = mock_post.call_args.kwargs["json"]
    assert "reply_markup" in payload
    assert payload["reply_markup"]["inline_keyboard"] == keyboard


def test_send_message_does_not_raise_on_request_exception(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(
        bot._session, "post", side_effect=requests.RequestException("network error")
    ):
        bot._send_message("hello")  # must not raise


# ---------------------------------------------------------------------------
# TelegramBot._answer_callback_query
# ---------------------------------------------------------------------------


def test_answer_callback_query_calls_api(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._answer_callback_query("cq123")
    url = mock_post.call_args.args[0]
    assert "answerCallbackQuery" in url
    assert mock_post.call_args.kwargs["json"]["callback_query_id"] == "cq123"


def test_answer_callback_query_does_not_raise_on_failure(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(
        bot._session, "post", side_effect=requests.RequestException("fail")
    ):
        bot._answer_callback_query("cq1")  # must not raise


# ---------------------------------------------------------------------------
# TelegramBot._handle_update — routing
# ---------------------------------------------------------------------------


def test_handle_update_routes_message(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_handle_message") as mock_msg:
        bot._handle_update(
            {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/help"}}
        )
    mock_msg.assert_called_once()


def test_handle_update_routes_callback_query(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_handle_callback_query") as mock_cb:
        bot._handle_update(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cq1",
                    "data": "noop",
                    "message": {"chat": {"id": 42}},
                },
            }
        )
    mock_cb.assert_called_once()


def test_handle_update_ignores_unknown_type(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_handle_message") as mock_msg,
        patch.object(bot, "_handle_callback_query") as mock_cb,
    ):
        bot._handle_update({"update_id": 1, "edited_message": {"text": "hi"}})
    mock_msg.assert_not_called()
    mock_cb.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._cmd_logs
# ---------------------------------------------------------------------------


def test_cmd_logs_launches_fetch_in_thread(tmp_path):
    """_cmd_logs must launch _fetch_and_send_logs in a background thread (no picker)."""
    bot = _bot(tmp_path=tmp_path)
    with patch("app.bot.threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        bot._cmd_logs([])
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["target"] == bot._fetch_and_send_logs


def test_handle_message_dispatches_logs(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_cmd_logs") as mock_logs:
        bot._handle_message({"chat": {"id": 42}, "text": "/logs"})
    mock_logs.assert_called_once()


# ---------------------------------------------------------------------------
# TelegramBot callback logs: legacy backward compat
# ---------------------------------------------------------------------------


def test_legacy_callback_logs_dispatches_fetch_and_send_logs(tmp_path):
    """A legacy logs:<instance> callback must trigger _fetch_and_send_logs (shared log)."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "logs:user1",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["target"] == bot._fetch_and_send_logs


def test_legacy_callback_logs_works_for_unknown_instance(tmp_path):
    """A legacy logs:<instance> callback must work even if the instance no longer exists.

    After the shared-log migration the instance name in the callback data is
    irrelevant — the shared log is fetched regardless.  Old inline buttons must
    not produce an "Unknown instance" error even if the instance was renamed or
    removed from the config.
    """
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch("app.bot.threading.Thread") as mock_thread,
        patch.object(bot, "_send_message") as mock_send,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "logs:deleted_instance",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["target"] == bot._fetch_and_send_logs
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._fetch_and_send_logs (shared log — no instance argument)
# ---------------------------------------------------------------------------


def test_fetch_and_send_logs_sends_todays_logs(tmp_path):
    import datetime as dt

    bot = _bot(tmp_path=tmp_path)
    log_dir = bot._cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    today = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")
    log_content = f"{today} 10:00:00 INFO sync_runner: all done\n"
    (log_dir / "sync.log").write_text(log_content)

    with patch.object(bot, "_send_message") as mock_send:
        bot._fetch_and_send_logs()
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert call_kwargs.kwargs.get("parse_mode") is None
    assert "all done" in call_kwargs.args[0]


def test_fetch_and_send_logs_no_log_file_sends_notice(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    # No sync.log file in log_dir

    with patch.object(bot, "_send_message") as mock_send:
        bot._fetch_and_send_logs()
    mock_send.assert_called_once()
    assert "No logs" in mock_send.call_args.args[0]


def test_fetch_and_send_logs_filters_non_today_lines(tmp_path):
    import datetime as dt

    bot = _bot(tmp_path=tmp_path)
    log_dir = bot._cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    today = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")
    log_content = (
        f"2000-01-01 10:00:00 INFO sync_runner: old line\n"
        f"{today} 10:00:00 INFO sync_runner: today line\n"
    )
    (log_dir / "sync.log").write_text(log_content)

    with patch.object(bot, "_send_message") as mock_send:
        bot._fetch_and_send_logs()
    sent_text = mock_send.call_args.args[0]
    assert "today line" in sent_text
    assert "old line" not in sent_text


def test_fetch_and_send_logs_truncates_long_output(tmp_path):
    import datetime as dt

    bot = _bot(tmp_path=tmp_path)
    log_dir = bot._cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    today = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")
    # Build a long log from today's lines
    long_log = "\n".join(
        f"{today} 10:00:00 INFO sync_runner: {'x' * 100}" for _ in range(100)
    )
    (log_dir / "sync.log").write_text(long_log)

    with patch.object(bot, "_send_message") as mock_send:
        bot._fetch_and_send_logs()
    sent_text = mock_send.call_args.args[0]
    assert "truncated" in sent_text


def test_fetch_and_send_logs_truncation_preserves_tail_drops_head(tmp_path):
    """When logs are truncated, the *last* lines must be kept and the *first* dropped.

    The streaming bounded-deque approach drops whole head lines to stay within
    _MAX_LOG_CHARS, so the text after the truncation marker always starts at a
    line boundary — unlike a character-level slice which can split mid-line.
    """
    import datetime as dt

    bot = _bot(tmp_path=tmp_path)
    log_dir = bot._cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    today = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")
    # 80 lines of ~140 chars each ≈ 11 200 chars > _MAX_LOG_CHARS (3 800)
    lines = [
        f"{today} 10:{i // 60:02d}:{i % 60:02d} INFO sync_runner: line-{i:03d} {'x' * 80}"
        for i in range(80)
    ]
    (log_dir / "sync.log").write_text("\n".join(lines))

    with patch.object(bot, "_send_message") as mock_send:
        bot._fetch_and_send_logs()
    sent_text = mock_send.call_args.args[0]

    assert "truncated" in sent_text
    assert "line-079" in sent_text, "last line must be preserved"
    assert "line-000" not in sent_text, "first line must be dropped when truncating"
    # After the truncation marker the first line of log content must start at a
    # line boundary (i.e. it begins with today's date), not mid-line.
    marker = "[... truncated ...]"
    marker_pos = sent_text.find(marker)
    assert marker_pos >= 0
    after_marker = sent_text[marker_pos + len(marker) :].lstrip("\n")
    assert after_marker.startswith(today), (
        "text after truncation marker must start at a line boundary, not mid-line"
    )


def test_fetch_and_send_logs_opens_log_file_with_utf8_encoding(tmp_path):
    """sync.log is written with UTF-8; reading it must also use UTF-8 explicitly.

    Without encoding="utf-8" the open() call falls back to the locale default,
    which can mis-decode log output on non-UTF-8 systems.
    """
    import datetime as dt
    from pathlib import Path

    bot = _bot(tmp_path=tmp_path)
    log_dir = bot._cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    today = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")
    (log_dir / "sync.log").write_text(
        f"{today} 10:00:00 INFO sync_runner: done\n", encoding="utf-8"
    )

    open_kwargs: list[dict] = []
    real_path_open = Path.open

    def recording_open(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.name == "sync.log":
            open_kwargs.append(dict(kwargs))
        return real_path_open(self, *args, **kwargs)

    with (
        patch("pathlib.Path.open", recording_open),
        patch.object(bot, "_send_message"),
    ):
        bot._fetch_and_send_logs()

    assert open_kwargs, "sync.log was never opened via Path.open()"
    for kwargs in open_kwargs:
        assert kwargs.get("encoding") == "utf-8", (
            f"sync.log must be opened with encoding='utf-8', got {kwargs}"
        )


def test_fetch_and_send_logs_sends_error_on_read_exception(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    log_dir = bot._cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "sync.log").write_text("dummy")

    with (
        patch("pathlib.Path.open", side_effect=OSError("permission denied")),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._fetch_and_send_logs()
    mock_send.assert_called_once()
    assert "permission denied" in mock_send.call_args.args[0]


def test_fetch_and_send_logs_header_has_no_markdown_chars_when_logs_present(tmp_path):
    """When log content is sent with parse_mode=None, the header must be plain text.

    MarkdownV2 escape characters (*  \\) must not appear in the header portion of
    the message, otherwise they will be displayed literally in Telegram.
    """
    import datetime as dt

    bot = _bot(tmp_path=tmp_path)
    log_dir = bot._cfg.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    today = dt.datetime.now(tz=dt.UTC).strftime("%Y-%m-%d")
    (log_dir / "sync.log").write_text(f"{today} 10:00:00 INFO sync_runner: done\n")

    with patch.object(bot, "_send_message") as mock_send:
        bot._fetch_and_send_logs()

    call = mock_send.call_args
    assert call.kwargs.get("parse_mode") is None
    sent_text = call.args[0]
    # Extract the header (everything before the first log line)
    header = sent_text.split(today)[0]
    assert "*" not in header, (
        f"MarkdownV2 bold markers found in plain-text header: {header!r}"
    )
    assert "\\" not in header, (
        f"MarkdownV2 escapes found in plain-text header: {header!r}"
    )


# ---------------------------------------------------------------------------
# _register_commands includes /logs
# ---------------------------------------------------------------------------


def test_register_commands_includes_logs(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    commands = mock_post.call_args.kwargs["json"]["commands"]
    cmd_names = [c["command"] for c in commands]
    assert "logs" in cmd_names


# ---------------------------------------------------------------------------
# _send_message parse_mode param
# ---------------------------------------------------------------------------


def test_send_message_default_parse_mode_is_markdownv2(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._send_message("hello")
    payload = mock_post.call_args.kwargs["json"]
    assert payload.get("parse_mode") == "MarkdownV2"


def test_send_message_no_parse_mode_omits_field(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch.object(bot._session, "post", return_value=mock_resp) as mock_post:
        bot._send_message("plain text", parse_mode=None)
    payload = mock_post.call_args.kwargs["json"]
    assert "parse_mode" not in payload


# ---------------------------------------------------------------------------
# _auth_icon
# ---------------------------------------------------------------------------


def test_auth_icon_true():
    assert _auth_icon(True) == "✅"


def test_auth_icon_false():
    assert _auth_icon(False) == "⚠️"


def test_auth_icon_none():
    assert _auth_icon(None) == "❓"


# ---------------------------------------------------------------------------
# _format_sync_timestamp
# ---------------------------------------------------------------------------


def test_format_sync_timestamp_iso_string():
    result = _format_sync_timestamp("2026-08-11T10:00:00+00:00")
    assert result == "2026/08/11 10:00 UTC"


def test_format_sync_timestamp_space_separated():
    result = _format_sync_timestamp("2026-08-11 10:00:00")
    assert result == "2026/08/11 10:00 UTC"


def test_format_sync_timestamp_invalid_returns_raw():
    result = _format_sync_timestamp("not-a-date")
    assert result == "not-a-date"


# ---------------------------------------------------------------------------
# _check_session_direct
# ---------------------------------------------------------------------------


def test_check_session_direct_no_cookies_returns_false(tmp_path):
    result = _check_session_direct(tmp_path, tmp_path / "sync.db", "user1")
    assert result is False


def test_check_session_direct_no_db_returns_true_when_cookie_valid(tmp_path):
    with patch("app.bot.has_valid_session", return_value=True):
        result = _check_session_direct(tmp_path, tmp_path / "sync.db", "user1")
    assert result is True


def test_check_session_direct_auth_state_failed_returns_false(tmp_path):
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)
    with EventRepository(db_path) as repo:
        repo.set_auth_state("user1", "failed")

    with patch("app.bot.has_valid_session", return_value=True):
        result = _check_session_direct(tmp_path, db_path, "user1")
    assert result is False


def test_check_session_direct_auth_state_ok_returns_true(tmp_path):
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)
    with EventRepository(db_path) as repo:
        repo.set_auth_state("user1", "ok")

    with patch("app.bot.has_valid_session", return_value=True):
        result = _check_session_direct(tmp_path, db_path, "user1")
    assert result is True


# ---------------------------------------------------------------------------
# _last_sync_summary_direct
# ---------------------------------------------------------------------------


def test_last_sync_summary_direct_no_db_returns_none(tmp_path):
    result = _last_sync_summary_direct(tmp_path / "sync.db", "user1")
    assert result is None


def test_last_sync_summary_direct_success_run(tmp_path):
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)
    with EventRepository(db_path) as repo:
        repo.set_sync_run("user1", status="success", saved=5, failed=0, excluded=1)

    result = _last_sync_summary_direct(db_path, "user1")
    assert result is not None
    assert "✅" in result
    assert "success" in result
    assert "saved 5" in result
    assert "excluded 1" in result


def test_last_sync_summary_direct_failed_run(tmp_path):
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)
    with EventRepository(db_path) as repo:
        repo.set_sync_run("user1", status="failed", saved=0, failed=2, excluded=0)

    result = _last_sync_summary_direct(db_path, "user1")
    assert result is not None
    assert "❌" in result
    assert "failed" in result


def test_last_sync_summary_direct_no_run_for_instance_returns_none(tmp_path):
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)
    with EventRepository(db_path) as repo:
        repo.set_sync_run("other", status="success", saved=1, failed=0, excluded=0)

    result = _last_sync_summary_direct(db_path, "user1")
    assert result is None


# ---------------------------------------------------------------------------
# _instance_status_direct
# ---------------------------------------------------------------------------


def test_instance_status_direct_no_cookies_no_db(tmp_path):
    """When there are no cookies and no DB, auth is False and last_sync is None."""
    result = _instance_status_direct(tmp_path, tmp_path / "sync.db", "user1")
    assert isinstance(result, InstanceStatus)
    assert result.auth is False
    assert result.last_sync is None


def test_instance_status_direct_valid_session_no_db(tmp_path):
    """With valid session but no DB, auth is True and last_sync is None."""
    with patch("app.bot.has_valid_session", return_value=True):
        result = _instance_status_direct(tmp_path, tmp_path / "sync.db", "user1")
    assert result.auth is True
    assert result.last_sync is None


def test_instance_status_direct_valid_session_with_sync_run(tmp_path):
    """With valid session and a sync run, both auth and last_sync are populated."""
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)
    with EventRepository(db_path) as repo:
        repo.set_auth_state("user1", "ok")
        repo.set_sync_run("user1", status="success", saved=3, failed=0, excluded=0)

    with patch("app.bot.has_valid_session", return_value=True):
        result = _instance_status_direct(tmp_path, db_path, "user1")
    assert result.auth is True
    assert result.last_sync is not None
    assert "success" in result.last_sync


def test_instance_status_direct_auth_failed_in_db(tmp_path):
    """When auth_state is 'failed' in DB, auth returns False."""
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)
    with EventRepository(db_path) as repo:
        repo.set_auth_state("user1", "failed")

    with patch("app.bot.has_valid_session", return_value=True):
        result = _instance_status_direct(tmp_path, db_path, "user1")
    assert result.auth is False


def test_instance_status_direct_db_error_returns_none_auth(tmp_path):
    """When the DB raises an exception, auth is None and last_sync is None."""
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    with (
        patch("app.bot.has_valid_session", return_value=True),
        patch.object(EventRepository, "__enter__", side_effect=Exception("boom")),
    ):
        result = _instance_status_direct(tmp_path, db_path, "user1")
    assert result.auth is None
    assert result.last_sync is None


def test_instance_status_direct_opens_only_one_connection(tmp_path):
    """_instance_status_direct must open exactly one EventRepository, not two."""
    from app.persistence import EventRepository, init_db

    db_path = tmp_path / "sync.db"
    init_db(db_path)

    open_count = 0
    original_enter = EventRepository.__enter__

    def counting_enter(self):
        nonlocal open_count
        open_count += 1
        return original_enter(self)

    with (
        patch("app.bot.has_valid_session", return_value=True),
        patch.object(EventRepository, "__enter__", counting_enter),
    ):
        _instance_status_direct(tmp_path, db_path, "user1")

    assert open_count == 1


# ---------------------------------------------------------------------------
# _cmd_status — direct checks (no Docker)
# ---------------------------------------------------------------------------


def test_cmd_status_shows_checkmark_for_authenticated_instance(tmp_path):
    """✅ icon when the session check passes for an instance."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch(
            "app.bot._instance_status_direct",
            return_value=InstanceStatus(
                auth=True, last_sync="✅ success at 2026/08/11 10:00 UTC"
            ),
        ),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "✅" in msg


def test_cmd_status_shows_warning_for_unauthenticated_instance(tmp_path):
    """⚠️ icon when the session check fails for an instance."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch(
            "app.bot._instance_status_direct",
            return_value=InstanceStatus(auth=False, last_sync=None),
        ),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "⚠️" in msg
    assert "unavailable" in msg


def test_cmd_status_shows_question_mark_for_unknown_state(tmp_path):
    """❓ icon when the session state is unknown (DB error)."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch(
            "app.bot._instance_status_direct",
            return_value=InstanceStatus(auth=None, last_sync=None),
        ),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "❓" in msg


def test_cmd_status_checks_each_instance(tmp_path):
    """_instance_status_direct must be called once per configured instance."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch(
            "app.bot._instance_status_direct",
            return_value=InstanceStatus(auth=True, last_sync=None),
        ) as mock_status,
        patch.object(bot, "_send_message"),
    ):
        bot._cmd_status([])
    assert mock_status.call_count == len(bot._cfg.instances)


# ---------------------------------------------------------------------------
def test_init_configures_ssl_once_at_startup():
    """TelegramBot.__init__ must call http_client.configure() once with the BotConfig ssl policy."""
    with patch("app.bot.http_client.configure") as mock_configure:
        TelegramBot(BotConfig(bot_token="tok", chat_id="42", allow_insecure_ssl=True))
    mock_configure.assert_called_once_with(allow_insecure_ssl=True)


# ---------------------------------------------------------------------------
# TelegramBot.run — polling loop
# ---------------------------------------------------------------------------


def test_run_sends_startup_message_without_help_reference(tmp_path):
    """run() must send a startup message that does not reference /help."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_register_commands"),
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_poll_once", side_effect=KeyboardInterrupt),
    ):
        bot.run()
    startup_msg = mock_send.call_args_list[0].args[0]
    assert "started" in startup_msg.lower() or "ready" in startup_msg.lower()
    assert "/help" not in startup_msg


def test_run_stops_on_keyboard_interrupt(tmp_path):
    """KeyboardInterrupt inside the loop causes run() to exit cleanly."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_register_commands"),
        patch.object(bot, "_send_message"),
        patch.object(bot, "_poll_once", side_effect=KeyboardInterrupt),
    ):
        bot.run()  # must not raise


def test_run_recovers_from_polling_exception_then_stops(tmp_path):
    """A generic exception is caught; a subsequent KeyboardInterrupt stops the loop."""
    bot = _bot(tmp_path=tmp_path)
    call_count = [0]

    def flaky_poll():
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("transient error")
        raise KeyboardInterrupt

    with (
        patch.object(bot, "_register_commands"),
        patch.object(bot, "_send_message"),
        patch.object(bot, "_poll_once", side_effect=flaky_poll),
        patch("app.bot.time.sleep"),
    ):
        bot.run()

    assert call_count[0] == 2


# ---------------------------------------------------------------------------
# TelegramBot._poll_once
# ---------------------------------------------------------------------------


def test_poll_once_dispatches_update(tmp_path):
    """_poll_once fetches updates and routes each one through _handle_update."""
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "result": [{"update_id": 10, "message": {"chat": {"id": 42}, "text": "/help"}}]
    }
    with (
        patch.object(bot._session, "get", return_value=mock_resp),
        patch.object(bot, "_handle_update") as mock_handle,
    ):
        bot._poll_once()
    mock_handle.assert_called_once()
    assert bot._offset == 11


def test_poll_once_advances_offset_for_multiple_updates(tmp_path):
    """Offset is set to last update_id + 1."""
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "result": [
            {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/help"}},
            {"update_id": 6, "message": {"chat": {"id": 42}, "text": "/status"}},
        ]
    }
    with (
        patch.object(bot._session, "get", return_value=mock_resp),
        patch.object(bot, "_handle_update"),
    ):
        bot._poll_once()
    assert bot._offset == 7


def test_poll_once_continues_on_handle_update_exception(tmp_path):
    """Exception inside _handle_update is caught; remaining updates are still processed."""
    bot = _bot(tmp_path=tmp_path)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "result": [
            {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/help"}},
            {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/status"}},
        ]
    }
    handle_calls = []

    def flaky_handle(update):
        handle_calls.append(update["update_id"])
        if update["update_id"] == 1:
            raise RuntimeError("boom")

    with (
        patch.object(bot._session, "get", return_value=mock_resp),
        patch.object(bot, "_handle_update", side_effect=flaky_handle),
    ):
        bot._poll_once()

    assert handle_calls == [1, 2]
    assert bot._offset == 3


# ---------------------------------------------------------------------------
# TelegramBot._cmd_status — no instances configured
# ---------------------------------------------------------------------------


def test_cmd_status_no_instances_sends_warning(tmp_path):
    """When no instances are configured, _cmd_status sends a clear warning."""
    bot = _bot(instances={}, tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_status([])
    mock_send.assert_called_once()
    assert "no instances" in mock_send.call_args.args[0].lower()


# ---------------------------------------------------------------------------
# TelegramBot._cmd_status — backup not configured
# ---------------------------------------------------------------------------


def test_cmd_status_mentions_backup_not_configured_when_absent(tmp_path):
    """When backup_cfg is None, the status message must say it is not configured."""
    bot = _bot(backup_cfg=None, tmp_path=tmp_path)
    with (
        patch(
            "app.bot._instance_status_direct",
            return_value=InstanceStatus(auth=None, last_sync=None),
        ),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "not configured" in msg


# ---------------------------------------------------------------------------
# TelegramBot._cmd_sync
# ---------------------------------------------------------------------------


def test_cmd_sync_sends_instance_picker_keyboard(tmp_path):
    """_cmd_sync must send a prompt with an inline keyboard of instances."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_sync([])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any(d.startswith("sync:") for d in cb_data)


# ---------------------------------------------------------------------------
# run() entry point
# ---------------------------------------------------------------------------


def test_run_entry_point_creates_bot_and_calls_run():
    """run() must build a BotConfig from env, construct a TelegramBot, and call bot.run()."""
    from app.bot import run

    mock_cfg = MagicMock()
    mock_bot = MagicMock()
    with (
        patch("app.bot.BotConfig.from_env", return_value=mock_cfg),
        patch("app.bot.TelegramBot", return_value=mock_bot) as mock_cls,
    ):
        run()

    mock_cls.assert_called_once_with(mock_cfg)
    mock_bot.run.assert_called_once()


# ---------------------------------------------------------------------------
# TelegramBot._cmd_resync — /resync command
# ---------------------------------------------------------------------------


def test_cmd_resync_no_args_sends_instance_picker(tmp_path):
    """/resync with no args must show an instance picker."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync([])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any(d.startswith("resync_pick_date:") for d in cb_data)


def test_cmd_resync_with_date_sends_instance_picker_for_date(tmp_path):
    """/resync 2026-07-15 must show an instance picker with the date encoded."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync(["2026-07-15"])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any("2026-07-15" in d for d in cb_data)


def test_cmd_resync_invalid_date_sends_error(tmp_path):
    """/resync with a non-date arg must send an error message."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync(["not-a-date"])
    mock_send.assert_called_once()
    msg = mock_send.call_args.args[0]
    assert "invalid" in msg.lower() or "YYYY" in msg


def test_cmd_resync_datetime_string_sends_error(tmp_path):
    """/resync with a full datetime string must send an error — only YYYY-MM-DD is valid."""
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync(["2026-07-15T12:00:00"])
    mock_send.assert_called_once()
    msg = mock_send.call_args.args[0]
    assert "invalid" in msg.lower() or "YYYY" in msg


# ---------------------------------------------------------------------------
# TelegramBot._handle_callback_query — resync callbacks
# ---------------------------------------------------------------------------


def test_callback_resync_pick_date_sends_date_keyboard(tmp_path):
    """resync_pick_date:<instance> callback must show a date-picker keyboard."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_resync_date_buttons", return_value=[[]]) as mock_dates,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "resync_pick_date:user1",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_dates.assert_called_once_with("user1")
    mock_send.assert_called_once()


def test_callback_resync_dispatches_launch_resync(tmp_path):
    """resync:<date>:<instance> callback must call _launch_resync."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_resync") as mock_launch,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "resync:2026-07-15:user1",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_launch.assert_called_once()
    inst, date_str = mock_launch.call_args.args
    assert date_str == "2026-07-15"
    assert inst.name == "User1"


def test_callback_resync_unknown_instance_replies(tmp_path):
    """resync:<date>:<unknown> callback must reply with error."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "resync:2026-07-15:nobody",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]


def test_callback_resync_pick_date_unknown_instance_replies(tmp_path):
    """resync_pick_date:<unknown> callback must reply with error."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "resync_pick_date:nobody",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]


def test_callback_resync_malformed_too_few_parts_does_not_raise(tmp_path):
    """resync callback with fewer than 3 parts must log warning and not raise."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "resync:2026-07-15",  # missing instance part
                "message": {"chat": {"id": 42}},
            }
        )
    mock_send.assert_not_called()


def test_launch_resync_sends_ack_and_starts_thread(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_resync(inst, "2026-07-15")
    mock_send.assert_called_once()
    mock_thread.assert_called_once()


def test_launch_resync_thread_target_is_run_resync_for_instance(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    with (
        patch.object(bot, "_send_message"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_resync(inst, "2026-07-15")
    assert mock_thread.call_args.kwargs["target"] == bot._run_resync_for_instance
    assert mock_thread.call_args.kwargs["args"] == (inst, "2026-07-15")


def test_run_resync_for_instance_calls_main_run_resync(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    inst = bot._cfg.instances["user1"]
    with patch("app.bot._main_run_resync") as mock_resync:
        bot._run_resync_for_instance(inst, "2026-07-15")
    mock_resync.assert_called_once_with("2026-07-15", cfg=inst.config)


# ---------------------------------------------------------------------------
# _register_commands — resync is registered
# ---------------------------------------------------------------------------


def test_register_commands_includes_resync(tmp_path):
    bot = _bot(tmp_path=tmp_path)
    with patch.object(bot._session, "post") as mock_post:
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        bot._register_commands()
    payload = mock_post.call_args.kwargs["json"]
    commands = [c["command"] for c in payload["commands"]]
    assert "resync" in commands


def test_register_commands_order_without_backup(tmp_path):
    """Commands must be registered in order: sync, status, logs, resync (no backup)."""
    bot = _bot(backup_cfg=None, tmp_path=tmp_path)
    with patch.object(bot._session, "post") as mock_post:
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        bot._register_commands()
    commands = [c["command"] for c in mock_post.call_args.kwargs["json"]["commands"]]
    assert commands == ["sync", "status", "logs", "resync"]


def test_register_commands_order_with_backup(tmp_path):
    """Commands must be registered in order: sync, status, logs, backup, resync."""
    bot = _bot(backup_cfg=_make_backup_config(tmp_path), tmp_path=tmp_path)
    with patch.object(bot._session, "post") as mock_post:
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        bot._register_commands()
    commands = [c["command"] for c in mock_post.call_args.kwargs["json"]["commands"]]
    assert commands == ["sync", "status", "logs", "backup", "resync"]


# ---------------------------------------------------------------------------
# Wiring — TelegramBot delegates to collaborators
# ---------------------------------------------------------------------------


def test_instance_buttons_delegates_to_bot_keyboards(tmp_path):
    """TelegramBot._instance_buttons must call the standalone function with instance names."""
    bot = _bot(tmp_path=tmp_path)
    with patch("app.bot._instance_buttons_fn", return_value=[[]]) as mock_fn:
        bot._instance_buttons("sync")
    mock_fn.assert_called_once_with("sync", ["User1", "User2"])


def test_month_buttons_delegates_to_bot_keyboards(tmp_path):
    """TelegramBot._month_buttons must delegate to bot_keyboards.month_buttons."""
    bot = _bot(tmp_path=tmp_path)
    with patch("app.bot._month_buttons_fn", return_value=[[]]) as mock_fn:
        bot._month_buttons()
    mock_fn.assert_called_once_with()


# ---------------------------------------------------------------------------
# _run_backup — direct backup calls
# ---------------------------------------------------------------------------


def test_run_backup_monthly_calls_backup_module(tmp_path):
    """_run_backup('monthly', '2026-07') must call backup.run_monthly directly."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch("app.bot.backup_module.run_monthly") as mock_run_monthly,
        patch("app.bot.WalletClient"),
        patch("app.bot.Notifier"),
    ):
        bot._run_backup("monthly", "2026-07")
    mock_run_monthly.assert_called_once()


def test_run_backup_yearly_calls_backup_module(tmp_path):
    """_run_backup('yearly', '2025') must call backup.run_yearly directly."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch("app.bot.backup_module.run_yearly") as mock_run_yearly,
        patch("app.bot.WalletClient"),
        patch("app.bot.Notifier"),
    ):
        bot._run_backup("yearly", "2025")
    mock_run_yearly.assert_called_once()


def test_run_backup_sends_error_on_exception(tmp_path):
    """_run_backup must catch exceptions and send an error message."""
    bot = _bot(tmp_path=tmp_path)
    with (
        patch("app.bot.backup_module.run_monthly", side_effect=RuntimeError("boom")),
        patch("app.bot.WalletClient"),
        patch("app.bot.Notifier"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._run_backup("monthly", "2026-07")
    mock_send.assert_called_once()
    assert "boom" in mock_send.call_args.args[0]
