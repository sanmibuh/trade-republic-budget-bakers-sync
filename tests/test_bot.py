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
    _docker_check_session,
    _docker_client_ctx,
    _docker_container_status,
    _docker_exec_silent,
    _docker_last_sync_summary,
    _docker_logs_today,
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
# _docker_exec_silent
# ---------------------------------------------------------------------------


def _make_docker_client(exit_code: int = 0, output: bytes = b"") -> MagicMock:
    """Return a mock docker client where exec_run returns (exit_code, output)."""
    container = MagicMock()
    container.exec_run.return_value = (exit_code, output)
    client = MagicMock()
    client.containers.get.return_value = container
    return client


def test_docker_exec_silent_success():
    client = _make_docker_client(0)
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"])
    client.containers.get.assert_called_once_with("my-container")
    args = client.containers.get.return_value.exec_run.call_args.args[0]
    assert "python" in args
    assert "-m" in args
    assert "app" in args
    assert "sync" in args


def test_docker_exec_silent_failure_does_not_raise():
    client = _make_docker_client(1, b"error output")
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"])  # must not raise


def test_docker_exec_silent_failure_calls_on_error():
    on_error = MagicMock()
    client = _make_docker_client(1, b"something broke")
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"], on_error=on_error)
    on_error.assert_called_once()
    assert "my-container" in on_error.call_args.args[0]


def test_docker_exec_silent_exception_calls_on_error():
    on_error = MagicMock()
    client = MagicMock()
    client.containers.get.side_effect = Exception("unexpected")
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"], on_error=on_error)
    on_error.assert_called_once()


def test_docker_exec_silent_success_does_not_call_on_error():
    on_error = MagicMock()
    client = _make_docker_client(0)
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"], on_error=on_error)
    on_error.assert_not_called()


def test_docker_exec_silent_success_calls_on_success():
    on_success = MagicMock()
    client = _make_docker_client(0)
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["login"], on_success=on_success)
    on_success.assert_called_once()


def test_docker_exec_silent_failure_does_not_call_on_success():
    on_success = MagicMock()
    client = _make_docker_client(1, b"boom")
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["login"], on_success=on_success)
    on_success.assert_not_called()


def test_docker_exec_silent_container_not_found_does_not_raise():
    import docker.errors

    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("not found")
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"])  # must not raise


def test_docker_exec_silent_exception_does_not_raise():
    client = MagicMock()
    client.containers.get.side_effect = Exception("unexpected")
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"])  # must not raise


def test_docker_exec_silent_passes_app_command_args():
    client = _make_docker_client(0)
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["backup", "monthly", "2026-07"])
    args = client.containers.get.return_value.exec_run.call_args.args[0]
    assert "backup" in args
    assert "monthly" in args
    assert "2026-07" in args


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
# _BACKUP_ICONS — consistent icon mapping
# ---------------------------------------------------------------------------


def test_backup_icons_has_monthly_and_yearly():
    assert "monthly" in _BACKUP_ICONS
    assert "yearly" in _BACKUP_ICONS


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
# TelegramBot._instance_buttons
# ---------------------------------------------------------------------------


def test_instance_buttons_returns_one_button_per_instance():
    bot = _bot()
    rows = bot._instance_buttons("sync")
    all_buttons = [btn for row in rows for btn in row]
    assert len(all_buttons) == 2
    labels = [b["text"] for b in all_buttons]
    assert "David" in labels
    assert "Eli" in labels


def test_instance_buttons_callback_data_encodes_cmd_and_instance():
    bot = _bot()
    rows = bot._instance_buttons("sync")
    all_buttons = [btn for row in rows for btn in row]
    data = {b["text"]: b["callback_data"] for b in all_buttons}
    assert data["David"] == "sync:david"
    assert data["Eli"] == "sync:eli"


def test_instance_buttons_rows_split_at_three():
    """More than 3 instances → buttons split into rows of max 3."""
    instances = {
        str(i): InstanceConfig(name=str(i), container_name=f"proj-sync-{i}-1")
        for i in range(5)
    }
    bot = _bot(instances)
    rows = bot._instance_buttons("sync")
    assert all(len(row) <= 3 for row in rows)
    assert sum(len(row) for row in rows) == 5


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


def test_handle_message_digit_string_not_deleted_when_no_pending_login():
    """Digit messages are not deleted and not submitted when no login is pending."""
    bot = _bot()
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
        patch.object(bot, "_delete_message") as mock_delete,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456", "message_id": 77})
    mock_exec.assert_not_called()
    mock_send.assert_not_called()
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


def test_handle_message_digit_string_ignored_when_no_pending_login():
    """Plain digit messages are silently ignored when no login is pending."""
    bot = _bot()
    with (
        patch.object(bot, "_exec_in_thread") as mock_exec,
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_message({"chat": {"id": 42}, "text": "123456"})
    mock_exec.assert_not_called()
    mock_send.assert_not_called()


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


def test_handle_message_unknown_plain_text_replies_commands_only():
    """Non-command, non-digit text receives a 'commands only' reply."""
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "hello there"})
    mock_send.assert_called_once()
    assert "command" in mock_send.call_args.args[0].lower()


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
# _docker_logs_today
# ---------------------------------------------------------------------------


def test_docker_logs_today_returns_decoded_output():
    import datetime

    from app.bot import _docker_logs_today

    container = MagicMock()
    container.logs.return_value = b"2026-08-11 10:00:00 INFO  sync: done\n"
    client = MagicMock()
    client.containers.get.return_value = container

    since = datetime.datetime(2026, 8, 11, 0, 0, 0, tzinfo=datetime.UTC)
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_logs_today("my-container", since=since)

    client.containers.get.assert_called_once_with("my-container")
    container.logs.assert_called_once_with(since=since, timestamps=False)
    assert "done" in result


