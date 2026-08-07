from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.notifier import (
    _escape_markdown,
    send_telegram_message,
    notify_authentication_required,
    notify_login_required,
    notify_login_failed,
    notify_login_success,
    notify_error,
    notify_fetch_summary,
    notify_sync_complete,
)


# ---------------------------------------------------------------------------
# _escape_markdown
# ---------------------------------------------------------------------------

def test_escape_markdown_escapes_special_chars():
    result = _escape_markdown("hello_world*test")
    assert r"\_" in result   # underscore must be escaped
    assert r"\*" in result   # asterisk must be escaped


def test_escape_markdown_plain_text_unchanged():
    assert _escape_markdown("hello world") == "hello world"


def test_escape_markdown_escapes_dot():
    assert r"\." in _escape_markdown("3.14")


def test_escape_markdown_escapes_underscore():
    assert r"\_" in _escape_markdown("some_name")


def test_escape_markdown_escapes_backslash_first():
    # backslash must be escaped before other chars to avoid double-escaping
    result = _escape_markdown("a\\b")
    assert result.startswith("a\\\\b") or "\\\\" in result


# ---------------------------------------------------------------------------
# send_telegram_message
# ---------------------------------------------------------------------------

def test_send_telegram_message_returns_false_when_no_token():
    assert send_telegram_message(None, "123", "hello") is False


def test_send_telegram_message_returns_false_when_no_chat_id():
    assert send_telegram_message("token", None, "hello") is False


def test_send_telegram_message_returns_false_when_both_missing():
    assert send_telegram_message(None, None, "hello") is False


def test_send_telegram_message_returns_true_on_success():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("app.notifier.requests.post", return_value=mock_response) as mock_post:
        result = send_telegram_message("mytoken", "mychat", "Test message")

    assert result is True
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "mytoken" in call_args.args[0]
    assert call_args.kwargs["json"]["chat_id"] == "mychat"
    assert call_args.kwargs["json"]["text"] == "Test message"


def test_send_telegram_message_disables_ssl_verify():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("app.notifier.requests.post", return_value=mock_response) as mock_post:
        send_telegram_message("tok", "chat", "msg")

    assert mock_post.call_args.kwargs["verify"] is False



    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.RequestException("fail")

    with patch("app.notifier.requests.post", return_value=mock_response):
        result = send_telegram_message("token", "chat", "msg")

    assert result is False


# ---------------------------------------------------------------------------
# notify_authentication_required
# ---------------------------------------------------------------------------

def test_notify_authentication_required_calls_send():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_authentication_required("tok", "chat", "myuser")

    assert result is True
    mock_send.assert_called_once()
    # owner name should appear in the message (possibly escaped)
    message = mock_send.call_args.args[2] if mock_send.call_args.args else mock_send.call_args.kwargs.get("message", "")
    assert "myuser" in message or "myuser".replace("_", r"\_") in message


def test_notify_authentication_required_no_credentials():
    with patch("app.notifier.send_telegram_message", return_value=False) as mock_send:
        result = notify_authentication_required(None, None, "owner")
    assert result is False


# ---------------------------------------------------------------------------
# notify_login_required / notify_login_success
# ---------------------------------------------------------------------------

def test_notify_login_required_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_login_required("tok", "chat", "myuser")
    assert result is True
    mock_send.assert_called_once()


def test_notify_login_failed_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_login_failed("tok", "chat", "myuser")
    assert result is True
    mock_send.assert_called_once()


def test_notify_login_success_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_login_success("tok", "chat", "myuser")
    assert result is True
    mock_send.assert_called_once()


# ---------------------------------------------------------------------------
# notify_error
# ---------------------------------------------------------------------------

def test_notify_error_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_error("tok", "chat", "myuser", ValueError("something went wrong"))

    assert result is True
    mock_send.assert_called_once()
    message = mock_send.call_args.kwargs["message"]
    assert "ValueError" in message
    assert "something went wrong" in message


def test_notify_error_returns_false_without_credentials():
    result = notify_error(None, None, "owner", RuntimeError("boom"))
    assert result is False


# ---------------------------------------------------------------------------
# notify_fetch_summary
# ---------------------------------------------------------------------------

def test_notify_fetch_summary_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_fetch_summary(
            "tok", "chat", "David",
            since="2026-07-31", until="2026-08-07",
            fetched=30, new=24, skipped=6,
        )
    assert result is True
    msg = mock_send.call_args.kwargs["message"]
    assert "30" in msg
    assert "24" in msg
    assert "6" in msg


def test_notify_fetch_summary_no_credentials():
    result = notify_fetch_summary(
        None, None, "David",
        since="2026-07-31", until="2026-08-07",
        fetched=10, new=5, skipped=5,
    )
    assert result is False


# ---------------------------------------------------------------------------
# notify_sync_complete
# ---------------------------------------------------------------------------

def test_notify_sync_complete_all_ok():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_sync_complete("tok", "chat", "David", synced=24, failed=0, skipped=6)
    assert result is True
    msg = mock_send.call_args.kwargs["message"]
    assert "24" in msg
    assert "✅" in msg


def test_notify_sync_complete_all_failed():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        notify_sync_complete("tok", "chat", "David", synced=0, failed=24, skipped=0)
    msg = mock_send.call_args.kwargs["message"]
    assert "❌" in msg


def test_notify_sync_complete_partial():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        notify_sync_complete("tok", "chat", "David", synced=10, failed=14, skipped=0)
    msg = mock_send.call_args.kwargs["message"]
    assert "⚠" in msg


def test_notify_sync_complete_no_credentials():
    result = notify_sync_complete(None, None, "David", synced=5, failed=0, skipped=0)
    assert result is False
