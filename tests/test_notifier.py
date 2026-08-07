from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.notifier import (
    _escape_markdown,
    send_telegram_message,
    notify_authentication_required,
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


def test_send_telegram_message_returns_false_on_http_error():
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
