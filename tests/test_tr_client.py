from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.tr_client import TRClient, LoginFailedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SINCE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_pytr_client(*, needs_authenticator: bool = True, resume: bool = False) -> MagicMock:
    client = MagicMock()
    client.resume_websession.return_value = resume
    client.weblogin_needs_authenticator = needs_authenticator
    return client


def _make_tr_client(tmp_path: Path) -> TRClient:
    return TRClient("+34000000000", "1234", tmp_path)


# ---------------------------------------------------------------------------
# TRClient.connect — session resume
# ---------------------------------------------------------------------------

def test_connect_resumes_existing_session(tmp_path):
    pytr = _make_pytr_client(resume=True)
    with patch("pytr.api.TradeRepublicApi", return_value=pytr):
        client = _make_tr_client(tmp_path)
        client.connect()

    pytr.resume_websession.assert_called_once()
    pytr.initiate_weblogin.assert_not_called()
    assert client._api is pytr


def test_connect_does_not_notify_when_session_resumes(tmp_path):
    pytr = _make_pytr_client(resume=True)
    on_login_required = MagicMock()
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=pytr):
        _make_tr_client(tmp_path).connect(
            on_login_required=on_login_required,
            on_login_success=on_login_success,
        )
    on_login_required.assert_not_called()
    on_login_success.assert_not_called()


# ---------------------------------------------------------------------------
# TRClient.connect — authenticator flow
# ---------------------------------------------------------------------------

