from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
import requests

import app.http_client as http_client_module
from app.http_client import SSLCircuitBreaker, build_session, configure, http_post

# ---------------------------------------------------------------------------
# SSLCircuitBreaker — unit tests (isolated instances, no shared state)
# ---------------------------------------------------------------------------


def test_circuit_breaker_verify_true_by_default():
    cb = SSLCircuitBreaker()
    assert cb.verify is True


def test_circuit_breaker_allow_insecure_false_by_default():
    cb = SSLCircuitBreaker()
    assert cb.allow_insecure is False


def test_circuit_breaker_configure_sets_allow_insecure():
    cb = SSLCircuitBreaker()
    cb.configure(allow_insecure_ssl=True)
    assert cb.allow_insecure is True


def test_circuit_breaker_configure_resets_verify_to_true():
    cb = SSLCircuitBreaker()
    cb.configure(allow_insecure_ssl=True)
    cb.open()
    assert cb.verify is False
    cb.configure(allow_insecure_ssl=True)
    assert cb.verify is True


def test_circuit_breaker_open_flips_verify_to_false():
    cb = SSLCircuitBreaker()
    cb.configure(allow_insecure_ssl=True)
    cb.open()
    assert cb.verify is False


def test_circuit_breaker_open_logs_warning_once(caplog):
    cb = SSLCircuitBreaker()
    cb.configure(allow_insecure_ssl=True)
    with caplog.at_level(logging.WARNING, logger="app.http_client"):
        cb.open()
        cb.open()  # second call must NOT log again
    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "SSL" in warnings[0]


def test_circuit_breaker_open_is_idempotent():
    cb = SSLCircuitBreaker()
    cb.configure(allow_insecure_ssl=True)
    cb.open()
    cb.open()
    assert cb.verify is False


# ---------------------------------------------------------------------------
# http_post — basic behaviour
# ---------------------------------------------------------------------------


def test_http_post_uses_ssl_verify_true_by_default():
    configure(allow_insecure_ssl=False)
    mock_response = MagicMock()

    with patch(
        "app.http_client.requests.post", return_value=mock_response
    ) as mock_post:
        http_post("https://example.com", json={"a": 1})

    assert mock_post.call_args.kwargs["verify"] is True


def test_http_post_returns_response_on_success():
    configure(allow_insecure_ssl=False)
    mock_response = MagicMock()

    with patch("app.http_client.requests.post", return_value=mock_response):
        result = http_post("https://example.com", json={})

    assert result is mock_response


def test_http_post_passes_kwargs_through():
    configure(allow_insecure_ssl=False)
    mock_response = MagicMock()

    with patch(
        "app.http_client.requests.post", return_value=mock_response
    ) as mock_post:
        http_post("https://example.com", json={"x": 1}, timeout=42)

    assert mock_post.call_args.kwargs["timeout"] == 42
    assert mock_post.call_args.kwargs["json"] == {"x": 1}


# ---------------------------------------------------------------------------
# http_post — SSL circuit breaker
# ---------------------------------------------------------------------------


def test_http_post_falls_back_to_no_verify_on_ssl_error():
    configure(allow_insecure_ssl=True)
    mock_response = MagicMock()
    call_verifies = []

    def fake_post(url, *, verify, **kwargs):
        call_verifies.append(verify)
        if verify is True:
            raise requests.exceptions.SSLError("cert verify failed")
        return mock_response

    with patch("app.http_client.requests.post", side_effect=fake_post):
        result = http_post("https://example.com", json={})

    assert result is mock_response
    assert call_verifies == [True, False]
    assert http_client_module.breaker.verify is False


def test_http_post_ssl_flag_stays_false_after_circuit_break():
    configure(allow_insecure_ssl=True)
    http_client_module.breaker.open()  # simulate previously tripped circuit
    mock_response = MagicMock()

    with patch(
        "app.http_client.requests.post", return_value=mock_response
    ) as mock_post:
        http_post("https://example.com", json={})

    assert mock_post.call_count == 1
    assert mock_post.call_args.kwargs["verify"] is False


def test_http_post_ssl_warning_logged_once(caplog):
    configure(allow_insecure_ssl=True)
    mock_response = MagicMock()

    def fake_post(url, *, verify, **kwargs):
        if verify is True:
            raise requests.exceptions.SSLError("cert verify failed")
        return mock_response

    with (
        caplog.at_level(logging.WARNING, logger="app.http_client"),
        patch("app.http_client.requests.post", side_effect=fake_post),
    ):
        http_post("https://example.com", json={})

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_msgs) == 1
    assert "SSL" in warning_msgs[0]


def test_http_post_ssl_error_on_fallback_reraises():
    """If the fallback call (verify=False) also raises SSLError, it propagates."""
    configure(allow_insecure_ssl=True)

    with (
        patch(
            "app.http_client.requests.post",
            side_effect=requests.exceptions.SSLError("still fails"),
        ),
        pytest.raises(requests.exceptions.SSLError),
    ):
        http_post("https://example.com", json={})