def test_docker_logs_today_decodes_invalid_bytes():
    import datetime

    from app.bot import _docker_logs_today

    container = MagicMock()
    container.logs.return_value = b"ok\xff\xfe"
    client = MagicMock()
    client.containers.get.return_value = container

    since = datetime.datetime(2026, 8, 11, 0, 0, 0, tzinfo=datetime.UTC)
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_logs_today("my-container", since=since)

    assert "ok" in result  # no UnicodeDecodeError raised


def test_docker_logs_today_uses_explicit_client():
    import datetime

    container = MagicMock()
    container.logs.return_value = b"explicit client log\n"
    client = MagicMock()
    client.containers.get.return_value = container

    since = datetime.datetime(2026, 8, 11, 0, 0, 0, tzinfo=datetime.UTC)
    result = _docker_logs_today("my-container", since=since, client=client)

    client.containers.get.assert_called_once_with("my-container")
    container.logs.assert_called_once_with(since=since, timestamps=False)
    assert "explicit client log" in result


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
# _docker_check_session
# ---------------------------------------------------------------------------


def test_docker_check_session_returns_true_when_exit_zero():
    """Exit code 0 from check-session → session is valid."""
    client = _make_docker_client(exit_code=0)
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_check_session("my-container")
    assert result is True


def test_docker_check_session_returns_false_when_exit_one():
    """Exit code 1 from check-session → session needs renewal."""
    client = _make_docker_client(exit_code=1)
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_check_session("my-container")
    assert result is False


def test_docker_check_session_returns_none_on_unexpected_exit_code():
    """Exit code other than 0/1 (e.g. crash) → unknown state, not False."""
    client = _make_docker_client(exit_code=2)
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_check_session("my-container")
    assert result is None


def test_docker_check_session_returns_none_on_exception():
    """If the container is unreachable, return None (unknown state)."""
    client = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_check_session("my-container")
    assert result is None


def test_docker_check_session_invokes_check_session_command():
    """The exec must call `python -m app check-session`."""
    client = _make_docker_client(exit_code=0)
    with patch("app.bot.docker.from_env", return_value=client):
        _docker_check_session("my-container")
    cmd = client.containers.get.return_value.exec_run.call_args.args[0]
    assert cmd == ["python", "-m", "app", "check-session"]


# ---------------------------------------------------------------------------
# _docker_client_ctx
# ---------------------------------------------------------------------------


def test_docker_client_ctx_yields_client_and_closes_it():
    client = MagicMock()
    with (
        patch("app.bot.docker.from_env", return_value=client),
        _docker_client_ctx() as c,
    ):
        assert c is client
    client.close.assert_called_once()


def test_docker_client_ctx_yields_none_on_init_failure():
    with (
        patch("app.bot.docker.from_env", side_effect=Exception("daemon unreachable")),
        _docker_client_ctx() as c,
    ):
        assert c is None


# ---------------------------------------------------------------------------
# _docker_container_status / _docker_last_sync_summary
# ---------------------------------------------------------------------------


def test_docker_container_status_returns_running_state():
    client = MagicMock()
    container = MagicMock()
    container.status = "running"
    client.containers.get.return_value = container
    with patch("app.bot.docker.from_env", return_value=client):
        assert _docker_container_status("my-container") == "running"


def test_docker_container_status_returns_none_on_exception():
    client = MagicMock()
    client.containers.get.side_effect = Exception("boom")
    with patch("app.bot.docker.from_env", return_value=client):
        assert _docker_container_status("my-container") is None


def test_docker_last_sync_summary_parses_success_from_log():
    client = _make_docker_client(
        output=b'{"status":"success","timestamp":"2026-08-11 10:00:00","synced":3,"failed":0,"excluded":1,"synced_at":null}'
    )
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_last_sync_summary("my-container")
    assert (
        result == "✅ success at 2026/08/11 10:00 UTC · saved 3 · failed 0 · excluded 1"
    )


def test_docker_last_sync_summary_uses_explicit_client():
    client = _make_docker_client(
        output=b'{"status":"success","timestamp":"2026-08-11 10:00:00","synced":1,"failed":0,"excluded":0,"synced_at":null}'
    )
    assert _docker_last_sync_summary("my-container", client=client) == (
        "✅ success at 2026/08/11 10:00 UTC · saved 1 · failed 0 · excluded 0"
    )


def test_docker_last_sync_summary_falls_back_to_last_synced_at():
    client = _make_docker_client(
        output=b'{"status":null,"timestamp":null,"synced":null,"failed":null,"excluded":null,"synced_at":"2026-08-11T10:00:00+00:00"}'
    )
    with patch("app.bot.docker.from_env", return_value=client):
        result = _docker_last_sync_summary("my-container")
    assert result == "✅ last saved event at 2026/08/11 10:00 UTC"


def test_docker_last_sync_summary_returns_none_on_invalid_payload():
    client = _make_docker_client(output=b"not json")
    with patch("app.bot.docker.from_env", return_value=client):
        assert _docker_last_sync_summary("my-container") is None


def test_docker_last_sync_summary_returns_none_on_nonzero_exit_code():
    client = _make_docker_client(exit_code=1, output=b"boom")
    with patch("app.bot.docker.from_env", return_value=client):
        assert _docker_last_sync_summary("my-container") is None


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
