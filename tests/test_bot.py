from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch

import pytest
import requests

from app.bot import (
    _BACKUP_ICONS,
    BotConfig,
    InstanceConfig,
    TelegramBot,
    _auth_icon,
    _docker_exec_silent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(
    instances: dict[str, InstanceConfig] | None = None,
    backup_container: str | None = "proj-backup-1",
) -> BotConfig:
    if instances is None:
        instances = {
            "david": InstanceConfig(name="David", container_name="proj-sync-david-1"),
            "eli": InstanceConfig(name="Eli", container_name="proj-sync-eli-1"),
        }
    return BotConfig(
        bot_token="tok",
        chat_id="42",
        instances=instances,
        backup_container=backup_container,
    )


def _bot(
    instances: dict[str, InstanceConfig] | None = None,
    backup_container: str | None = "proj-backup-1",
) -> TelegramBot:
    return TelegramBot(_cfg(instances, backup_container))


# ---------------------------------------------------------------------------
# BotConfig.from_env
# ---------------------------------------------------------------------------

_VALID_ENV = {
    "TELEGRAM_BOT_TOKEN": "mytoken",
    "TELEGRAM_CHAT_ID": "123",
    "INSTANCES": "david,eli",
    "CONTAINER_PREFIX": "myproject",
}


def test_botconfig_from_env_valid(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    cfg = BotConfig.from_env()
    assert cfg.bot_token == "mytoken"
    assert cfg.chat_id == "123"
    assert "david" in cfg.instances
    assert "eli" in cfg.instances
    # Sync containers include "sync-" prefix
    assert cfg.instances["david"].container_name == "myproject-sync-david-1"
    assert cfg.instances["eli"].container_name == "myproject-sync-eli-1"


def test_botconfig_from_env_backup_container_default(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    cfg = BotConfig.from_env()
    assert cfg.backup_container == "myproject-backup-1"


def test_botconfig_from_env_backup_service_custom(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BACKUP_SERVICE", "wallet-backup")
    cfg = BotConfig.from_env()
    assert cfg.backup_container == "myproject-wallet-backup-1"


def test_botconfig_from_env_backup_service_empty_disables_backup(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("BACKUP_SERVICE", "")
    cfg = BotConfig.from_env()
    assert cfg.backup_container is None


def test_botconfig_from_env_missing_token(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        BotConfig.from_env()


def test_botconfig_from_env_missing_chat_id(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    with pytest.raises(ValueError, match="TELEGRAM_CHAT_ID"):
        BotConfig.from_env()


def test_botconfig_from_env_missing_instances(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("INSTANCES")
    with pytest.raises(ValueError, match="INSTANCES"):
        BotConfig.from_env()


def test_botconfig_from_env_missing_prefix(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CONTAINER_PREFIX")
    with pytest.raises(ValueError, match="CONTAINER_PREFIX"):
        BotConfig.from_env()


def test_botconfig_from_env_instance_names_normalised(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("INSTANCES", " David , Eli ")  # extra spaces
    cfg = BotConfig.from_env()
    assert "david" in cfg.instances
    assert "eli" in cfg.instances


def test_botconfig_telegram_verify_ssl_default_true(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    cfg = BotConfig.from_env()
    assert cfg.telegram_verify_ssl is True


def test_botconfig_telegram_verify_ssl_false(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TELEGRAM_VERIFY_SSL", "false")
    cfg = BotConfig.from_env()
    assert cfg.telegram_verify_ssl is False


def test_botconfig_telegram_verify_ssl_invalid(monkeypatch):
    for k, v in _VALID_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("TELEGRAM_VERIFY_SSL", "maybe")
    with pytest.raises(ValueError, match="TELEGRAM_VERIFY_SSL"):
        BotConfig.from_env()


# ---------------------------------------------------------------------------
# TelegramBot._register_commands
# ---------------------------------------------------------------------------


def test_register_commands_includes_backup_when_configured():
    bot = _bot(backup_container="proj-backup-1")
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    commands = mock_post.call_args.kwargs["json"]["commands"]
    cmd_names = [c["command"] for c in commands]
    assert "sync" in cmd_names
    assert "backup" in cmd_names
    assert "backup_monthly" not in cmd_names
    assert "backup_yearly" not in cmd_names
    assert "status" in cmd_names
    assert "help" in cmd_names
    assert "login" in cmd_names


def test_register_commands_excludes_backup_when_not_configured():
    bot = _bot(backup_container=None)
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    commands = mock_post.call_args.kwargs["json"]["commands"]
    cmd_names = [c["command"] for c in commands]
    assert "backup" not in cmd_names
    assert "sync" in cmd_names


def test_register_commands_does_not_raise_on_failure():
    bot = _bot()
    with patch("app.bot.requests.post", side_effect=requests.RequestException("fail")):
        bot._register_commands()  # must not raise


# ---------------------------------------------------------------------------
# TelegramBot._handle_message — authorization
# ---------------------------------------------------------------------------


def test_handle_message_ignores_unauthorized_chat():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 9999}, "text": "/help"})
    mock_send.assert_not_called()


def test_handle_message_non_command_replies_commands_only():
    """Non-command plain text receives a 'commands only' reply (not silently ignored)."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "hello"})
    mock_send.assert_called_once()
    assert "command" in mock_send.call_args.args[0].lower()


def test_handle_message_unknown_command_replies():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "/unknown"})
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]


def test_handle_message_dispatches_help():
    bot = _bot()
    with patch.object(bot, "_cmd_help") as mock_help:
        bot._handle_message({"chat": {"id": 42}, "text": "/help"})
    mock_help.assert_called_once()


def test_handle_message_strips_bot_name_suffix():
    """Commands like /sync@MyBot should be treated as /sync."""
    bot = _bot()
    with patch.object(bot, "_cmd_sync") as mock_sync:
        bot._handle_message({"chat": {"id": 42}, "text": "/sync@MyBot"})
    mock_sync.assert_called_once()


def test_handle_message_deletes_code_message_for_privacy():
    """The /code message carries a sensitive 2FA code and must be deleted."""
    bot = _bot()
    with (
        patch.object(bot, "_cmd_code"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message(
            {"chat": {"id": 42}, "message_id": 555, "text": "/code david 123456"}
        )
    mock_delete.assert_called_once_with(555)


def test_handle_message_does_not_delete_non_code_commands():
    bot = _bot()
    with (
        patch.object(bot, "_cmd_sync"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "message_id": 555, "text": "/sync"})
    mock_delete.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._delete_message
# ---------------------------------------------------------------------------


def test_delete_message_calls_telegram_api():
    bot = _bot()
    with patch("app.bot.requests.post") as mock_post:
        bot._delete_message(555)
    mock_post.assert_called_once()
    payload = mock_post.call_args.kwargs["json"]
    assert payload["message_id"] == 555
    assert str(payload["chat_id"]) == bot._cfg.chat_id


def test_delete_message_does_not_raise_on_failure():
    bot = _bot()
    with patch("app.bot.requests.post", side_effect=requests.RequestException("fail")):
        bot._delete_message(555)  # must not raise


def test_delete_message_ignores_missing_id():
    bot = _bot()
    with patch("app.bot.requests.post") as mock_post:
        bot._delete_message(None)
    mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._cmd_help
# ---------------------------------------------------------------------------


def test_cmd_help_includes_backup_when_configured():
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_help([])
    msg = mock_send.call_args.args[0]
    assert "sync" in msg.lower()
    assert "backup" in msg.lower()


def test_cmd_help_excludes_backup_when_not_configured():
    bot = _bot(backup_container=None)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_help([])
    msg = mock_send.call_args.args[0]
    assert "sync" in msg.lower()
    assert "/backup" not in msg


# ---------------------------------------------------------------------------
# TelegramBot._cmd_backup
# ---------------------------------------------------------------------------


def test_cmd_backup_no_backup_container_sends_error():
    bot = _bot(backup_container=None)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup([])
    mock_send.assert_called_once()
    assert "not configured" in mock_send.call_args.args[0]


def test_cmd_backup_without_args_shows_type_keyboard():
    """Without args /backup should show a Monthly / Yearly type-selection keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup([])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any("backup_type:monthly" in d for d in cb_data)
    assert any("backup_type:yearly" in d for d in cb_data)


def test_cmd_backup_monthly_arg_shows_month_keyboard():
    """'/backup monthly' (no period) → show month selection keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup(["monthly"])
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert all(d.startswith("backup_monthly:") for d in cb_data)


def test_cmd_backup_yearly_arg_shows_year_keyboard():
    """'/backup yearly' (no year) → show year selection keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup(["yearly"])
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert all(d.startswith("backup_yearly:") for d in cb_data)


def test_cmd_backup_monthly_with_period_executes_directly():
    """/backup monthly YYYY-MM executes without showing a keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._cmd_backup(["monthly", "2026-07"])
    _, kwargs = mock_send.call_args
    assert kwargs.get("keyboard") is None
    mock_exec.assert_called_once_with(
        "proj-backup-1", ["backup", "monthly", "2026-07"], on_error=ANY
    )


def test_cmd_backup_yearly_with_year_executes_directly():
    """/backup yearly YYYY executes without showing a keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._cmd_backup(["yearly", "2025"])
    _, kwargs = mock_send.call_args
    assert kwargs.get("keyboard") is None
    mock_exec.assert_called_once_with(
        "proj-backup-1", ["backup", "yearly", "2025"], on_error=ANY
    )


def test_cmd_backup_unknown_type_sends_error():
    """/backup weekly → error message, no keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup(["weekly"])
    msg = mock_send.call_args.args[0]
    assert "weekly" in msg
    assert mock_send.call_args.kwargs.get("keyboard") is None


def test_handle_message_dispatches_backup():
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_cmd_backup") as mock_backup:
        bot._handle_message({"chat": {"id": 42}, "text": "/backup"})
    mock_backup.assert_called_once_with([])


def test_handle_message_dispatches_backup_with_args():
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_cmd_backup") as mock_backup:
        bot._handle_message({"chat": {"id": 42}, "text": "/backup monthly 2026-07"})
    mock_backup.assert_called_once_with(["monthly", "2026-07"])


# ---------------------------------------------------------------------------
# backup_type callback — type selection step
# ---------------------------------------------------------------------------


def test_callback_query_backup_type_monthly_shows_month_keyboard():
    """`backup_type:monthly` callback → show month selection keyboard."""
    bot = _bot(backup_container="proj-backup-1")
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


def test_callback_query_backup_type_yearly_shows_year_keyboard():
    """`backup_type:yearly` callback → show year selection keyboard."""
    bot = _bot(backup_container="proj-backup-1")
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


def test_launch_backup_monthly_sends_ack_and_starts_thread():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread"),
    ):
        bot._launch_backup("monthly", "2026-07")
    mock_send.assert_called_once()
    assert "2026" in mock_send.call_args.args[0]


def test_launch_backup_sends_error_when_backup_not_configured():
    """Guard: _launch_backup with no backup container → clear error, no crash."""
    bot = _bot(backup_container=None)
    with patch.object(bot, "_send_message") as mock_send:
        bot._launch_backup("monthly", "2026-07")
    mock_send.assert_called_once()
    assert "not configured" in mock_send.call_args.args[0].lower()


def test_callback_backup_monthly_with_no_backup_container_sends_error():
    """Stale inline keyboard: backup_monthly callback when backup not configured → error message."""
    bot = _bot(backup_container=None)
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


def test_callback_backup_yearly_with_no_backup_container_sends_error():
    """Stale inline keyboard: backup_yearly callback when backup not configured → error message."""
    bot = _bot(backup_container=None)
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


def test_callback_query_backup_monthly_dispatches_launch():
    """backup_monthly:YYYY-MM callback should trigger _launch_backup("monthly", period)."""
    bot = _bot(backup_container="proj-backup-1")
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


def test_launch_backup_monthly_executes_correct_args():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._launch_backup("monthly", "2026-07")
    mock_exec.assert_called_once_with(
        "proj-backup-1", ["backup", "monthly", "2026-07"], on_error=ANY
    )


def test_callback_query_backup_yearly_dispatches_launch():
    """backup_yearly:<year> callback should trigger _launch_backup("yearly", year)."""
    bot = _bot(backup_container="proj-backup-1")
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


def test_launch_backup_yearly_sends_ack_and_starts_thread():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread"),
    ):
        bot._launch_backup("yearly", "2025")
    mock_send.assert_called_once()
    assert "2025" in mock_send.call_args.args[0]


def test_launch_backup_yearly_executes_correct_args():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._launch_backup("yearly", "2024")
    mock_exec.assert_called_once_with(
        "proj-backup-1", ["backup", "yearly", "2024"], on_error=ANY
    )


# ---------------------------------------------------------------------------
# _BACKUP_ICONS — bot uses the right icon per backup mode
# ---------------------------------------------------------------------------


def test_launch_backup_uses_correct_icon_for_monthly():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread"),
    ):
        bot._launch_backup("monthly", "2026-07")
    assert _BACKUP_ICONS["monthly"] in mock_send.call_args.args[0]


def test_launch_backup_uses_correct_icon_for_yearly():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread"),
    ):
        bot._launch_backup("yearly", "2025")
    assert _BACKUP_ICONS["yearly"] in mock_send.call_args.args[0]


# ---------------------------------------------------------------------------
# TelegramBot._exec_in_thread
# ---------------------------------------------------------------------------


def test_exec_in_thread_starts_daemon_thread():
    """_exec_in_thread must start a daemon thread targeting _docker_exec_silent."""
    bot = _bot()
    with patch("app.bot.threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        bot._exec_in_thread("my-container", ["sync"], on_error=None)
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["daemon"] is True
    mock_thread.return_value.start.assert_called_once()


def test_exec_in_thread_passes_on_error_and_on_success():
    on_error = MagicMock()
    on_success = MagicMock()
    bot = _bot()
    with patch("app.bot.threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        bot._exec_in_thread("c", ["login"], on_error=on_error, on_success=on_success)
    kwargs = mock_thread.call_args.kwargs
    assert kwargs["kwargs"]["on_error"] is on_error
    assert kwargs["kwargs"]["on_success"] is on_success


# ---------------------------------------------------------------------------
# TelegramBot._handle_callback_query
# ---------------------------------------------------------------------------


def test_callback_query_noop_is_acknowledged_and_ignored():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query") as mock_ack,
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "noop", "message": {"chat": {"id": 42}}}
        )
    mock_ack.assert_called_once_with("cq1")
    mock_sync.assert_not_called()


def test_callback_query_unauthorized_chat_ignored():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "sync:david", "message": {"chat": {"id": 9999}}}
        )
    mock_sync.assert_not_called()


def test_callback_query_sync_dispatches_launch_sync():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "sync:david", "message": {"chat": {"id": 42}}}
        )
    mock_sync.assert_called_once()
    assert mock_sync.call_args.args[0].name == "David"


def test_callback_query_unknown_instance_replies():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "sync:nobody", "message": {"chat": {"id": 42}}}
        )
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]


def test_callback_query_malformed_data_does_not_raise():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message"),
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "malformed", "message": {"chat": {"id": 42}}}
        )
    # must not raise


def test_callback_query_unknown_cmd_logs_warning_and_does_not_raise():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "badcmd:david", "message": {"chat": {"id": 42}}}
        )
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._launch_sync
# ---------------------------------------------------------------------------


def test_launch_sync_sends_ack_and_starts_thread():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread"),
    ):
        bot._launch_sync(inst)
    mock_send.assert_called_once()
    assert "David" in mock_send.call_args.args[0]


def test_launch_sync_passes_correct_args_to_exec():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._launch_sync(inst)
    mock_exec.assert_called_once_with(inst.container_name, ["sync"], on_error=ANY)


# ---------------------------------------------------------------------------
# TelegramBot._cmd_login / _launch_login
# ---------------------------------------------------------------------------


def test_cmd_login_sends_keyboard_with_instances():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_login([])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs["keyboard"]
    all_buttons = [btn for row in keyboard for btn in row]
    labels = [b["text"] for b in all_buttons]
    assert "David" in labels
    assert "Eli" in labels


def test_login_buttons_callback_data_encodes_login_cmd():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_login([])
    keyboard = mock_send.call_args.kwargs["keyboard"]
    cb_data = [b["callback_data"] for row in keyboard for b in row]
    assert all(d.startswith("login:") for d in cb_data)


def test_callback_query_login_dispatches_launch_login():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_login") as mock_login,
    ):
        bot._handle_callback_query(
            {"id": "cq1", "data": "login:david", "message": {"chat": {"id": 42}}}
        )
    mock_login.assert_called_once()
    assert mock_login.call_args.args[0].name == "David"


def test_launch_login_sends_ack_and_starts_thread():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread"),
    ):
        bot._launch_login(inst)
    mock_send.assert_called_once()
    assert "David" in mock_send.call_args.args[0]


def test_launch_login_passes_correct_args_to_exec():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._launch_login(inst)
    call_kwargs = mock_exec.call_args.kwargs
    assert mock_exec.call_args.args == (inst.container_name, ["login"])
    assert call_kwargs["on_error"] is not None
    assert call_kwargs["on_success"] is not None


def test_launch_login_reports_success_via_on_success():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_launch_sync"),
    ):
        bot._launch_login(inst)
        on_success = mock_exec.call_args.kwargs["on_success"]
        mock_send.reset_mock()
        on_success()
    mock_send.assert_called_once()
    assert "David" in mock_send.call_args.args[0]


def test_launch_login_auto_syncs_on_success():
    """After a successful login, the bot automatically triggers a sync for the same instance."""
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._launch_login(inst)
        on_success = mock_exec.call_args.kwargs["on_success"]
        on_success()
    mock_sync.assert_called_once_with(inst)


# ---------------------------------------------------------------------------
# TelegramBot — pending login state & digit-intercept for 2FA
# ---------------------------------------------------------------------------


def test_launch_login_marks_instance_as_pending():
    """While login exec is running, the instance should be in _pending_login."""
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread"),
    ):
        bot._launch_login(inst)
    assert "david" in bot._pending_login


def test_on_login_success_removes_pending_state():
    """After success, the instance is removed from _pending_login."""
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_launch_sync"),
    ):
        bot._launch_login(inst)
        assert "david" in bot._pending_login
        on_success = mock_exec.call_args.kwargs["on_success"]
        on_success()
    assert "david" not in bot._pending_login


def test_on_login_error_removes_pending_state():
    """After an error, the instance is also removed from _pending_login."""
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._launch_login(inst)
        assert "david" in bot._pending_login
        on_error = mock_exec.call_args.kwargs["on_error"]
        on_error("❌ failed")
    assert "david" not in bot._pending_login


def test_handle_message_digit_string_submitted_to_pending_instance():
    """A digit-only reply is treated as 2FA code when exactly one instance is pending."""
    bot = _bot()
    inst = bot._cfg.instances["david"]
    bot._pending_login["david"] = inst
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    mock_exec.assert_called_once_with(
        inst.container_name, ["submit-code", "123456"], on_error=ANY
    )
    mock_delete.assert_called_once_with(77)


def test_handle_message_digit_string_not_deleted_when_no_pending_login_multi_instance():
    """Digit messages with no pending login and multiple instances send a disambiguation
    prompt, but do not submit code or delete the message."""
    bot = _bot()
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    mock_exec.assert_not_called()
    mock_send.assert_called_once()
    assert "/code" in mock_send.call_args.args[0]
    mock_delete.assert_not_called()


def test_handle_message_digit_string_not_deleted_when_multiple_pending():
    """Digit message is not deleted when multiple instances are pending (just a prompt is sent)."""
    bot = _bot()
    bot._pending_login["david"] = bot._cfg.instances["david"]
    bot._pending_login["eli"] = bot._cfg.instances["eli"]
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    mock_exec.assert_not_called()
    mock_delete.assert_not_called()


def test_handle_message_digit_string_prompts_disambiguation_when_no_pending_login_multi_instance():
    """Plain digit messages with no pending login and multiple instances send a
    disambiguation prompt asking the user to specify the instance."""
    bot = _bot()
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456"})
    mock_exec.assert_not_called()
    mock_send.assert_called_once()
    assert "/code" in mock_send.call_args.args[0]


def test_handle_message_digit_string_sends_prompt_when_multiple_pending():
    """When multiple instances are pending, ask which one the code is for."""
    bot = _bot()
    bot._pending_login["david"] = bot._cfg.instances["david"]
    bot._pending_login["eli"] = bot._cfg.instances["eli"]
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456"})
    mock_exec.assert_not_called()
    mock_send.assert_called_once()
    sent_text = mock_send.call_args.args[0]
    assert "david" in sent_text.lower() or "eli" in sent_text.lower()


def test_maybe_submit_pending_code_snapshots_dict_to_avoid_race():
    """_maybe_submit_pending_code must snapshot _pending_login before iterating
    so a concurrent mutation from a worker thread doesn't cause RuntimeError."""
    bot = _bot()
    inst = bot._cfg.instances["david"]
    bot._pending_login["david"] = inst

    # Simulate the worker thread clearing pending state mid-iteration by
    # patching _exec_in_thread to mutate _pending_login before returning.
    def clear_pending(*_args, **_kwargs):
        bot._pending_login.clear()

    with (
        patch.object(bot, "_exec_in_thread", side_effect=clear_pending),
        patch.object(bot, "_send_message"),
    ):
        # Must not raise RuntimeError even though the dict is mutated mid-call.
        result = bot._maybe_submit_pending_code("123456")
    assert result is True

    """Non-command, non-digit text receives a 'commands only' reply."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "hello there"})
    mock_send.assert_called_once()
    assert "command" in mock_send.call_args.args[0].lower()


def test_maybe_submit_pending_code_single_instance_no_pending_submits_directly():
    """When _pending_login is empty and there is exactly one configured instance,
    a plain-digit message should be submitted to that instance (cron 2FA fallback)."""
    single_instance = {
        "david": InstanceConfig(name="David", container_name="proj-sync-david-1")
    }
    bot = _bot(instances=single_instance)
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message"),
    ):
        result = bot._maybe_submit_pending_code("123456")
    assert result is True
    mock_exec.assert_called_once_with(
        "proj-sync-david-1", ["submit-code", "123456"], on_error=ANY
    )


def test_handle_message_digit_cron_single_instance_submits_code():
    """Replying with a digit-only code while _pending_login is empty should work
    for single-instance setups (cron-triggered 2FA) and delete the sensitive message."""
    single_instance = {
        "david": InstanceConfig(name="David", container_name="proj-sync-david-1")
    }
    bot = _bot(instances=single_instance)
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    mock_exec.assert_called_once_with(
        "proj-sync-david-1", ["submit-code", "123456"], on_error=ANY
    )
    mock_delete.assert_called_once_with(77)


def test_maybe_submit_pending_code_multi_instance_no_pending_sends_disambiguation():
    """When _pending_login is empty and there are multiple instances,
    a plain-digit message should prompt the user to use /code <instance> <code>."""
    bot = _bot()  # default has david + eli
    with (
        patch("app.bot._docker_check_awaiting_code", return_value=False),
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
    ):
        result = bot._maybe_submit_pending_code("123456")
    assert result is False
    mock_exec.assert_not_called()
    mock_send.assert_called_once()
    sent = mock_send.call_args.args[0]
    assert "/code" in sent


def test_handle_message_digit_cron_multi_instance_sends_disambiguation():
    """Replying with a digit-only code while _pending_login is empty with multiple
    instances should ask the user to disambiguate."""
    bot = _bot()  # david + eli
    with (
        patch("app.bot._docker_check_awaiting_code", return_value=False),
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456"})
    mock_exec.assert_not_called()
    mock_send.assert_called_once()
    assert "/code" in mock_send.call_args.args[0]


def test_maybe_submit_pending_code_docker_single_awaiting_submits_directly():
    """When _pending_login is empty but exactly one container is awaiting a code
    (detected via Docker check-pending), the code is submitted to that instance."""
    bot = _bot()  # david + eli
    david = bot._cfg.instances["david"]

    def docker_check(container_name: str, client=None) -> bool:
        return container_name == david.container_name

    with (
        patch("app.bot._docker_check_awaiting_code", side_effect=docker_check),
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message"),
    ):
        result = bot._maybe_submit_pending_code("123456")

    assert result is True
    mock_exec.assert_called_once_with(
        david.container_name, ["submit-code", "123456"], on_error=ANY
    )


def test_maybe_submit_pending_code_docker_multiple_awaiting_sends_disambiguation():
    """When _pending_login is empty but multiple containers are awaiting a code,
    the user is asked to specify with /code <instance> <code>."""
    bot = _bot()  # david + eli

    with (
        patch("app.bot._docker_check_awaiting_code", return_value=True),
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
    ):
        result = bot._maybe_submit_pending_code("123456")

    assert result is False
    mock_exec.assert_not_called()
    sent = mock_send.call_args.args[0]
    assert "/code" in sent


def test_handle_message_digit_docker_single_awaiting_submits_and_deletes():
    """Plain-digit reply on multi-instance setup is submitted and deleted when
    exactly one container is awaiting a code via Docker pending check."""
    bot = _bot()  # david + eli
    david = bot._cfg.instances["david"]

    def docker_check(container_name: str, client=None) -> bool:
        return container_name == david.container_name

    with (
        patch("app.bot._docker_check_awaiting_code", side_effect=docker_check),
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message"),
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})

    mock_exec.assert_called_once()
    mock_delete.assert_called_once_with(77)


def test_handle_message_unknown_plain_text_ignored_from_other_chat():
    """Messages from unauthorized chats are never answered."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 99}, "text": "hello"})
    mock_send.assert_not_called()


def test_cmd_code_executes_submit_code_for_instance():
    bot = _bot()
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._cmd_code(["david", "123456"])
    mock_exec.assert_called_once_with(
        "proj-sync-david-1", ["submit-code", "123456"], on_error=ANY
    )
    mock_send.assert_called_once()


def test_cmd_code_missing_args_sends_usage():
    bot = _bot()
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        bot._cmd_code(["david"])
    mock_thread.assert_not_called()
    mock_send.assert_called_once()
    assert "code" in mock_send.call_args.args[0].lower()


def test_cmd_code_unknown_instance_sends_error():
    bot = _bot()
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        bot._cmd_code(["nobody", "123456"])
    mock_thread.assert_not_called()
    mock_send.assert_called_once()
    assert "nobody" in mock_send.call_args.args[0]


def test_cmd_code_non_digit_code_sends_error():
    bot = _bot()
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        bot._cmd_code(["david", "abc123"])
    mock_thread.assert_not_called()
    mock_send.assert_called_once()


def test_handle_message_dispatches_code():
    bot = _bot()
    with patch.object(bot, "_cmd_code") as mock_code:
        bot._handle_message({"chat": {"id": 42}, "text": "/code david 123456"})
    mock_code.assert_called_once_with(["david", "123456"])


# ---------------------------------------------------------------------------
# TelegramBot._send_message
# ---------------------------------------------------------------------------


def test_send_message_posts_to_telegram():
    bot = _bot()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._send_message("hello")
    url = mock_post.call_args.args[0]
    assert "sendMessage" in url
    payload = mock_post.call_args.kwargs["json"]
    assert payload["chat_id"] == "42"
    assert payload["text"] == "hello"
    assert payload["parse_mode"] == "MarkdownV2"


def test_send_message_with_keyboard_includes_reply_markup():
    bot = _bot()
    keyboard = [[{"text": "A", "callback_data": "a"}]]
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._send_message("pick", keyboard=keyboard)
    payload = mock_post.call_args.kwargs["json"]
    assert "reply_markup" in payload
    assert payload["reply_markup"]["inline_keyboard"] == keyboard


def test_send_message_does_not_raise_on_request_exception():
    bot = _bot()
    with patch(
        "app.bot.requests.post", side_effect=requests.RequestException("network error")
    ):
        bot._send_message("hello")  # must not raise


# ---------------------------------------------------------------------------
# TelegramBot._answer_callback_query
# ---------------------------------------------------------------------------


def test_answer_callback_query_calls_api():
    bot = _bot()
    mock_resp = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._answer_callback_query("cq123")
    url = mock_post.call_args.args[0]
    assert "answerCallbackQuery" in url
    assert mock_post.call_args.kwargs["json"]["callback_query_id"] == "cq123"


def test_answer_callback_query_does_not_raise_on_failure():
    bot = _bot()
    with patch("app.bot.requests.post", side_effect=requests.RequestException("fail")):
        bot._answer_callback_query("cq1")  # must not raise


# ---------------------------------------------------------------------------
# TelegramBot._handle_update — routing
# ---------------------------------------------------------------------------


def test_handle_update_routes_message():
    bot = _bot()
    with patch.object(bot, "_handle_message") as mock_msg:
        bot._handle_update(
            {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/help"}}
        )
    mock_msg.assert_called_once()


def test_handle_update_routes_callback_query():
    bot = _bot()
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


def test_handle_update_ignores_unknown_type():
    bot = _bot()
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


def test_cmd_logs_sends_keyboard_with_instances():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_logs([])
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert "keyboard" in kwargs
    all_buttons = [btn for row in kwargs["keyboard"] for btn in row]
    labels = [b["text"] for b in all_buttons]
    assert "David" in labels
    assert "Eli" in labels


def test_cmd_logs_callback_data_encodes_logs_cmd():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_logs([])
    _, kwargs = mock_send.call_args
    cb_data = [b["callback_data"] for row in kwargs["keyboard"] for b in row]
    assert all(d.startswith("logs:") for d in cb_data)


def test_handle_message_dispatches_logs():
    bot = _bot()
    with patch.object(bot, "_cmd_logs") as mock_logs:
        bot._handle_message({"chat": {"id": 42}, "text": "/logs"})
    mock_logs.assert_called_once()


# ---------------------------------------------------------------------------
# TelegramBot callback logs:
# ---------------------------------------------------------------------------


def test_callback_query_logs_dispatches_fetch_and_send_logs():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "logs:david",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["target"] == bot._fetch_and_send_logs
    assert mock_thread.call_args.kwargs["args"][0].name == "David"


# ---------------------------------------------------------------------------
# TelegramBot._fetch_and_send_logs
# ---------------------------------------------------------------------------


def test_fetch_and_send_logs_sends_todays_logs():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch(
            "app.bot._docker_logs_today", return_value="INFO sync: all done\n"
        ) as mock_logs,
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._fetch_and_send_logs(inst)
    mock_logs.assert_called_once()
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    # parse_mode should be None so log text is sent as plain text
    assert call_kwargs.kwargs.get("parse_mode") is None
    assert "all done" in call_kwargs.args[0]


def test_fetch_and_send_logs_empty_logs_sends_notice():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch("app.bot._docker_logs_today", return_value=""),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._fetch_and_send_logs(inst)
    mock_send.assert_called_once()
    assert "No logs" in mock_send.call_args.args[0]


def test_fetch_and_send_logs_truncates_long_output():
    from app.bot import _MAX_LOG_CHARS

    bot = _bot()
    inst = bot._cfg.instances["david"]
    long_log = "x" * (_MAX_LOG_CHARS + 500)
    with (
        patch("app.bot._docker_logs_today", return_value=long_log),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._fetch_and_send_logs(inst)
    sent_text = mock_send.call_args.args[0]
    assert "truncated" in sent_text
    # No MarkdownV2 escape sequences should appear in the truncation marker
    assert "\\[" not in sent_text


def test_fetch_and_send_logs_sends_error_on_exception():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch(
            "app.bot._docker_logs_today", side_effect=Exception("container not found")
        ),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._fetch_and_send_logs(inst)
    mock_send.assert_called_once()
    assert "container not found" in mock_send.call_args.args[0]


# ---------------------------------------------------------------------------
# _register_commands includes /logs
# ---------------------------------------------------------------------------


def test_register_commands_includes_logs():
    bot = _bot()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    commands = mock_post.call_args.kwargs["json"]["commands"]
    cmd_names = [c["command"] for c in commands]
    assert "logs" in cmd_names


# ---------------------------------------------------------------------------
# _send_message parse_mode param
# ---------------------------------------------------------------------------


def test_send_message_default_parse_mode_is_markdownv2():
    bot = _bot()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._send_message("hello")
    payload = mock_post.call_args.kwargs["json"]
    assert payload.get("parse_mode") == "MarkdownV2"


def test_send_message_no_parse_mode_omits_field():
    bot = _bot()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
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
# _cmd_status — auth state decoration
# ---------------------------------------------------------------------------


def test_cmd_status_shows_checkmark_for_authenticated_instance():
    """✅ icon when the session check passes for an instance."""
    bot = _bot()
    with (
        patch("app.bot.docker.from_env", return_value=MagicMock()),
        patch("app.bot._docker_container_status", return_value="running"),
        patch("app.bot._docker_check_session", return_value=True),
        patch(
            "app.bot._docker_last_sync_summary",
            return_value="✅ success at 2026/08/11 10:00 UTC",
        ),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "✅" in msg
    assert "running" in msg
    assert "last: ✅ success at 2026/08/11 10:00 UTC" in msg


def test_cmd_status_shows_warning_for_unauthenticated_instance():
    """⚠️ icon when the session check fails for an instance."""
    bot = _bot()
    with (
        patch("app.bot.docker.from_env", return_value=MagicMock()),
        patch("app.bot._docker_container_status", return_value="running"),
        patch("app.bot._docker_check_session", return_value=False),
        patch("app.bot._docker_last_sync_summary", return_value=None),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "⚠️" in msg
    assert "unavailable" in msg


def test_cmd_status_shows_question_mark_for_unavailable_instance():
    """❓ icon when the container is unreachable."""
    bot = _bot()
    with (
        patch("app.bot.docker.from_env", return_value=MagicMock()),
        patch("app.bot._docker_container_status", return_value=None),
        patch("app.bot._docker_check_session", return_value=None) as mock_check_session,
        patch("app.bot._docker_last_sync_summary", return_value=None) as mock_last_sync,
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "❓" in msg
    assert "unknown" in msg
    assert "last: unavailable" in msg
    mock_check_session.assert_not_called()
    mock_last_sync.assert_not_called()


def test_cmd_status_checks_each_instance():
    """_docker_check_session must be called once per configured instance."""
    bot = _bot()
    with (
        patch("app.bot.docker.from_env", return_value=MagicMock()),
        patch("app.bot._docker_container_status", return_value="running"),
        patch("app.bot._docker_check_session", return_value=True) as mock_check,
        patch(
            "app.bot._docker_last_sync_summary",
            return_value="✅ success at 2026/08/11 10:00 UTC",
        ),
        patch.object(bot, "_send_message"),
    ):
        bot._cmd_status([])
    assert mock_check.call_count == len(bot._cfg.instances)


# ---------------------------------------------------------------------------
# TelegramBot.__init__ — telegram_verify_ssl=False disables urllib3 warnings
# ---------------------------------------------------------------------------


def test_init_disables_urllib3_warnings_when_ssl_verify_false():
    """When telegram_verify_ssl=False, urllib3 InsecureRequestWarning is suppressed."""
    import urllib3

    with patch.object(urllib3, "disable_warnings") as mock_dw:
        TelegramBot(
            BotConfig(
                bot_token="tok",
                chat_id="42",
                instances={},
                telegram_verify_ssl=False,
            )
        )
    mock_dw.assert_called_once_with(urllib3.exceptions.InsecureRequestWarning)


# ---------------------------------------------------------------------------
# TelegramBot.run — polling loop
# ---------------------------------------------------------------------------


def test_run_stops_on_keyboard_interrupt():
    """KeyboardInterrupt inside the loop causes run() to exit cleanly."""
    bot = _bot()
    with (
        patch.object(bot, "_register_commands"),
        patch.object(bot, "_send_message"),
        patch.object(bot, "_poll_once", side_effect=KeyboardInterrupt),
    ):
        bot.run()  # must not raise


def test_run_recovers_from_polling_exception_then_stops():
    """A generic exception is caught; a subsequent KeyboardInterrupt stops the loop."""
    bot = _bot()
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


def test_poll_once_dispatches_update():
    """_poll_once fetches updates and routes each one through _handle_update."""
    bot = _bot()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "result": [{"update_id": 10, "message": {"chat": {"id": 42}, "text": "/help"}}]
    }
    with (
        patch("app.bot.requests.get", return_value=mock_resp),
        patch.object(bot, "_handle_update") as mock_handle,
    ):
        bot._poll_once()
    mock_handle.assert_called_once()
    assert bot._offset == 11


def test_poll_once_advances_offset_for_multiple_updates():
    """Offset is set to last update_id + 1."""
    bot = _bot()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "result": [
            {"update_id": 5, "message": {"chat": {"id": 42}, "text": "/help"}},
            {"update_id": 6, "message": {"chat": {"id": 42}, "text": "/status"}},
        ]
    }
    with (
        patch("app.bot.requests.get", return_value=mock_resp),
        patch.object(bot, "_handle_update"),
    ):
        bot._poll_once()
    assert bot._offset == 7


def test_poll_once_continues_on_handle_update_exception():
    """Exception inside _handle_update is caught; remaining updates are still processed."""
    bot = _bot()
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
        patch("app.bot.requests.get", return_value=mock_resp),
        patch.object(bot, "_handle_update", side_effect=flaky_handle),
    ):
        bot._poll_once()

    assert handle_calls == [1, 2]
    assert bot._offset == 3


# ---------------------------------------------------------------------------
# TelegramBot._cmd_status — no instances configured
# ---------------------------------------------------------------------------


def test_cmd_status_no_instances_sends_warning():
    """When no instances are configured, _cmd_status sends a clear warning."""
    bot = _bot(instances={})
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_status([])
    mock_send.assert_called_once()
    assert "no instances" in mock_send.call_args.args[0].lower()


# ---------------------------------------------------------------------------
# TelegramBot._cmd_status — backup not configured
# ---------------------------------------------------------------------------


def test_cmd_status_mentions_backup_not_configured_when_absent():
    """When backup_container is None, the status message must say it is not configured."""
    bot = _bot(backup_container=None)
    with (
        patch("app.bot.docker.from_env", return_value=MagicMock()),
        patch("app.bot._docker_container_status", return_value=None),
        patch("app.bot._docker_check_session", return_value=None),
        patch("app.bot._docker_last_sync_summary", return_value=None),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "not configured" in msg


# ---------------------------------------------------------------------------
# TelegramBot._cmd_sync
# ---------------------------------------------------------------------------


def test_cmd_sync_sends_instance_picker_keyboard():
    """_cmd_sync must send a prompt with an inline keyboard of instances."""
    bot = _bot()
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


def test_cmd_resync_no_args_sends_instance_picker():
    """/resync with no args must show an instance picker."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync([])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any(d.startswith("resync_pick_date:") for d in cb_data)


def test_cmd_resync_with_date_sends_instance_picker_for_date():
    """/resync 2026-07-15 must show an instance picker with the date encoded."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync(["2026-07-15"])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard")
    assert keyboard is not None
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any("2026-07-15" in d for d in cb_data)


def test_cmd_resync_invalid_date_sends_error():
    """/resync with a non-date arg must send an error message."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync(["not-a-date"])
    mock_send.assert_called_once()
    msg = mock_send.call_args.args[0]
    assert "invalid" in msg.lower() or "YYYY" in msg


def test_cmd_resync_datetime_string_sends_error():
    """/resync with a full datetime string must send an error — only YYYY-MM-DD is valid."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_resync(["2026-07-15T12:00:00"])
    mock_send.assert_called_once()
    msg = mock_send.call_args.args[0]
    assert "invalid" in msg.lower() or "YYYY" in msg


# ---------------------------------------------------------------------------
# TelegramBot._handle_callback_query — resync callbacks
# ---------------------------------------------------------------------------


def test_callback_resync_pick_date_sends_date_keyboard():
    """resync_pick_date:<instance> callback must show a date-picker keyboard."""
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_resync_date_buttons", return_value=[[]]) as mock_dates,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "resync_pick_date:david",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_dates.assert_called_once_with("david")
    mock_send.assert_called_once()


def test_callback_resync_dispatches_launch_resync():
    """resync:<date>:<instance> callback must call _launch_resync."""
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_resync") as mock_launch,
    ):
        bot._handle_callback_query(
            {
                "id": "cq1",
                "data": "resync:2026-07-15:david",
                "message": {"chat": {"id": 42}},
            }
        )
    mock_launch.assert_called_once()
    inst, date_str = mock_launch.call_args.args
    assert date_str == "2026-07-15"
    assert inst.name == "David"


def test_callback_resync_unknown_instance_replies():
    """resync:<date>:<unknown> callback must reply with error."""
    bot = _bot()
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


def test_callback_resync_pick_date_unknown_instance_replies():
    """resync_pick_date:<unknown> callback must reply with error."""
    bot = _bot()
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


def test_callback_resync_malformed_too_few_parts_does_not_raise():
    """resync callback with fewer than 3 parts must log warning and not raise."""
    bot = _bot()
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
    # No message sent — silently swallowed with a log warning
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------


def test_launch_resync_sends_ack_and_starts_thread():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._launch_resync(inst, "2026-07-15")
    mock_send.assert_called_once()
    mock_exec.assert_called_once()


def test_launch_resync_passes_correct_args():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message"),
        patch.object(bot, "_exec_in_thread") as mock_exec,
    ):
        bot._launch_resync(inst, "2026-07-15")
    _, app_args = mock_exec.call_args.args
    assert app_args == ["resync", "2026-07-15"]


# ---------------------------------------------------------------------------
# _register_commands — resync is registered
# ---------------------------------------------------------------------------


def test_register_commands_includes_resync():
    bot = _bot()
    with patch("app.bot.requests.post") as mock_post:
        mock_post.return_value = MagicMock(raise_for_status=MagicMock())
        bot._register_commands()
    payload = mock_post.call_args.kwargs["json"]
    commands = [c["command"] for c in payload["commands"]]
    assert "resync" in commands


# ---------------------------------------------------------------------------
# _cmd_help — resync mentioned
# ---------------------------------------------------------------------------


def test_cmd_help_mentions_resync():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_help([])
    msg = mock_send.call_args.args[0]
    assert "resync" in msg.lower()


# ---------------------------------------------------------------------------
# Wiring — TelegramBot delegates to collaborators
# ---------------------------------------------------------------------------


def test_instance_buttons_delegates_to_bot_keyboards():
    """TelegramBot._instance_buttons must call the standalone function with instance names."""
    bot = _bot()
    with patch("app.bot._instance_buttons_fn", return_value=[[]]) as mock_fn:
        bot._instance_buttons("sync")
    mock_fn.assert_called_once_with("sync", ["David", "Eli"])


def test_month_buttons_delegates_to_bot_keyboards():
    """TelegramBot._month_buttons must delegate to bot_keyboards.month_buttons."""
    bot = _bot()
    with patch("app.bot._month_buttons_fn", return_value=[[]]) as mock_fn:
        bot._month_buttons()
    mock_fn.assert_called_once_with()


def test_exec_in_thread_target_is_docker_exec_silent():
    """TelegramBot._exec_in_thread must use _docker_exec_silent as the thread target."""
    bot = _bot()
    with patch("app.bot.threading.Thread") as mock_thread:
        mock_thread.return_value.start = MagicMock()
        bot._exec_in_thread("c", ["sync"])
    assert mock_thread.call_args.kwargs["target"] is _docker_exec_silent