# ---------------------------------------------------------------------------
# build_session — basic behaviour
# ---------------------------------------------------------------------------


def test_build_session_returns_session_with_verify_true_by_default():
    configure(allow_insecure_ssl=False)
    session = build_session()
    assert session.verify is True


def test_build_session_respects_circuit_breaker_state():
    configure(allow_insecure_ssl=True)
    http_client_module.breaker.open()  # simulate tripped circuit
    session = build_session()
    assert session.verify is False


def test_build_session_applies_extra_headers():
    configure(allow_insecure_ssl=False)
    session = build_session(headers={"Authorization": "Bearer tok"})
    assert session.headers["Authorization"] == "Bearer tok"


def test_build_session_ssl_circuit_breaker_on_request(monkeypatch):
    """When a Session request raises SSLError, verify is flipped and request retried."""
    configure(allow_insecure_ssl=True)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.url = "https://example.com"
    mock_response.history = []
    mock_response.is_redirect = False
    mock_response.headers = {}

    import requests.adapters

    call_count = [0]

    def fake_base_send(self_adapter, request, **kwargs):
        call_count[0] += 1
        verify = kwargs.get("verify")
        if verify is not False:
            raise requests.exceptions.SSLError("cert verify failed")
        return mock_response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_base_send)

    session = build_session()
    result = session.get("https://example.com")

    assert result is mock_response
    assert call_count[0] == 2
    assert http_client_module.breaker.verify is False


def test_adapter_uses_current_circuit_state_not_session_verify(monkeypatch):
    """Adapter must use breaker.verify, not session.verify, so tripped circuit is honoured
    even on sessions that were built before the circuit opened."""
    configure(allow_insecure_ssl=True)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.url = "https://example.com"
    mock_response.history = []
    mock_response.is_redirect = False
    mock_response.headers = {}

    import requests.adapters

    verify_values: list = []

    def fake_base_send(self_adapter, request, **kwargs):
        verify_values.append(kwargs.get("verify"))
        if verify_values[-1] is not False:
            raise requests.exceptions.SSLError("cert verify failed")
        return mock_response

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_base_send)

    # Build session while circuit is closed (verify=True)
    session = build_session()
    assert session.verify is True

    # Trip the circuit (simulates another request having already opened it)
    http_client_module.breaker.open()

    # This request should use verify=False from the breaker, NOT verify=True from session
    result = session.get("https://example.com")

    assert result is mock_response
    assert verify_values == [False], f"Expected [False], got {verify_values}"


# ---------------------------------------------------------------------------
# configure()
# ---------------------------------------------------------------------------


def test_configure_sets_allow_insecure_ssl_true():
    configure(allow_insecure_ssl=True)
    assert http_client_module.breaker.allow_insecure is True


def test_configure_sets_allow_insecure_ssl_false():
    configure(allow_insecure_ssl=True)
    configure(allow_insecure_ssl=False)
    assert http_client_module.breaker.allow_insecure is False


def test_configure_resets_ssl_verify_flag():
    """configure() resets the circuit-breaker so a previously tripped flag is cleared."""
    configure(allow_insecure_ssl=True)
    http_client_module.breaker.open()
    assert http_client_module.breaker.verify is False
    configure(allow_insecure_ssl=True)
    assert http_client_module.breaker.verify is True


# ---------------------------------------------------------------------------
# http_post — circuit breaker blocked when allow_insecure_ssl=False
# ---------------------------------------------------------------------------


def test_http_post_ssl_error_propagates_when_insecure_not_allowed():
    """With allow_insecure_ssl=False (default), SSLError must propagate — no fallback."""
    configure(allow_insecure_ssl=False)

    with (
        patch(
            "app.http_client.requests.post",
            side_effect=requests.exceptions.SSLError("cert failed"),
        ),
        pytest.raises(requests.exceptions.SSLError),
    ):
        http_post("https://example.com", json={})

    assert http_client_module.breaker.verify is True


def test_http_post_ssl_fallback_only_when_allowed():
    """With allow_insecure_ssl=True, SSLError triggers the fallback."""
    configure(allow_insecure_ssl=True)

    mock_response = MagicMock()
    calls = []

    def fake_post(url, *, verify, **kwargs):
        calls.append(verify)
        if verify is not False:
            raise requests.exceptions.SSLError("cert failed")
        return mock_response

    with patch("app.http_client.requests.post", side_effect=fake_post):
        result = http_post("https://example.com", json={})

    assert result is mock_response
    assert calls == [True, False]
    assert http_client_module.breaker.verify is False


def test_build_session_ssl_error_propagates_when_insecure_not_allowed(monkeypatch):
    """SSLError in Session adapter must propagate when allow_insecure_ssl=False."""
    configure(allow_insecure_ssl=False)

    import requests.adapters

    def fake_base_send(self_adapter, request, **kwargs):
        raise requests.exceptions.SSLError("cert verify failed")

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_base_send)

    session = build_session()
    with pytest.raises(requests.exceptions.SSLError):
        session.get("https://example.com")

    assert http_client_module.breaker.verify is True
