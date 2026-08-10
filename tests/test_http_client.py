from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

import app.http_client as http_client_module
from app.http_client import build_session, configure, http_post

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_state():
    """Restore module-level flags to initial state."""
    http_client_module._ssl_verify = True
    http_client_module._allow_insecure_ssl = False


# ---------------------------------------------------------------------------
# http_post — basic behaviour
# ---------------------------------------------------------------------------

def test_http_post_uses_ssl_verify_true_by_default():
    _reset_state()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()

    with patch("app.http_client.requests.post", return_value=mock_response) as mock_post:
        http_post("https://example.com", json={"a": 1})

    assert mock_post.call_args.kwargs["verify"] is True


def test_http_post_returns_response_on_success():
    _reset_state()
    mock_response = MagicMock()

    with patch("app.http_client.requests.post", return_value=mock_response):
        result = http_post("https://example.com", json={})

    assert result is mock_response


def test_http_post_passes_kwargs_through():
    _reset_state()
    mock_response = MagicMock()

    with patch("app.http_client.requests.post", return_value=mock_response) as mock_post:
        http_post("https://example.com", json={"x": 1}, timeout=42)

    assert mock_post.call_args.kwargs["timeout"] == 42
    assert mock_post.call_args.kwargs["json"] == {"x": 1}


# ---------------------------------------------------------------------------
# http_post — SSL circuit breaker
# ---------------------------------------------------------------------------

def test_http_post_falls_back_to_no_verify_on_ssl_error():
    _reset_state()
    http_client_module._allow_insecure_ssl = True
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
    assert http_client_module._ssl_verify is False


def test_http_post_ssl_flag_stays_false_after_circuit_break():
    _reset_state()
    http_client_module._ssl_verify = False

    mock_response = MagicMock()

    with patch("app.http_client.requests.post", return_value=mock_response) as mock_post:
        http_post("https://example.com", json={})

    assert mock_post.call_count == 1
    assert mock_post.call_args.kwargs["verify"] is False


def test_http_post_ssl_warning_logged_once(caplog):
    _reset_state()
    http_client_module._allow_insecure_ssl = True
    mock_response = MagicMock()

    def fake_post(url, *, verify, **kwargs):
        if verify is True:
            raise requests.exceptions.SSLError("cert verify failed")
        return mock_response

    import logging
    with caplog.at_level(logging.WARNING, logger="app.http_client"), patch("app.http_client.requests.post", side_effect=fake_post):
        http_post("https://example.com", json={})

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_msgs) == 1
    assert "SSL" in warning_msgs[0]


def test_http_post_ssl_error_on_fallback_reraises():
    """If the fallback call (verify=False) also raises SSLError, it propagates."""
    _reset_state()
    http_client_module._allow_insecure_ssl = True

    with patch("app.http_client.requests.post", side_effect=requests.exceptions.SSLError("still fails")), pytest.raises(requests.exceptions.SSLError):
        http_post("https://example.com", json={})


# ---------------------------------------------------------------------------
# build_session — basic behaviour
# ---------------------------------------------------------------------------

def test_build_session_returns_session_with_verify_true_by_default():
    _reset_state()
    session = build_session()
    assert session.verify is True


def test_build_session_respects_circuit_breaker_state():
    http_client_module._ssl_verify = False
    session = build_session()
    assert session.verify is False
    _reset_state()


def test_build_session_applies_extra_headers():
    _reset_state()
    session = build_session(headers={"Authorization": "Bearer tok"})
    assert session.headers["Authorization"] == "Bearer tok"


def test_build_session_ssl_circuit_breaker_on_request(monkeypatch):
    """When a Session request raises SSLError, verify is flipped and request retried."""
    _reset_state()
    http_client_module._allow_insecure_ssl = True

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
    assert call_count[0] == 2  # first attempt (truthy verify) + retry (verify=False)
    assert http_client_module._ssl_verify is False


# ---------------------------------------------------------------------------
# configure()
# ---------------------------------------------------------------------------

def test_configure_sets_allow_insecure_ssl_true():
    _reset_state()
    configure(allow_insecure_ssl=True)
    assert http_client_module._allow_insecure_ssl is True
    _reset_state()


def test_configure_sets_allow_insecure_ssl_false():
    http_client_module._allow_insecure_ssl = True
    configure(allow_insecure_ssl=False)
    assert http_client_module._allow_insecure_ssl is False
    _reset_state()


def test_configure_resets_ssl_verify_flag():
    """configure() resets the circuit-breaker so a previously tripped flag is cleared."""
    http_client_module._ssl_verify = False  # simulates a previously tripped circuit
    configure(allow_insecure_ssl=True)
    assert http_client_module._ssl_verify is True
    _reset_state()


# ---------------------------------------------------------------------------
# http_post — circuit breaker blocked when allow_insecure_ssl=False
# ---------------------------------------------------------------------------

def test_http_post_ssl_error_propagates_when_insecure_not_allowed():
    """With allow_insecure_ssl=False (default), SSLError must propagate — no fallback."""
    _reset_state()

    with patch("app.http_client.requests.post", side_effect=requests.exceptions.SSLError("cert failed")), pytest.raises(requests.exceptions.SSLError):
        http_post("https://example.com", json={})

    # Circuit must NOT have opened
    assert http_client_module._ssl_verify is True


def test_http_post_ssl_fallback_only_when_allowed():
    """With allow_insecure_ssl=True, SSLError triggers the fallback."""
    _reset_state()
    http_client_module._allow_insecure_ssl = True

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
    assert http_client_module._ssl_verify is False
    _reset_state()
