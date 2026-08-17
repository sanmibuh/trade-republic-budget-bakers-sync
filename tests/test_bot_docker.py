"""Tests for app.bot_docker — Docker exec helpers."""

from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch

from app.bot_docker import (
    _docker_check_awaiting_code,
    _docker_check_session,
    _docker_client_ctx,
    _docker_container_status,
    _docker_exec_silent,
    _docker_last_sync_summary,
    _docker_logs_today,
    _format_sync_timestamp,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_docker_client(exit_code: int = 0, output: bytes = b"") -> MagicMock:
    container = MagicMock()
    container.exec_run.return_value = (exit_code, output)
    client = MagicMock()
    client.containers.get.return_value = container
    return client


# ---------------------------------------------------------------------------
# _docker_exec_silent
# ---------------------------------------------------------------------------


def test_docker_exec_silent_success():
    client = _make_docker_client(0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"])
    client.containers.get.assert_called_once_with("my-container")
    args = client.containers.get.return_value.exec_run.call_args.args[0]
    assert "python" in args
    assert "sync" in args


def test_docker_exec_silent_failure_does_not_raise():
    client = _make_docker_client(1, b"error output")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"])  # must not raise


def test_docker_exec_silent_failure_calls_on_error():
    on_error = MagicMock()
    client = _make_docker_client(1, b"something broke")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"], on_error=on_error)
    on_error.assert_called_once()
    assert "my-container" in on_error.call_args.args[0]


def test_docker_exec_silent_exception_calls_on_error():
    on_error = MagicMock()
    client = MagicMock()
    client.containers.get.side_effect = Exception("unexpected")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"], on_error=on_error)
    on_error.assert_called_once()


def test_docker_exec_silent_success_does_not_call_on_error():
    on_error = MagicMock()
    client = _make_docker_client(0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"], on_error=on_error)
    on_error.assert_not_called()


def test_docker_exec_silent_success_calls_on_success():
    on_success = MagicMock()
    client = _make_docker_client(0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["login"], on_success=on_success)
    on_success.assert_called_once()


def test_docker_exec_silent_failure_does_not_call_on_success():
    on_success = MagicMock()
    client = _make_docker_client(1, b"boom")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["login"], on_success=on_success)
    on_success.assert_not_called()


def test_docker_exec_silent_container_not_found_does_not_raise():
    import docker.errors

    client = MagicMock()
    client.containers.get.side_effect = docker.errors.NotFound("not found")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["sync"])


def test_docker_exec_silent_passes_app_command_args():
    client = _make_docker_client(0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_exec_silent("my-container", ["backup", "monthly", "2026-07"])
    args = client.containers.get.return_value.exec_run.call_args.args[0]
    assert "backup" in args
    assert "monthly" in args
    assert "2026-07" in args


# ---------------------------------------------------------------------------
# _docker_check_session
# ---------------------------------------------------------------------------


def test_docker_check_session_returns_true_when_exit_zero():
    client = _make_docker_client(exit_code=0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_session("my-container") is True


def test_docker_check_session_returns_false_when_exit_one():
    client = _make_docker_client(exit_code=1)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_session("my-container") is False


def test_docker_check_session_returns_none_on_unexpected_exit_code():
    client = _make_docker_client(exit_code=2)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_session("my-container") is None


def test_docker_check_session_returns_none_on_exception():
    client = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_session("my-container") is None


def test_docker_check_session_invokes_check_session_command():
    client = _make_docker_client(exit_code=0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_check_session("my-container")
    cmd = client.containers.get.return_value.exec_run.call_args.args[0]
    assert cmd == ["python", "-m", "app", "check-session"]


def test_docker_check_session_uses_explicit_client():
    client = _make_docker_client(exit_code=0)
    assert _docker_check_session("my-container", client=client) is True
    client.containers.get.assert_called_once_with("my-container")


# ---------------------------------------------------------------------------
# _docker_client_ctx
# ---------------------------------------------------------------------------


def test_docker_client_ctx_yields_client_and_closes_it():
    client = MagicMock()
    with (
        patch("app.bot_docker.docker.from_env", return_value=client),
        _docker_client_ctx() as c,
    ):
        assert c is client
    client.close.assert_called_once()


def test_docker_client_ctx_yields_none_on_init_failure():
    with (
        patch(
            "app.bot_docker.docker.from_env",
            side_effect=Exception("daemon unreachable"),
        ),
        _docker_client_ctx() as c,
    ):
        assert c is None


# ---------------------------------------------------------------------------
# _docker_container_status
# ---------------------------------------------------------------------------


def test_docker_container_status_returns_running_state():
    client = MagicMock()
    container = MagicMock()
    container.status = "running"
    client.containers.get.return_value = container
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_container_status("my-container") == "running"


def test_docker_container_status_returns_none_on_exception():
    client = MagicMock()
    client.containers.get.side_effect = Exception("boom")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_container_status("my-container") is None


def test_docker_container_status_uses_explicit_client():
    client = MagicMock()
    container = MagicMock()
    container.status = "exited"
    client.containers.get.return_value = container
    assert _docker_container_status("my-container", client=client) == "exited"


# ---------------------------------------------------------------------------
# _docker_logs_today
# ---------------------------------------------------------------------------


def test_docker_logs_today_returns_decoded_output():
    container = MagicMock()
    container.logs.return_value = b"INFO sync: done\n"
    client = MagicMock()
    client.containers.get.return_value = container
    since = datetime.datetime(2026, 8, 11, 0, 0, 0, tzinfo=datetime.UTC)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        result = _docker_logs_today("my-container", since=since)
    assert "done" in result


def test_docker_logs_today_decodes_invalid_bytes():
    container = MagicMock()
    container.logs.return_value = b"ok\xff\xfe"
    client = MagicMock()
    client.containers.get.return_value = container
    since = datetime.datetime(2026, 8, 11, 0, 0, 0, tzinfo=datetime.UTC)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        result = _docker_logs_today("my-container", since=since)
    assert "ok" in result


def test_docker_logs_today_uses_explicit_client():
    container = MagicMock()
    container.logs.return_value = b"explicit client log\n"
    client = MagicMock()
    client.containers.get.return_value = container
    since = datetime.datetime(2026, 8, 11, 0, 0, 0, tzinfo=datetime.UTC)
    result = _docker_logs_today("my-container", since=since, client=client)
    assert "explicit client log" in result


# ---------------------------------------------------------------------------
# _docker_last_sync_summary
# ---------------------------------------------------------------------------


def test_docker_last_sync_summary_parses_success_from_db():
    client = _make_docker_client(
        output=b'{"status":"success","ran_at":"2026-08-11T10:00:00+00:00","saved":3,"failed":0,"excluded":1}'
    )
    with patch("app.bot_docker.docker.from_env", return_value=client):
        result = _docker_last_sync_summary("my-container")
    assert (
        result == "✅ success at 2026/08/11 10:00 UTC · saved 3 · failed 0 · excluded 1"
    )


def test_docker_last_sync_summary_returns_none_when_no_db_row():
    client = _make_docker_client(output=b"null")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_last_sync_summary("my-container") is None


def test_docker_last_sync_summary_returns_none_on_invalid_payload():
    client = _make_docker_client(output=b"not json")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_last_sync_summary("my-container") is None


def test_docker_last_sync_summary_returns_none_on_nonzero_exit_code():
    client = _make_docker_client(exit_code=1, output=b"boom")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_last_sync_summary("my-container") is None


def test_docker_last_sync_summary_uses_explicit_client():
    client = _make_docker_client(
        output=b'{"status":"success","ran_at":"2026-08-11T10:00:00+00:00","saved":1,"failed":0,"excluded":0}'
    )
    assert _docker_last_sync_summary("my-container", client=client) == (
        "✅ success at 2026/08/11 10:00 UTC · saved 1 · failed 0 · excluded 0"
    )


# ---------------------------------------------------------------------------
# _format_sync_timestamp
# ---------------------------------------------------------------------------


def test_format_sync_timestamp_iso_with_timezone():
    assert _format_sync_timestamp("2026-08-11T10:00:00+00:00") == "2026/08/11 10:00 UTC"


def test_format_sync_timestamp_naive_datetime_string():
    assert _format_sync_timestamp("2026-08-11 10:00:00") == "2026/08/11 10:00 UTC"


def test_format_sync_timestamp_returns_raw_on_invalid_string():
    raw = "not-a-timestamp"
    assert _format_sync_timestamp(raw) == raw


def test_docker_last_sync_summary_returns_none_for_unknown_status():
    """_docker_last_sync_summary must return None when status is not a known value."""
    client = _make_docker_client(
        output=b'{"status":"pending","ran_at":"2026-08-11T10:00:00+00:00"}'
    )
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_last_sync_summary("my-container") is None


# ---------------------------------------------------------------------------
# _docker_check_awaiting_code
# ---------------------------------------------------------------------------


def test_docker_check_awaiting_code_returns_true_when_exit_zero():
    """Exit code 0 means the container has an active pending-login marker."""
    client = _make_docker_client(exit_code=0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_awaiting_code("my-container") is True


def test_docker_check_awaiting_code_returns_false_when_exit_one():
    """Exit code 1 means no pending-login marker is present."""
    client = _make_docker_client(exit_code=1)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_awaiting_code("my-container") is False


def test_docker_check_awaiting_code_returns_none_on_unexpected_exit_code():
    client = _make_docker_client(exit_code=2)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_awaiting_code("my-container") is None


def test_docker_check_awaiting_code_returns_none_on_exception():
    client = MagicMock()
    client.containers.get.side_effect = Exception("not found")
    with patch("app.bot_docker.docker.from_env", return_value=client):
        assert _docker_check_awaiting_code("my-container") is None


def test_docker_check_awaiting_code_invokes_check_pending_command():
    """Must call ``python -m app check-pending`` inside the container."""
    client = _make_docker_client(exit_code=0)
    with patch("app.bot_docker.docker.from_env", return_value=client):
        _docker_check_awaiting_code("my-container")
    cmd = client.containers.get.return_value.exec_run.call_args.args[0]
    assert "check-pending" in cmd
