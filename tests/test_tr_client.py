from __future__ import annotations

from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.tr_client import LoginFailedError, SessionExpiredError, TRClient


def _make_pytr_client(
    *, needs_authenticator: bool = True, resume: bool = False
) -> MagicMock:
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
    code_provider = MagicMock()
    code_provider.get_code.return_value = "123456"
    with patch("pytr.api.TradeRepublicApi", return_value=pytr):
        client = _make_tr_client(tmp_path)
        client.connect(code_provider=code_provider)

    pytr.initiate_weblogin.assert_called_once()
    pytr.complete_weblogin.assert_called_once_with(verify_code="123456")
    pytr._await_weblogin_confirmation.assert_called_once()
    pytr.save_websession.assert_called_once()
    assert client._api is pytr


def test_connect_authenticator_calls_on_login_required(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    code_provider = MagicMock()
    code_provider.get_code.return_value = "123456"
    on_login_required = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=pytr):
        _make_tr_client(tmp_path).connect(
            on_login_required=on_login_required, code_provider=code_provider
        )
    on_login_required.assert_called_once()


def test_connect_authenticator_calls_on_login_success(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    code_provider = MagicMock()
    code_provider.get_code.return_value = "123456"
    on_login_success = MagicMock()
    with patch("pytr.api.TradeRepublicApi", return_value=pytr):
        _make_tr_client(tmp_path).connect(
            on_login_success=on_login_success, code_provider=code_provider
        )
    on_login_success.assert_called_once()


def test_connect_authenticator_raises_login_failed_on_bad_code(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    pytr.complete_weblogin.side_effect = Exception("VALIDATION_CODE_INVALID")
    code_provider = MagicMock()
    code_provider.get_code.return_value = "000000"
    tr_client = _make_tr_client(tmp_path)
    with (
        patch("pytr.api.TradeRepublicApi", return_value=pytr),
        pytest.raises(LoginFailedError),
    ):
        tr_client.connect(code_provider=code_provider)


def test_connect_authenticator_no_success_callback_on_failure(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    pytr.complete_weblogin.side_effect = Exception("401")
    code_provider = MagicMock()
    code_provider.get_code.return_value = "000000"
    on_login_success = MagicMock()
    tr_client = _make_tr_client(tmp_path)
    with (
        patch("pytr.api.TradeRepublicApi", return_value=pytr),
        pytest.raises(LoginFailedError),
    ):
        tr_client.connect(
            on_login_success=on_login_success, code_provider=code_provider
        )
    on_login_success.assert_not_called()


# ---------------------------------------------------------------------------
# TRClient.connect — no code provider (non-interactive) bails out cleanly
# ---------------------------------------------------------------------------


def test_connect_authenticator_no_provider_raises_session_expired(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    tr_client = _make_tr_client(tmp_path)
    with (
        patch("pytr.api.TradeRepublicApi", return_value=pytr),
        pytest.raises(SessionExpiredError),
    ):
        tr_client.connect(code_provider=None)

    pytr.complete_weblogin.assert_not_called()
    pytr._await_weblogin_confirmation.assert_not_called()
    pytr.save_websession.assert_not_called()


def test_connect_authenticator_no_provider_no_success_callback(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    on_login_success = MagicMock()
    tr_client = _make_tr_client(tmp_path)
    with (
        patch("pytr.api.TradeRepublicApi", return_value=pytr),
        pytest.raises(SessionExpiredError),
    ):
        tr_client.connect(on_login_success=on_login_success, code_provider=None)
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
    pytr.save_websession.assert_called_once()
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
    tr_client = _make_tr_client(tmp_path)
    with pytest.raises(RuntimeError, match="connect\\(\\)"):
        tr_client.fetch_timeline_events()


# ---------------------------------------------------------------------------
# TRClient.fetch_timeline_events — Timeline-based fetch
# ---------------------------------------------------------------------------


class _FakeTimeline:
    """Test double for pytr.timeline.Timeline.

    Calls ``event_callback`` with each event in ``events`` when ``tl_loop``
    is awaited, then returns.
    """

    def __init__(
        self,
        *,
        tr,
        output_path,
        not_before,
        store_event_database,
        event_callback,
        events=None,
    ):
        self.tr = tr
        self.output_path = output_path
        self.not_before = not_before
        self.store_event_database = store_event_database
        self.event_callback = event_callback
        self._events = events or []

    async def tl_loop(self):
        for event in self._events:
            self.event_callback(event)


def _patch_timeline(events=None, side_effect=None):
    """Return a patch context manager for pytr.timeline.Timeline."""

    def _factory(**kwargs):
        fake = _FakeTimeline(events=events, **kwargs)
        if side_effect is not None:

            async def _raise():
                raise side_effect

            fake.tl_loop = _raise
        return fake

    return patch("pytr.timeline.Timeline", side_effect=_factory)


def test_fetch_returns_events_from_timeline(tmp_path):
    events = [{"id": "e1"}, {"id": "e2"}]
    client = _make_tr_client(tmp_path)
    client._api = MagicMock()
    with _patch_timeline(events=events):
        result = client.fetch_timeline_events()
    assert result == events


def test_fetch_returns_empty_when_no_events(tmp_path):
    client = _make_tr_client(tmp_path)
    client._api = MagicMock()
    with _patch_timeline(events=[]):
        result = client.fetch_timeline_events()
    assert result == []


def test_fetch_passes_zero_not_before_when_since_is_none(tmp_path):
    captured = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeTimeline(**kwargs)

    client = _make_tr_client(tmp_path)
    client._api = MagicMock()
    with patch("pytr.timeline.Timeline", side_effect=_factory):
        client.fetch_timeline_events(since=None)

    assert captured["not_before"] == 0.0


def test_fetch_passes_since_as_not_before(tmp_path):
    from datetime import datetime

    since = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
    captured = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeTimeline(**kwargs)

    client = _make_tr_client(tmp_path)
    client._api = MagicMock()
    with patch("pytr.timeline.Timeline", side_effect=_factory):
        client.fetch_timeline_events(since=since)

    assert captured["not_before"] == since.timestamp()


def test_fetch_passes_store_event_database_false(tmp_path):
    captured = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeTimeline(**kwargs)

    client = _make_tr_client(tmp_path)
    client._api = MagicMock()
    with patch("pytr.timeline.Timeline", side_effect=_factory):
        client.fetch_timeline_events()

    assert captured["store_event_database"] is False


def test_fetch_passes_data_dir_as_output_path(tmp_path):
    captured = {}

    def _factory(**kwargs):
        captured.update(kwargs)
        return _FakeTimeline(**kwargs)

    client = _make_tr_client(tmp_path)
    client._api = MagicMock()
    with patch("pytr.timeline.Timeline", side_effect=_factory):
        client.fetch_timeline_events()

    assert captured["output_path"] == tmp_path


def test_fetch_raises_runtime_error_on_timeline_exception(tmp_path):
    client = _make_tr_client(tmp_path)
    client._api = MagicMock()
    with _patch_timeline(side_effect=Exception("websocket failed")):  # noqa: SIM117 — nested with required by S5778
        with pytest.raises(RuntimeError, match="Timeline fetch failed"):
            client.fetch_timeline_events()


# ---------------------------------------------------------------------------
# TRClient._create_api_client
# ---------------------------------------------------------------------------


def test_create_api_client_uses_correct_credentials(tmp_path):
    pytr = _make_pytr_client(resume=True)
    with patch("pytr.api.TradeRepublicApi", return_value=pytr) as mock_api:
        client = _make_tr_client(tmp_path)
        result = client._create_api_client()

    assert result is pytr
    kw = mock_api.call_args.kwargs
    assert kw["phone_no"] == "+34000000000"
    assert kw["pin"] == "1234"
    assert kw["save_cookies"] is True
    assert kw["credentials_file"] == str(tmp_path / "credentials.json")
    assert kw["cookies_file"] == str(tmp_path / "cookies.txt")
    assert kw["use_v2_login"] is True


# ---------------------------------------------------------------------------
# TRClient._do_authenticator_login
# ---------------------------------------------------------------------------


def test_do_authenticator_login_raises_session_expired_when_no_provider(tmp_path):
    pytr = _make_pytr_client()
    client = _make_tr_client(tmp_path)
    with pytest.raises(SessionExpiredError):
        client._do_authenticator_login(pytr, code_provider=None)
    pytr.complete_weblogin.assert_not_called()
    pytr.save_websession.assert_not_called()


def test_do_authenticator_login_completes_with_provider(tmp_path):
    pytr = _make_pytr_client()
    code_provider = MagicMock()
    code_provider.get_code.return_value = "654321"
    client = _make_tr_client(tmp_path)
    client._do_authenticator_login(pytr, code_provider=code_provider)
    pytr.complete_weblogin.assert_called_once_with(verify_code="654321")
    pytr._await_weblogin_confirmation.assert_called_once()
    pytr.save_websession.assert_called_once()


# ---------------------------------------------------------------------------
# TRClient._do_push_notification_login
# ---------------------------------------------------------------------------


def test_do_push_notification_login_completes(tmp_path):
    pytr = _make_pytr_client()
    client = _make_tr_client(tmp_path)
    client._do_push_notification_login(pytr)
    pytr.complete_weblogin.assert_called_once_with()
    pytr.save_websession.assert_called_once()


# ---------------------------------------------------------------------------
# TRClient._perform_weblogin
# ---------------------------------------------------------------------------


def test_perform_weblogin_uses_authenticator_when_needed(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    code_provider = MagicMock()
    code_provider.get_code.return_value = "111111"
    client = _make_tr_client(tmp_path)
    client._perform_weblogin(pytr, code_provider=code_provider)
    pytr.complete_weblogin.assert_called_once_with(verify_code="111111")


def test_perform_weblogin_uses_push_when_no_authenticator(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=False)
    client = _make_tr_client(tmp_path)
    client._perform_weblogin(pytr, code_provider=None)
    pytr.complete_weblogin.assert_called_once_with()


def test_perform_weblogin_wraps_generic_exception_as_login_failed(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=False)
    pytr.complete_weblogin.side_effect = Exception("network error")
    client = _make_tr_client(tmp_path)
    with pytest.raises(LoginFailedError, match="2FA login failed"):
        client._perform_weblogin(pytr, code_provider=None)


def test_perform_weblogin_propagates_session_expired_unwrapped(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=True)
    client = _make_tr_client(tmp_path)
    with pytest.raises(SessionExpiredError):
        client._perform_weblogin(pytr, code_provider=None)


def test_perform_weblogin_propagates_login_failed_unwrapped(tmp_path):
    pytr = _make_pytr_client(needs_authenticator=False)
    pytr.initiate_weblogin.side_effect = LoginFailedError("already failed")
    client = _make_tr_client(tmp_path)
    with pytest.raises(LoginFailedError, match="already failed"):
        client._perform_weblogin(pytr, code_provider=None)
