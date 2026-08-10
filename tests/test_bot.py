from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from app.bot import (
    BotConfig,
    InstanceConfig,
    TelegramBot,
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
            "eli":   InstanceConfig(name="Eli",   container_name="proj-sync-eli-1"),
        }
    return BotConfig(bot_token="tok", chat_id="42", instances=instances, backup_container=backup_container)


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
    assert "backup_monthly" in cmd_names
    assert "backup_yearly" in cmd_names
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
    assert "backup_monthly" not in cmd_names
    assert "backup_yearly" not in cmd_names
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


def test_handle_message_ignores_non_command():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._handle_message({"chat": {"id": 42}, "text": "hello"})
    mock_send.assert_not_called()


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
    assert "backup_monthly" not in msg and "backup\\_monthly" not in msg


# ---------------------------------------------------------------------------
# TelegramBot._cmd_status
# ---------------------------------------------------------------------------

def test_cmd_status_no_instances_sends_warning():
    bot = _bot(instances={})
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_status([])
    mock_send.assert_called_once()
    assert "No instances" in mock_send.call_args.args[0]


def test_cmd_status_shows_each_instance():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "David" in msg
    assert "Eli" in msg


def test_cmd_status_shows_backup_container_when_configured():
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    # Container name is MarkdownV2-escaped (hyphens become \-)
    assert "proj" in msg and "backup" in msg and "1" in msg


def test_cmd_status_shows_backup_not_configured():
    bot = _bot(backup_container=None)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "not configured" in msg


# ---------------------------------------------------------------------------
# TelegramBot._cmd_sync
# ---------------------------------------------------------------------------

def test_cmd_sync_sends_keyboard_with_instances():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_sync([])
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert "keyboard" in kwargs
    all_buttons = [btn for row in kwargs["keyboard"] for btn in row]
    labels = [b["text"] for b in all_buttons]
    assert "David" in labels
    assert "Eli" in labels


# ---------------------------------------------------------------------------
# TelegramBot._cmd_backup_monthly / _cmd_backup_yearly
# ---------------------------------------------------------------------------

def test_cmd_backup_monthly_no_backup_container_sends_error():
    bot = _bot(backup_container=None)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup_monthly([])
    mock_send.assert_called_once()
    assert "not configured" in mock_send.call_args.args[0]


def test_cmd_backup_monthly_without_args_shows_month_keyboard():
    """Without args, /backup_monthly should show an inline month-selection keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup_monthly([])
    mock_send.assert_called_once()
    keyboard = mock_send.call_args.kwargs.get("keyboard") or mock_send.call_args[1].get("keyboard")
    assert keyboard is not None, "Expected a keyboard when no month arg is given"
    all_buttons = [btn for row in keyboard for btn in row]
    assert len(all_buttons) >= 1


def test_cmd_backup_monthly_keyboard_callback_data_encodes_month():
    """Keyboard buttons encode 'backup_monthly:YYYY-MM' as callback_data."""
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup_monthly([])
    keyboard = mock_send.call_args.kwargs.get("keyboard") or mock_send.call_args[1].get("keyboard")
    all_buttons = [btn for row in keyboard for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert all(d.startswith("backup_monthly:") for d in cb_data)
    # All values should be YYYY-MM format
    periods = [d.split(":")[1] for d in cb_data]
    for period in periods:
        assert len(period) == 7, f"Expected YYYY-MM format, got: {period}"
        year, month = period.split("-")
        assert year.isdigit() and month.isdigit()


def test_cmd_backup_monthly_with_arg_executes_directly():
    """With an explicit month arg, execute without showing a keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._cmd_backup_monthly(["2026-07"])
    _, kwargs = mock_send.call_args
    assert kwargs.get("keyboard") is None
    mock_thread.assert_called_once()


def test_cmd_backup_monthly_with_param_includes_period_in_ack():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._cmd_backup_monthly(["2026-07"])
    # MarkdownV2 escapes hyphens, so check year and month separately
    assert "2026" in mock_send.call_args.args[0]
    assert "07" in mock_send.call_args.args[0]


def test_cmd_backup_monthly_executes_on_backup_container():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message"),
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._cmd_backup_monthly(["2026-07"])
    _, kwargs = mock_thread.call_args
    assert kwargs["args"] == ("proj-backup-1", ["backup", "monthly", "2026-07"])


def test_callback_query_backup_monthly_dispatches_launch():
    """backup_monthly:YYYY-MM callback should trigger _launch_backup_monthly."""
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_backup_monthly") as mock_launch,
    ):
        bot._handle_callback_query({
            "id": "cq1",
            "data": "backup_monthly:2026-07",
            "message": {"chat": {"id": 42}},
        })
    mock_launch.assert_called_once_with("2026-07")


def test_launch_backup_monthly_sends_ack_and_starts_thread():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup_monthly("2026-07")
    mock_send.assert_called_once()
    assert "2026" in mock_send.call_args.args[0]
    mock_thread.assert_called_once()


def test_launch_backup_monthly_executes_correct_args():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message"),
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup_monthly("2026-07")
    _, kwargs = mock_thread.call_args
    assert kwargs["args"] == ("proj-backup-1", ["backup", "monthly", "2026-07"])


def test_cmd_backup_yearly_no_backup_container_sends_error():
    bot = _bot(backup_container=None)
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup_yearly([])
    assert "not configured" in mock_send.call_args.args[0]


