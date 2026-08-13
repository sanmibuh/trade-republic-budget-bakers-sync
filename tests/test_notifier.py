from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from app.notifier import (
    Notifier,
    _escape_markdown,
    send_telegram_message,
)

# ---------------------------------------------------------------------------
# _escape_markdown
# ---------------------------------------------------------------------------


def test_escape_markdown_escapes_special_chars():
    result = _escape_markdown("hello_world*test")
    assert r"\_" in result  # underscore must be escaped
    assert r"\*" in result  # asterisk must be escaped


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

    with patch("app.notifier.http_post", return_value=mock_response) as mock_post:
        result = send_telegram_message("mytoken", "mychat", "Test message")

    assert result is True
    mock_post.assert_called_once()
    call_args = mock_post.call_args
    assert "mytoken" in call_args.args[0]
    assert call_args.kwargs["json"]["chat_id"] == "mychat"
    assert call_args.kwargs["json"]["text"] == "Test message"


def test_send_telegram_message_request_exception_returns_false():
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.RequestException("fail")

    with patch("app.notifier.http_post", return_value=mock_response):
        result = send_telegram_message("token", "chat", "msg")

    assert result is False


def test_send_telegram_message_http_error_returns_false():
    """HTTPError from Telegram API should be caught and return False."""
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Bad Request: can't parse entities"

    http_error = requests.HTTPError(response=mock_response)

    with patch("app.notifier.http_post") as mock_post:
        mock_post.return_value.raise_for_status.side_effect = http_error
        from app.notifier import send_telegram_message

        result = send_telegram_message(bot_token="tok", chat_id="chat", message="hello")

    assert result is False


def test_send_telegram_message_http_error_no_response_returns_false():
    """HTTPError with no response object should still be caught and return False."""
    http_error = requests.HTTPError(response=None)

    with patch("app.notifier.http_post") as mock_post:
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


def test_notifier_login_code_request_sends_message_with_instance():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        result = _make_notifier().login_code_request("david")
    assert result is True
    mock_send.assert_called_once()
    sent = mock_send.call_args.kwargs["message"]
    assert "david" in sent


def test_notifier_login_code_request_sends_force_reply():
    """login_code_request must include force_reply so Telegram opens the reply box."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().login_code_request("david")
    reply_markup = mock_send.call_args.kwargs.get("reply_markup")
    assert reply_markup is not None
    assert reply_markup.get("force_reply") is True


def test_notifier_login_code_request_does_not_backslash_escape_instance():
    """Instance sits inside an inline-code span; MarkdownV2 shows backslashes
    literally there, so a hyphenated instance must NOT be escaped (the user
    copies it verbatim into /code)."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().login_code_request("sync-1")
    sent = mock_send.call_args.kwargs["message"]
    assert "sync-1" in sent
    assert "sync\\-1" not in sent


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


def test_notifier_fetch_summary_does_not_send_message():
    """fetch_summary buffers data; it must NOT send a Telegram message immediately."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().fetch_summary(
            since="2024-01-01", until="2024-01-07", fetched=10, new=5, skipped=5
        )
    mock_send.assert_not_called()


def test_notifier_sync_complete_includes_fetch_summary_when_buffered():
    """After fetch_summary is called, sync_complete should include period and fetch counts."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        n = _make_notifier()
        n.fetch_summary(
            since="2024-01-01", until="2024-01-07", fetched=10, new=5, skipped=5
        )
        n.sync_complete(synced=5, failed=0, skipped=5)
    mock_send.assert_called_once()
    msg = mock_send.call_args.kwargs["message"]
    assert "2024" in msg
    assert "01" in msg
    assert "2024\\-01\\-07" in msg or "2024-01-07" in msg
    assert "10" in msg  # fetched count


def test_notifier_sync_complete_without_fetch_summary_still_works():
    """sync_complete without a prior fetch_summary must still send a valid message."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().sync_complete(synced=3, failed=0, skipped=1)
    mock_send.assert_called_once()
    msg = mock_send.call_args.kwargs["message"]
    assert "✅" in msg


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
            counts={
                "records": 42,
                "accounts": 3,
                "categories": 18,
                "budgets": 5,
                "labels": 7,
            },
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
            counts={
                "records": 300,
                "accounts": 3,
                "categories": 20,
                "budgets": 4,
                "labels": 5,
                "monthly_removed": 12,
            },
        )
    msg = mock_send.call_args.kwargs["message"]
    assert "12" in msg
    assert "Yearly" in msg or "yearly" in msg


def test_notifier_backup_complete_no_credentials_returns_false():
    notifier = Notifier(bot_token=None, chat_id=None, owner_name="David")
    result = notifier.backup_complete(
        mode="monthly",
        period="2026-07",
        date_from="2026-07-01",
        date_to="2026-07-31",
        counts={
            "records": 1,
            "accounts": 1,
            "categories": 1,
            "budgets": 0,
            "labels": 0,
        },
    )
    assert result is False


def test_notifier_backup_complete_no_monthly_removed_field():
    """monthly_removed not present → no mention of removed files in message."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().backup_complete(
            mode="monthly",
            period="2026-07",
            date_from="2026-07-01",
            date_to="2026-07-31",
            counts={
                "records": 10,
                "accounts": 2,
                "categories": 5,
                "budgets": 1,
                "labels": 2,
            },
        )
    msg = mock_send.call_args.kwargs["message"]
    assert "removed" not in msg.lower()


def test_notifier_backup_complete_includes_filename():
    """filename kwarg → appears in notification message."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().backup_complete(
            mode="monthly",
            period="2026-07",
            date_from="2026-07-01",
            date_to="2026-07-31",
            counts={
                "records": 10,
                "accounts": 2,
                "categories": 5,
                "budgets": 1,
                "labels": 2,
            },
            filename="wallet-monthly-2026-07.json",
        )
    msg = mock_send.call_args.kwargs["message"]
    assert "wallet" in msg
    assert "2026" in msg


def test_notifier_backup_complete_filename_optional():
    """filename defaults to None → no File line in message."""
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().backup_complete(
            mode="monthly",
            period="2026-07",
            date_from="2026-07-01",
            date_to="2026-07-31",
            counts={
                "records": 10,
                "accounts": 2,
                "categories": 5,
                "budgets": 1,
                "labels": 2,
            },
        )
    msg = mock_send.call_args.kwargs["message"]
    assert "File" not in msg


# ---------------------------------------------------------------------------
# Notifier.missing_api_result
# ---------------------------------------------------------------------------


def test_notifier_missing_api_result_sends_message():
    with patch("app.notifier.send_telegram_message", return_value=True) as mock_send:
        _make_notifier().missing_api_result("evt-123", [2, 3])
    mock_send.assert_called_once()
    msg = mock_send.call_args.kwargs["message"]
    assert "evt" in msg
    assert "123" in msg
    assert "2" in msg
    assert "3" in msg


def test_notifier_missing_api_result_returns_send_result():
    with patch("app.notifier.send_telegram_message", return_value=False):
        result = _make_notifier().missing_api_result("evt-x", [0])
    assert result is False
