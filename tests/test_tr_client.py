from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from app.tr_client import connect_trade_republic, fetch_timeline_events, LoginFailedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SINCE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_client(*, needs_authenticator: bool = True, resume: bool = False) -> MagicMock:
    client = MagicMock()
    client.resume_websession.return_value = resume
    client.weblogin_needs_authenticator = needs_authenticator
    return client


# ---------------------------------------------------------------------------
# connect_trade_republic — session resume
# ---------------------------------------------------------------------------

def test_connect_resumes_existing_session(tmp_path):
    client = _make_client(resume=True)
    with patch("pytr.api.TradeRepublicApi", return_value=client):
        result = connect_trade_republic("+34000000000", "1234", tmp_path)

    client.resume_websession.assert_called_once()
    client.initiate_weblogin.assert_not_called()
    assert result is client


def test_connect_does_not_notify_when_session_resumes(tmp_path):
    client = _make_client(resume=True)
    on_login_required = MagicMock()
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=client):
        connect_trade_republic(
            "+34000000000", "1234", tmp_path,
            on_login_required=on_login_required,
            on_login_success=on_login_success,
        )
    on_login_required.assert_not_called()
    on_login_success.assert_not_called()


# ---------------------------------------------------------------------------
# connect_trade_republic — authenticator flow
# ---------------------------------------------------------------------------

def test_connect_authenticator_flow(tmp_path):
    """Authenticator: complete_weblogin(verify_code=...) + _await_weblogin_confirmation + save."""
    client = _make_client(needs_authenticator=True)
    with patch("pytr.api.TradeRepublicApi", return_value=client), \
         patch("builtins.input", return_value="123456"):
        result = connect_trade_republic("+34000000000", "1234", tmp_path)

    client.initiate_weblogin.assert_called_once()
    client.complete_weblogin.assert_called_once_with(verify_code="123456")
    client._await_weblogin_confirmation.assert_called_once()
    client.save_websession.assert_called_once()
    assert result is client


def test_connect_authenticator_calls_on_login_required(tmp_path):
    client = _make_client(needs_authenticator=True)
    on_login_required = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=client), \
         patch("builtins.input", return_value="123456"):
        connect_trade_republic("+34000000000", "1234", tmp_path, on_login_required=on_login_required)
    on_login_required.assert_called_once()


def test_connect_authenticator_calls_on_login_success(tmp_path):
    client = _make_client(needs_authenticator=True)
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=client), \
         patch("builtins.input", return_value="123456"):
        connect_trade_republic("+34000000000", "1234", tmp_path, on_login_success=on_login_success)
    on_login_success.assert_called_once()


def test_connect_authenticator_raises_login_failed_on_bad_code(tmp_path):
    client = _make_client(needs_authenticator=True)
    client.complete_weblogin.side_effect = Exception("VALIDATION_CODE_INVALID")
    with patch("pytr.api.TradeRepublicApi", return_value=client), \
         patch("builtins.input", return_value="000000"):
        with pytest.raises(LoginFailedError):
            connect_trade_republic("+34000000000", "1234", tmp_path)


def test_connect_authenticator_no_success_callback_on_failure(tmp_path):
    client = _make_client(needs_authenticator=True)
    client.complete_weblogin.side_effect = Exception("401")
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=client), \
         patch("builtins.input", return_value="000000"):
        with pytest.raises(LoginFailedError):
            connect_trade_republic("+34000000000", "1234", tmp_path, on_login_success=on_login_success)
    on_login_success.assert_not_called()


# ---------------------------------------------------------------------------
# connect_trade_republic — app approval flow
# ---------------------------------------------------------------------------

def test_connect_app_approval_flow(tmp_path):
    """App approval: complete_weblogin() with no args (polls internally)."""
    client = _make_client(needs_authenticator=False)
    with patch("pytr.api.TradeRepublicApi", return_value=client):
        result = connect_trade_republic("+34000000000", "1234", tmp_path)

    client.initiate_weblogin.assert_called_once()
    client.complete_weblogin.assert_called_once_with()
    client._await_weblogin_confirmation.assert_not_called()
    assert result is client


# ---------------------------------------------------------------------------
# connect_trade_republic — API constructor arguments
# ---------------------------------------------------------------------------