def test_cmd_backup_yearly_without_args_shows_year_keyboard():
    """Without args, /backup_yearly should show an inline year-selection keyboard."""
    import datetime
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup_yearly([])
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    keyboard = kwargs.get("keyboard") or mock_send.call_args[1].get("keyboard")
    assert keyboard is not None, "Expected a keyboard when no year arg is given"
    all_buttons = [btn for row in keyboard for btn in row]
    prev_year = str(datetime.datetime.now(tz=datetime.timezone.utc).year - 1)
    labels = [b["text"] for b in all_buttons]
    assert any(prev_year in label for label in labels), f"Expected {prev_year} in buttons: {labels}"


def test_cmd_backup_yearly_keyboard_callback_data_encodes_year():
    """Keyboard buttons encode 'backup_yearly:<year>' as callback_data."""
    import datetime
    bot = _bot(backup_container="proj-backup-1")
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_backup_yearly([])
    keyboard = mock_send.call_args[1].get("keyboard") or mock_send.call_args.kwargs.get("keyboard")
    all_buttons = [btn for row in keyboard for btn in row]
    prev_year = str(datetime.datetime.now(tz=datetime.timezone.utc).year - 1)
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any(f"backup_yearly:{prev_year}" in d for d in cb_data)


def test_cmd_backup_yearly_with_arg_executes_directly():
    """With an explicit year arg, execute without showing a keyboard."""
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._cmd_backup_yearly(["2025"])
    # No keyboard should be shown
    _, kwargs = mock_send.call_args
    assert kwargs.get("keyboard") is None
    mock_thread.assert_called_once()


def test_cmd_backup_yearly_executes_on_backup_container():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message"),
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._cmd_backup_yearly(["2025"])
    _, kwargs = mock_thread.call_args
    assert kwargs["args"] == ("proj-backup-1", ["backup", "yearly", "2025"])


def test_callback_query_backup_yearly_dispatches_launch():
    """backup_yearly:<year> callback should trigger _launch_backup_yearly."""
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_backup_yearly") as mock_launch,
    ):
        bot._handle_callback_query({
            "id": "cq1",
            "data": "backup_yearly:2025",
            "message": {"chat": {"id": 42}},
        })
    mock_launch.assert_called_once_with("2025")


def test_launch_backup_yearly_sends_ack_and_starts_thread():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup_yearly("2025")
    mock_send.assert_called_once()
    assert "2025" in mock_send.call_args.args[0]
    mock_thread.assert_called_once()


def test_launch_backup_yearly_executes_correct_args():
    bot = _bot(backup_container="proj-backup-1")
    with (
        patch.object(bot, "_send_message"),
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup_yearly("2024")
    _, kwargs = mock_thread.call_args
    assert kwargs["args"] == ("proj-backup-1", ["backup", "yearly", "2024"])


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
    instances = {str(i): InstanceConfig(name=str(i), container_name=f"proj-sync-{i}-1") for i in range(5)}
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
        bot._handle_callback_query({"id": "cq1", "data": "noop", "message": {"chat": {"id": 42}}})
    mock_ack.assert_called_once_with("cq1")
    mock_sync.assert_not_called()


def test_callback_query_unauthorized_chat_ignored():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "sync:david", "message": {"chat": {"id": 9999}}})
    mock_sync.assert_not_called()


def test_callback_query_sync_dispatches_launch_sync():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_sync") as mock_sync,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "sync:david", "message": {"chat": {"id": 42}}})
    mock_sync.assert_called_once()
    assert mock_sync.call_args.args[0].name == "David"


def test_callback_query_unknown_instance_replies():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "sync:nobody", "message": {"chat": {"id": 42}}})
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0]


def test_callback_query_malformed_data_does_not_raise():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message"),
    ):
        bot._handle_callback_query({"id": "cq1", "data": "malformed", "message": {"chat": {"id": 42}}})
    # must not raise


def test_callback_query_unknown_cmd_logs_warning_and_does_not_raise():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "badcmd:david", "message": {"chat": {"id": 42}}})
    mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._launch_sync
# ---------------------------------------------------------------------------

def test_launch_sync_sends_ack_and_starts_thread():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_sync(inst)
    mock_send.assert_called_once()
    assert "David" in mock_send.call_args.args[0]
    mock_thread.assert_called_once()


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
        bot._handle_callback_query({"id": "cq1", "data": "login:david", "message": {"chat": {"id": 42}}})
    mock_login.assert_called_once()
    assert mock_login.call_args.args[0].name == "David"


def test_launch_login_sends_ack_and_starts_thread():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_login(inst)
    mock_send.assert_called_once()
    assert "David" in mock_send.call_args.args[0]
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["args"] == (inst.container_name, ["login"])


def test_launch_login_reports_success_via_on_success():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_login(inst)
        on_success = mock_thread.call_args.kwargs["kwargs"]["on_success"]
        mock_send.reset_mock()
        on_success()
    mock_send.assert_called_once()
    assert "David" in mock_send.call_args.args[0]


# ---------------------------------------------------------------------------
# TelegramBot._cmd_code
# ---------------------------------------------------------------------------

def test_cmd_code_executes_submit_code_for_instance():
    bot = _bot()
    with (
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot._docker_exec_silent"),
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._cmd_code(["david", "123456"])
    mock_thread.assert_called_once()
    assert mock_thread.call_args.kwargs["args"] == ("proj-sync-david-1", ["submit-code", "123456"])
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
    with patch("app.bot.requests.post", side_effect=requests.RequestException("network error")):
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
        bot._handle_update({"update_id": 1, "message": {"chat": {"id": 42}, "text": "/help"}})
    mock_msg.assert_called_once()


def test_handle_update_routes_callback_query():
    bot = _bot()
    with patch.object(bot, "_handle_callback_query") as mock_cb:
        bot._handle_update({"update_id": 1, "callback_query": {"id": "cq1", "data": "noop", "message": {"chat": {"id": 42}}}})
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
