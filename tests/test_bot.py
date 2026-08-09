from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest
import requests

from app.bot import (
    BotConfig,
    InstanceConfig,
    TelegramBot,
    _container_has_backup_schedule,
    _docker_exec_silent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(instances: dict[str, InstanceConfig] | None = None) -> BotConfig:
    if instances is None:
        instances = {
            "david": InstanceConfig(name="David", container_name="proj-david-1"),
            "eli":   InstanceConfig(name="Eli",   container_name="proj-eli-1"),
        }
    return BotConfig(bot_token="tok", chat_id="42", instances=instances)


def _bot(instances: dict[str, InstanceConfig] | None = None) -> TelegramBot:
    return TelegramBot(_cfg(instances))


def _message(text: str, chat_id: str = "42") -> dict:
    return {"message": {"chat": {"id": int(chat_id)}, "text": text}}


def _callback(data: str, chat_id: str = "42", cq_id: str = "cq1") -> dict:
    return {"callback_query": {"id": cq_id, "data": data, "message": {"chat": {"id": int(chat_id)}}}}


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
    assert cfg.instances["david"].container_name == "myproject-david-1"
    assert cfg.instances["eli"].container_name == "myproject-eli-1"


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


# ---------------------------------------------------------------------------
# _container_has_backup_schedule
# ---------------------------------------------------------------------------

def _inspect_result(stdout: str, returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = ""
    return r


def test_container_has_backup_schedule_returns_true_when_set():
    with patch("app.bot.subprocess.run", return_value=_inspect_result("FOO=bar\nBACKUP_SCHEDULE=0 3 * * *\n")):
        assert _container_has_backup_schedule("my-container") is True


def test_container_has_backup_schedule_returns_false_when_empty_value():
    with patch("app.bot.subprocess.run", return_value=_inspect_result("BACKUP_SCHEDULE=\n")):
        assert _container_has_backup_schedule("my-container") is False


def test_container_has_backup_schedule_returns_false_when_key_absent():
    with patch("app.bot.subprocess.run", return_value=_inspect_result("FOO=bar\nBAR=baz\n")):
        assert _container_has_backup_schedule("my-container") is False


def test_container_has_backup_schedule_returns_false_on_inspect_failure():
    with patch("app.bot.subprocess.run", return_value=_inspect_result("", returncode=1)):
        assert _container_has_backup_schedule("my-container") is False


def test_container_has_backup_schedule_returns_false_on_exception():
    with patch("app.bot.subprocess.run", side_effect=OSError("docker not found")):
        assert _container_has_backup_schedule("my-container") is False


# ---------------------------------------------------------------------------
# _docker_exec_silent
# ---------------------------------------------------------------------------

def _exec_result(returncode: int) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = "some output"
    r.stderr = ""
    return r


def test_docker_exec_silent_success():
    with patch("app.bot.subprocess.run", return_value=_exec_result(0)) as mock_run:
        _docker_exec_silent("my-container", ["sync"])
    cmd = mock_run.call_args.args[0]
    assert "docker" in cmd
    assert "exec" in cmd
    assert "my-container" in cmd
    assert "sync" in cmd


def test_docker_exec_silent_failure_does_not_raise():
    with patch("app.bot.subprocess.run", return_value=_exec_result(1)):
        _docker_exec_silent("my-container", ["sync"])  # must not raise


def test_docker_exec_silent_timeout_does_not_raise():
    with patch("app.bot.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=600)):
        _docker_exec_silent("my-container", ["sync"])  # must not raise


def test_docker_exec_silent_exception_does_not_raise():
    with patch("app.bot.subprocess.run", side_effect=OSError("docker not found")):
        _docker_exec_silent("my-container", ["sync"])  # must not raise


def test_docker_exec_silent_passes_app_command_args():
    with patch("app.bot.subprocess.run", return_value=_exec_result(0)) as mock_run:
        _docker_exec_silent("my-container", ["backup", "monthly", "2026-07"])
    cmd = mock_run.call_args.args[0]
    assert "backup" in cmd
    assert "monthly" in cmd
    assert "2026-07" in cmd


# ---------------------------------------------------------------------------
# TelegramBot._register_commands
# ---------------------------------------------------------------------------

def test_register_commands_calls_set_my_commands():
    bot = _bot()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    with patch("app.bot.requests.post", return_value=mock_resp) as mock_post:
        bot._register_commands()
    url = mock_post.call_args.args[0]
    assert "setMyCommands" in url
    commands = mock_post.call_args.kwargs["json"]["commands"]
    cmd_names = [c["command"] for c in commands]
    assert "sync" in cmd_names
    assert "backup_monthly" in cmd_names
    assert "backup_yearly" in cmd_names
    assert "status" in cmd_names
    assert "help" in cmd_names


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
    assert "Unknown" in mock_send.call_args.args[0] or "nknown" in mock_send.call_args.args[0]


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


# ---------------------------------------------------------------------------
# TelegramBot._cmd_help / _cmd_status
# ---------------------------------------------------------------------------

def test_cmd_help_sends_message():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_help([])
    mock_send.assert_called_once()
    msg = mock_send.call_args.args[0]
    assert "sync" in msg.lower()
    assert "backup" in msg.lower()


def test_cmd_status_no_instances_sends_warning():
    bot = _bot(instances={})
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_status([])
    mock_send.assert_called_once()
    assert "No instances" in mock_send.call_args.args[0]


def test_cmd_status_shows_each_instance():
    bot = _bot()
    with (
        patch("app.bot._container_has_backup_schedule", return_value=True),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "David" in msg
    assert "Eli" in msg


def test_cmd_status_shows_backup_unavailable():
    bot = _bot()
    with (
        patch("app.bot._container_has_backup_schedule", return_value=False),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_status([])
    msg = mock_send.call_args.args[0]
    assert "❌" in msg


# ---------------------------------------------------------------------------
# TelegramBot._cmd_sync / _cmd_backup_* — keyboard shown
# ---------------------------------------------------------------------------

def test_cmd_sync_sends_keyboard():
    bot = _bot()
    with patch.object(bot, "_send_message") as mock_send:
        bot._cmd_sync([])
    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert "keyboard" in kwargs
    # one row of buttons, one per instance
    all_buttons = [btn for row in kwargs["keyboard"] for btn in row]
    labels = [b["text"] for b in all_buttons]
    assert "David" in labels
    assert "Eli" in labels


def test_cmd_backup_monthly_no_param_shows_keyboard():
    bot = _bot()
    with (
        patch("app.bot._container_has_backup_schedule", return_value=True),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_backup_monthly([])
    _, kwargs = mock_send.call_args
    assert "keyboard" in kwargs
    assert "previous month" in mock_send.call_args.args[0]


def test_cmd_backup_monthly_with_param_encodes_in_keyboard():
    bot = _bot()
    with (
        patch("app.bot._container_has_backup_schedule", return_value=True),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_backup_monthly(["2026-07"])
    _, kwargs = mock_send.call_args
    all_buttons = [btn for row in kwargs["keyboard"] for btn in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    # callback_data should encode the param for at least one button
    assert any("2026-07" in d for d in cb_data)


def test_cmd_backup_yearly_no_param_shows_keyboard():
    bot = _bot()
    with (
        patch("app.bot._container_has_backup_schedule", return_value=True),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._cmd_backup_yearly([])
    _, kwargs = mock_send.call_args
    assert "keyboard" in kwargs
    assert "previous year" in mock_send.call_args.args[0]


# ---------------------------------------------------------------------------
# TelegramBot._instance_buttons — backup availability reflected in buttons
# ---------------------------------------------------------------------------

def test_instance_buttons_no_check_backup_all_active():
    bot = _bot()
    rows = bot._instance_buttons("sync", param=None, check_backup=False)
    all_buttons = [btn for row in rows for btn in row]
    assert all(b["callback_data"] != "noop" for b in all_buttons)
    assert all("🚫" not in b["text"] for b in all_buttons)


def test_instance_buttons_check_backup_unavailable_shown_as_noop():
    bot = _bot()
    with patch("app.bot._container_has_backup_schedule", return_value=False):
        rows = bot._instance_buttons("backup_monthly", param=None, check_backup=True)
    all_buttons = [btn for row in rows for btn in row]
    assert all(b["callback_data"] == "noop" for b in all_buttons)
    assert all("🚫" in b["text"] for b in all_buttons)


def test_instance_buttons_check_backup_mixed():
    """One instance available, one not — only unavailable gets noop."""
    instances = {
        "david": InstanceConfig(name="David", container_name="proj-david-1"),
        "eli":   InstanceConfig(name="Eli",   container_name="proj-eli-1"),
    }
    bot = _bot(instances)

    def _has_schedule(container_name: str) -> bool:
        return "david" in container_name  # only david has it

    with patch("app.bot._container_has_backup_schedule", side_effect=_has_schedule):
        rows = bot._instance_buttons("backup_monthly", param=None, check_backup=True)

    all_buttons = {b["text"]: b["callback_data"] for row in rows for b in row}
    assert all_buttons["David"] != "noop"
    assert all_buttons["🚫 Eli"] == "noop"


def test_instance_buttons_rows_split_at_three():
    """More than 3 instances → buttons split into rows of max 3."""
    instances = {str(i): InstanceConfig(name=str(i), container_name=f"proj-{i}-1") for i in range(5)}
    bot = _bot(instances)
    rows = bot._instance_buttons("sync", param=None, check_backup=False)
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


def test_callback_query_backup_monthly_no_param_dispatches():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_backup") as mock_backup,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "backup_monthly:david", "message": {"chat": {"id": 42}}})
    mock_backup.assert_called_once()
    _, mode, param = mock_backup.call_args.args
    assert mode == "monthly"
    assert param is None


def test_callback_query_backup_monthly_with_param_dispatches():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_backup") as mock_backup,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "backup_monthly:2026-07:david", "message": {"chat": {"id": 42}}})
    _, mode, param = mock_backup.call_args.args
    assert mode == "monthly"
    assert param == "2026-07"


def test_callback_query_backup_yearly_dispatches():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_launch_backup") as mock_backup,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "backup_yearly:2025:david", "message": {"chat": {"id": 42}}})
    _, mode, param = mock_backup.call_args.args
    assert mode == "yearly"
    assert param == "2025"


def test_callback_query_unknown_instance_replies():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        bot._handle_callback_query({"id": "cq1", "data": "sync:nobody", "message": {"chat": {"id": 42}}})
    mock_send.assert_called_once()
    assert "Unknown" in mock_send.call_args.args[0] or "nknown" in mock_send.call_args.args[0]


def test_callback_query_malformed_data_does_not_raise():
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message"),
    ):
        bot._handle_callback_query({"id": "cq1", "data": "malformed", "message": {"chat": {"id": 42}}})
    # must not raise


# ---------------------------------------------------------------------------
# TelegramBot._launch_sync / _launch_backup
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


def test_launch_backup_without_schedule_sends_error():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch("app.bot._container_has_backup_schedule", return_value=False),
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        bot._launch_backup(inst, "monthly", None)

    mock_send.assert_called_once()
    assert "BACKUP_SCHEDULE" in mock_send.call_args.args[0]
    mock_thread.assert_not_called()


def test_launch_backup_with_schedule_sends_ack_and_starts_thread():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch("app.bot._container_has_backup_schedule", return_value=True),
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup(inst, "monthly", "2026-07")

    mock_send.assert_called_once()
    msg = mock_send.call_args.args[0]
    assert "David" in msg
    assert "2026" in msg
    mock_thread.assert_called_once()


def test_launch_backup_yearly_uses_correct_unit():
    bot = _bot()
    inst = bot._cfg.instances["david"]
    with (
        patch("app.bot._container_has_backup_schedule", return_value=True),
        patch.object(bot, "_send_message") as mock_send,
        patch("app.bot.threading.Thread") as mock_thread,
    ):
        mock_thread.return_value.start = MagicMock()
        bot._launch_backup(inst, "yearly", None)

    msg = mock_send.call_args.args[0]
    assert "year" in msg  # "previous year" — not "previous yearly"


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
    """Updates with neither message nor callback_query should be silently ignored."""
    bot = _bot()
    with (
        patch.object(bot, "_handle_message") as mock_msg,
        patch.object(bot, "_handle_callback_query") as mock_cb,
    ):
        bot._handle_update({"update_id": 1, "edited_message": {"text": "hi"}})
    mock_msg.assert_not_called()
    mock_cb.assert_not_called()


# ---------------------------------------------------------------------------
# TelegramBot._handle_callback_query — unknown cmd branch
# ---------------------------------------------------------------------------

def test_callback_query_unknown_cmd_logs_warning_and_does_not_raise():
    """A valid instance but unknown command in callback_data should log and not raise."""
    bot = _bot()
    with (
        patch.object(bot, "_answer_callback_query"),
        patch.object(bot, "_send_message") as mock_send,
    ):
        # "badcmd:david" — unknown cmd, known instance
        bot._handle_callback_query({"id": "cq1", "data": "badcmd:david", "message": {"chat": {"id": 42}}})
    # No message should be sent for an unknown cmd (just a warning log)
    mock_send.assert_not_called()