def test_connect_authenticator_flow(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    with patch("pytr.api.TradeRepublicApi", return_value=pytr), \
         patch("builtins.input", return_value="123456"):
        client = _make_tr_client(tmp_path)
        client.connect()

    pytr.initiate_weblogin.assert_called_once()
    pytr.complete_weblogin.assert_called_once_with(verify_code="123456")
    pytr._await_weblogin_confirmation.assert_called_once()
    pytr.save_websession.assert_called_once()
    assert client._api is pytr


def test_connect_authenticator_calls_on_login_required(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    on_login_required = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=pytr), \
         patch("builtins.input", return_value="123456"):
        _make_tr_client(tmp_path).connect(on_login_required=on_login_required)
    on_login_required.assert_called_once()


def test_connect_authenticator_calls_on_login_success(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=pytr), \
         patch("builtins.input", return_value="123456"):
        _make_tr_client(tmp_path).connect(on_login_success=on_login_success)
    on_login_success.assert_called_once()


def test_connect_authenticator_raises_login_failed_on_bad_code(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    pytr.complete_weblogin.side_effect = Exception("VALIDATION_CODE_INVALID")
    with patch("pytr.api.TradeRepublicApi", return_value=pytr), \
         patch("builtins.input", return_value="000000"):
        with pytest.raises(LoginFailedError):
            _make_tr_client(tmp_path).connect()


def test_connect_authenticator_no_success_callback_on_failure(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    pytr.complete_weblogin.side_effect = Exception("401")
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=pytr), \
         patch("builtins.input", return_value="000000"):
        with pytest.raises(LoginFailedError):
            _make_tr_client(tmp_path).connect(on_login_success=on_login_success)
    on_login_success.assert_not_called()


# ---------------------------------------------------------------------------
# TRClient.connect — app approval flow
# ---------------------------------------------------------------------------

def test_connect_app_approval_flow(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=False)
    with patch("pytr.api.TradeRepublicApi", return_value=pytr):
        client = _make_tr_client(tmp_path)
        client.connect()

    pytr.initiate_weblogin.assert_called_once()
    pytr.complete_weblogin.assert_called_once_with()
    pytr._await_weblogin_confirmation.assert_not_called()
    assert client._api is pytr


# ---------------------------------------------------------------------------
# TRClient.connect — API constructor arguments
# ---------------------------------------------------------------------------

def test_connect_passes_correct_files_to_api(tmp_path):
    pytr = _make_pytr_client(resume=True)
    with patch("pytr.api.TradeRepublicApi", return_value=pytr) as mock_api:
        _make_tr_client(tmp_path).connect()

    kw = mock_api.call_args.kwargs
    assert kw["phone_no"] == "+34000000000"
    assert kw["pin"] == "1234"
    assert kw["credentials_file"] == str(tmp_path / "credentials.json")
    assert kw["cookies_file"] == str(tmp_path / "cookies.txt")
    assert kw["use_v2_login"] is True
    assert kw.get("save_cookies") is True


# ---------------------------------------------------------------------------
# TRClient.fetch_timeline_events — requires connect() first
# ---------------------------------------------------------------------------

def test_fetch_raises_if_not_connected(tmp_path):
    with pytest.raises(RuntimeError, match="connect\\(\\)"):
        _make_tr_client(tmp_path).fetch_timeline_events(SINCE)


# ---------------------------------------------------------------------------
# TRClient.fetch_timeline_events — settings() warm-up
# ---------------------------------------------------------------------------

def test_fetch_calls_settings_before_subscription(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = []
    client = _make_tr_client(tmp_path)
    client._api = pytr
    client.fetch_timeline_events(since=SINCE)
    pytr.settings.assert_called_once()


def test_fetch_continues_if_settings_raises(tmp_path):
    pytr = MagicMock()
    pytr.settings.side_effect = Exception("401")
    pytr.timeline_transactions.return_value = []
    client = _make_tr_client(tmp_path)
    client._api = pytr
    result = client.fetch_timeline_events(since=SINCE)
    assert result == []


# ---------------------------------------------------------------------------
# TRClient.fetch_timeline_events — method selection
# ---------------------------------------------------------------------------

def test_fetch_uses_timeline_transactions(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = [{"id": "1"}]
    client = _make_tr_client(tmp_path)
    client._api = pytr
    result = client.fetch_timeline_events(since=SINCE)
    pytr.timeline_transactions.assert_called_once_with()
    assert result == [{"id": "1"}]


def test_fetch_falls_back_to_timeline_activity_log(tmp_path):
    pytr = MagicMock(spec=["settings", "timeline_activity_log", "run_blocking"])
    pytr.timeline_activity_log.return_value = [{"id": "2"}]
    client = _make_tr_client(tmp_path)
    client._api = pytr
    result = client.fetch_timeline_events(since=SINCE)
    pytr.timeline_activity_log.assert_called_once_with()
    assert result == [{"id": "2"}]


def test_fetch_raises_when_no_method_found(tmp_path):
    pytr = MagicMock(spec=["settings"])
    client = _make_tr_client(tmp_path)
    client._api = pytr
    with pytest.raises(RuntimeError, match="No supported timeline method"):
        client.fetch_timeline_events(since=SINCE)


def test_fetch_raises_on_trade_republic_error(tmp_path):
    from pytr.api import TradeRepublicError
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = MagicMock(__await__=None)
    pytr.run_blocking.side_effect = TradeRepublicError("1", {}, {"errors": []})
    client = _make_tr_client(tmp_path)
    client._api = pytr
    with pytest.raises(RuntimeError):
        client.fetch_timeline_events(since=SINCE)


# ---------------------------------------------------------------------------
# TRClient.fetch_timeline_events — result parsing
# ---------------------------------------------------------------------------

def test_fetch_parses_list_directly(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = [{"id": "1"}, {"id": "2"}]
    client = _make_tr_client(tmp_path)
    client._api = pytr
    assert client.fetch_timeline_events(since=SINCE) == [{"id": "1"}, {"id": "2"}]


def test_fetch_parses_dict_with_items(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = {"items": [{"id": "1"}]}
    client = _make_tr_client(tmp_path)
    client._api = pytr
    assert client.fetch_timeline_events(since=SINCE) == [{"id": "1"}]


def test_fetch_parses_dict_with_data(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = {"data": [{"id": "2"}]}
    client = _make_tr_client(tmp_path)
    client._api = pytr
    assert client.fetch_timeline_events(since=SINCE) == [{"id": "2"}]


def test_fetch_filters_non_dict_items(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = [{"id": "ok"}, "string", 42, None]
    client = _make_tr_client(tmp_path)
    client._api = pytr
    assert client.fetch_timeline_events(since=SINCE) == [{"id": "ok"}]


def test_fetch_returns_empty_on_none(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = None
    client = _make_tr_client(tmp_path)
    client._api = pytr
    assert client.fetch_timeline_events(since=SINCE) == []


def test_fetch_returns_empty_on_unexpected_type(tmp_path):
    pytr = MagicMock()
    pytr.timeline_transactions.return_value = "unexpected"
    client = _make_tr_client(tmp_path)
    client._api = pytr
    assert client.fetch_timeline_events(since=SINCE) == []


def test_fetch_handles_async_coroutine(tmp_path):
    import asyncio

    async def _coro():
        return [{"id": "async-event"}]

    pytr = MagicMock()
    pytr.timeline_transactions.return_value = _coro()
    pytr.run_blocking.side_effect = lambda coro, timeout=5.0: asyncio.run(coro)
    client = _make_tr_client(tmp_path)
    client._api = pytr
    result = client.fetch_timeline_events(since=SINCE)
    assert result == [{"id": "async-event"}]
    pytr.run_blocking.assert_called_once()
