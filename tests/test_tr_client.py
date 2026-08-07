from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.tr_client import connect_trade_republic, fetch_timeline_events, LoginFailedError


# ---------------------------------------------------------------------------
# connect_trade_republic
# ---------------------------------------------------------------------------

def _make_api_mock(resume_returns: bool):
    mock_client = MagicMock()
    mock_client.resume_websession.return_value = resume_returns
    return mock_client


def test_connect_resumes_existing_session(tmp_path):
    mock_client = _make_api_mock(resume_returns=True)
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client):
        result = connect_trade_republic("+34000000000", "1234", tmp_path)

    mock_client.resume_websession.assert_called_once()
    mock_client.initiate_weblogin.assert_not_called()
    assert result is mock_client


def test_connect_initiates_login_when_no_session(tmp_path):
    mock_client = _make_api_mock(resume_returns=False)
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client), \
         patch("builtins.input", return_value="123456"):
        result = connect_trade_republic("+34000000000", "1234", tmp_path)

    mock_client.initiate_weblogin.assert_called_once()
    mock_client.complete_weblogin.assert_called_once_with(verify_code="123456")
    assert result is mock_client


def test_connect_calls_on_login_required_callback(tmp_path):
    mock_client = _make_api_mock(resume_returns=False)
    on_login_required = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client), \
         patch("builtins.input", return_value="000000"):
        connect_trade_republic("+34000000000", "1234", tmp_path, on_login_required=on_login_required)
    on_login_required.assert_called_once()


def test_connect_calls_on_login_success_callback(tmp_path):
    mock_client = _make_api_mock(resume_returns=False)
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client), \
         patch("builtins.input", return_value="000000"):
        connect_trade_republic("+34000000000", "1234", tmp_path, on_login_success=on_login_success)
    on_login_success.assert_called_once()


def test_connect_raises_login_failed_error_on_bad_2fa(tmp_path):
    mock_client = _make_api_mock(resume_returns=False)
    mock_client.complete_weblogin.side_effect = Exception("401 Unauthorized")
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client), \
         patch("builtins.input", return_value="wrong"):
        with pytest.raises(LoginFailedError):
            connect_trade_republic("+34000000000", "1234", tmp_path)


def test_connect_does_not_call_success_callback_on_failed_login(tmp_path):
    mock_client = _make_api_mock(resume_returns=False)
    mock_client.complete_weblogin.side_effect = Exception("401")
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client), \
         patch("builtins.input", return_value="wrong"):
        with pytest.raises(LoginFailedError):
            connect_trade_republic("+34000000000", "1234", tmp_path, on_login_success=on_login_success)
    on_login_success.assert_not_called()
    mock_client = _make_api_mock(resume_returns=True)
    on_login_required = MagicMock()
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client):
        connect_trade_republic("+34000000000", "1234", tmp_path,
                               on_login_required=on_login_required,
                               on_login_success=on_login_success)
    on_login_required.assert_not_called()
    on_login_success.assert_not_called()


def test_connect_passes_correct_files_to_api(tmp_path):
    mock_client = _make_api_mock(resume_returns=True)
    with patch("pytr.api.TradeRepublicApi", return_value=mock_client) as mock_api:
        connect_trade_republic("+34000000000", "1234", tmp_path)

    call_kwargs = mock_api.call_args.kwargs
    assert call_kwargs["phone_no"] == "+34000000000"
    assert call_kwargs["pin"] == "1234"
    assert call_kwargs["credentials_file"] == str(tmp_path / "credentials.json")
    assert call_kwargs["cookies_file"] == str(tmp_path / "cookies.txt")
    assert call_kwargs["use_v2_login"] is True


# ---------------------------------------------------------------------------
# fetch_timeline_events
# ---------------------------------------------------------------------------

SINCE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_fetch_timeline_returns_list_directly():
    events = [{"id": "1", "eventType": "INTEREST_PAYMENT"}, {"id": "2"}]
    client = MagicMock()
    client.timeline.return_value = events
    result = fetch_timeline_events(client, since=SINCE)
    assert result == events


def test_fetch_timeline_returns_dict_with_items():
    events = [{"id": "1"}]
    client = MagicMock()
    client.timeline.return_value = {"items": events}
    result = fetch_timeline_events(client, since=SINCE)
    assert result == events


def test_fetch_timeline_returns_dict_with_data():
    events = [{"id": "2"}]
    client = MagicMock()
    client.timeline.return_value = {"data": events}
    result = fetch_timeline_events(client, since=SINCE)
    assert result == events


def test_fetch_timeline_returns_empty_when_none():
    client = MagicMock()
    client.timeline.return_value = None
    result = fetch_timeline_events(client, since=SINCE)
    assert result == []


def test_fetch_timeline_filters_non_dict_items():
    client = MagicMock()
    client.timeline.return_value = [{"id": "ok"}, "not-a-dict", 42, None]
    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "ok"}]


def test_fetch_timeline_falls_back_to_get_timeline():
    client = MagicMock(spec=["get_timeline"])
    client.get_timeline.return_value = [{"id": "fallback"}]
    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "fallback"}]


def test_fetch_timeline_raises_when_no_method_found():
    client = MagicMock(spec=[])  # no methods
    with pytest.raises(RuntimeError, match="No supported timeline method"):
        fetch_timeline_events(client, since=SINCE)


def test_fetch_timeline_skips_type_error_and_tries_next():
    """If a method raises TypeError, the next provider is tried."""
    client = MagicMock()
    client.timeline.side_effect = TypeError("bad args")
    client.get_timeline.return_value = [{"id": "second"}]
    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "second"}]


def test_fetch_timeline_unknown_return_type_returns_empty():
    client = MagicMock()
    client.timeline.return_value = "unexpected string"
    result = fetch_timeline_events(client, since=SINCE)
    assert result == []


def test_fetch_timeline_handles_async_coroutine():
    """If timeline returns a coroutine, run_blocking is used to resolve it."""
    import asyncio

    async def _coro():
        return [{"id": "async-event"}]

    client = MagicMock()
    client.timeline.return_value = _coro()
    client.run_blocking.side_effect = lambda coro: asyncio.run(coro)

    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "async-event"}]
    client.run_blocking.assert_called_once()
