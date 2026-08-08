from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.notifier import (
    Notifier,
    _escape_markdown,
    notify_authentication_required,
    notify_error,
    notify_fetch_summary,
    notify_login_failed,
    notify_login_required,
    notify_login_success,
    notify_sync_complete,
    notify_unknown_event_type,
    send_telegram_message,
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


def test_send_telegram_message_request_exception_returns_false():
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
    with patch("app.notifier.send_telegram_message", return_value=False):
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


def test_notify_sync_complete_with_excluded():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        notify_sync_complete("tok", "chat", "David", synced=22, failed=0, skipped=0, excluded=2)
    msg = mock_send.call_args.kwargs["message"]
    assert "22" in msg
    assert "2" in msg
    assert "zero amount" in msg.lower() or "Excluded" in msg


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


def test_send_telegram_message_http_error_returns_false():
    """HTTPError from Telegram API should be caught and return False."""
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request: can't parse entities"

    http_error = requests.HTTPError(response=mock_response)

    with patch("requests.post") as mock_post:
        mock_post.return_value.raise_for_status.side_effect = http_error
        from app.notifier import send_telegram_message
        result = send_telegram_message(bot_token="tok", chat_id="chat", message="hello")

    assert result is False


def test_send_telegram_message_http_error_no_response_returns_false():
    """HTTPError with no response object should still be caught and return False."""
    http_error = requests.HTTPError(response=None)

    with patch("requests.post") as mock_post:
        mock_post.return_value.raise_for_status.side_effect = http_error
        from app.notifier import send_telegram_message
        result = send_telegram_message(bot_token="tok", chat_id="chat", message="hello")

    assert result is False


# ---------------------------------------------------------------------------
# Notifier class
# ---------------------------------------------------------------------------

def _make_notifier() -> Notifier:
    return Notifier(bot_token="tok", chat_id="chat", owner_name="David")


def test_notifier_login_required_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().login_required()
    assert result is True
    mock_send.assert_called_once()


def test_notifier_login_success_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().login_success()
    assert result is True
    mock_send.assert_called_once()


def test_notifier_login_failed_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().login_failed()
    assert result is True
    mock_send.assert_called_once()


def test_notifier_authentication_required_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().authentication_required()
    assert result is True
    mock_send.assert_called_once()


def test_notifier_error_includes_exception_info():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().error(ValueError("something broke"))
    msg = mock_send.call_args.kwargs["message"]
    assert "ValueError" in msg
    assert "something broke" in msg


def test_notifier_fetch_summary_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().fetch_summary(since="2024-01-01", until="2024-01-07", fetched=10, new=5, skipped=5)
    assert result is True
    msg = mock_send.call_args.kwargs["message"]
    assert "10" in msg
    assert "5" in msg


def test_notifier_sync_complete_success():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().sync_complete(synced=10, failed=0, skipped=2)
    assert "✅" in mock_send.call_args.kwargs["message"]


def test_notifier_sync_complete_all_failed():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().sync_complete(synced=0, failed=5, skipped=0)
    assert "❌" in mock_send.call_args.kwargs["message"]


def test_notifier_sync_complete_partial():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().sync_complete(synced=3, failed=2, skipped=0)
    assert "⚠" in mock_send.call_args.kwargs["message"]


def test_notifier_no_credentials_returns_false():
    notifier = Notifier(bot_token=None, chat_id=None, owner_name="David")
    assert notifier.login_required() is False
    assert notifier.sync_complete(synced=1, failed=0, skipped=0) is False


# ---------------------------------------------------------------------------
# unknown_event_type
# ---------------------------------------------------------------------------

def test_notifier_unknown_event_type_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().unknown_event_type("MYSTERY_TYPE")
    assert result is True
    msg = mock_send.call_args.kwargs["message"]
    assert "MYSTERY" in msg  # underscore gets escaped in MarkdownV2
    assert "⚠" in msg


def test_notifier_unknown_event_type_no_credentials():
    notifier = Notifier(bot_token=None, chat_id=None, owner_name="David")
    assert notifier.unknown_event_type("MYSTERY_TYPE") is False


def test_notify_unknown_event_type_compat():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = notify_unknown_event_type("tok", "chat", "David", "NEW_EVENT")
    assert result is True
    assert "NEW" in mock_send.call_args.kwargs["message"]  # underscore gets escaped


# ---------------------------------------------------------------------------
# backup_complete
# ---------------------------------------------------------------------------

def test_notifier_backup_complete_monthly_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().backup_complete(
            mode="monthly",
            period="2026-07",
            date_from="2026-07-01",
            date_to="2026-07-31",
            counts={"records": 42, "accounts": 3, "categories": 18, "budgets": 5, "labels": 7},
        )
    assert result is True
    msg = mock_send.call_args.kwargs["message"]
    assert "📆" in msg
    assert "42" in msg
    assert "3" in msg
    assert "18" in msg
    assert "Monthly" in msg or "monthly" in msg


def test_notifier_backup_complete_yearly_includes_removed():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().backup_complete(
            mode="yearly",
            period="2025",
            date_from="2025-01-01",
            date_to="2025-12-31",
            counts={"records": 300, "accounts": 3, "categories": 20,
                    "budgets": 4, "labels": 5, "monthly_removed": 12},
        )
    msg = mock_send.call_args.kwargs["message"]
    assert "12" in msg
    assert "Yearly" in msg or "yearly" in msg


def test_notifier_backup_complete_no_credentials_returns_false():
    notifier = Notifier(bot_token=None, chat_id=None, owner_name="David")
    result = notifier.backup_complete(
        mode="monthly", period="2026-07",
        date_from="2026-07-01", date_to="2026-07-31",
        counts={"records": 1, "accounts": 1, "categories": 1, "budgets": 0, "labels": 0},
    )
    assert result is False


def test_notifier_backup_complete_no_monthly_removed_field():
    """monthly_removed not present → no mention of removed files in message."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().backup_complete(
            mode="monthly", period="2026-07",
            date_from="2026-07-01", date_to="2026-07-31",
            counts={"records": 10, "accounts": 2, "categories": 5, "budgets": 1, "labels": 2},
        )
    msg = mock_send.call_args.kwargs["message"]
    assert "removed" not in msg.lower()