def test_connect_passes_correct_files_to_api(tmp_path):
    client = _make_client(resume=True)
    with patch("pytr.api.TradeRepublicApi", return_value=client) as mock_api:
        connect_trade_republic("+34000000000", "1234", tmp_path)

    kw = mock_api.call_args.kwargs
    assert kw["phone_no"] == "+34000000000"
    assert kw["pin"] == "1234"
    assert kw["credentials_file"] == str(tmp_path / "credentials.json")
    assert kw["cookies_file"] == str(tmp_path / "cookies.txt")
    assert kw["use_v2_login"] is True
    assert kw.get("save_cookies") is True


# ---------------------------------------------------------------------------
# fetch_timeline_events — settings() warm-up
# ---------------------------------------------------------------------------

def test_fetch_calls_settings_before_subscription():
    client = MagicMock()
    client.timeline_transactions.return_value = []
    fetch_timeline_events(client, since=SINCE)
    client.settings.assert_called_once()


def test_fetch_continues_if_settings_raises():
    client = MagicMock()
    client.settings.side_effect = Exception("401")
    client.timeline_transactions.return_value = []
    # Should not raise — settings failure is logged as a warning
    result = fetch_timeline_events(client, since=SINCE)
    assert result == []


# ---------------------------------------------------------------------------
# fetch_timeline_events — method selection
# ---------------------------------------------------------------------------

def test_fetch_uses_timeline_transactions():
    client = MagicMock()
    client.timeline_transactions.return_value = [{"id": "1"}]
    result = fetch_timeline_events(client, since=SINCE)
    client.timeline_transactions.assert_called_once_with()
    assert result == [{"id": "1"}]


def test_fetch_falls_back_to_timeline_activity_log(tmp_path):
    """If timeline_transactions is not present, use timeline_activity_log."""
    client = MagicMock(spec=["settings", "timeline_activity_log", "run_blocking"])
    client.timeline_activity_log.return_value = [{"id": "2"}]
    result = fetch_timeline_events(client, since=SINCE)
    client.timeline_activity_log.assert_called_once_with()
    assert result == [{"id": "2"}]


def test_fetch_raises_when_no_method_found():
    client = MagicMock(spec=["settings"])
    with pytest.raises(RuntimeError, match="No supported timeline method"):
        fetch_timeline_events(client, since=SINCE)


def test_fetch_raises_on_trade_republic_error():
    """A TradeRepublicError from the subscription is re-raised as RuntimeError."""
    from pytr.api import TradeRepublicError
    client = MagicMock()
    client.timeline_transactions.return_value = MagicMock(__await__=None)
    client.run_blocking.side_effect = TradeRepublicError("1", {}, {"errors": []})
    with pytest.raises(RuntimeError):
        fetch_timeline_events(client, since=SINCE)


# ---------------------------------------------------------------------------
# fetch_timeline_events — result parsing
# ---------------------------------------------------------------------------

def test_fetch_parses_list_directly():
    client = MagicMock()
    client.timeline_transactions.return_value = [{"id": "1"}, {"id": "2"}]
    assert fetch_timeline_events(client, since=SINCE) == [{"id": "1"}, {"id": "2"}]


def test_fetch_parses_dict_with_items():
    client = MagicMock()
    client.timeline_transactions.return_value = {"items": [{"id": "1"}]}
    assert fetch_timeline_events(client, since=SINCE) == [{"id": "1"}]


def test_fetch_parses_dict_with_data():
    client = MagicMock()
    client.timeline_transactions.return_value = {"data": [{"id": "2"}]}
    assert fetch_timeline_events(client, since=SINCE) == [{"id": "2"}]


def test_fetch_filters_non_dict_items():
    client = MagicMock()
    client.timeline_transactions.return_value = [{"id": "ok"}, "string", 42, None]
    assert fetch_timeline_events(client, since=SINCE) == [{"id": "ok"}]


def test_fetch_returns_empty_on_none():
    client = MagicMock()
    client.timeline_transactions.return_value = None
    assert fetch_timeline_events(client, since=SINCE) == []


def test_fetch_returns_empty_on_unexpected_type():
    client = MagicMock()
    client.timeline_transactions.return_value = "unexpected"
    assert fetch_timeline_events(client, since=SINCE) == []


def test_fetch_handles_async_coroutine():
    import asyncio

    async def _coro():
        return [{"id": "async-event"}]

    client = MagicMock()
    client.timeline_transactions.return_value = _coro()
    client.run_blocking.side_effect = lambda coro, timeout=5.0: asyncio.run(coro)

    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "async-event"}]
    client.run_blocking.assert_called_once()
