"""Shared HTTP utilities with an SSL circuit-breaker.

Both `notifier` and `wallet_client` may run in environments where the
certificate chain is broken (e.g. corporate VPN on a developer machine).
Rather than disabling SSL verification globally from the start, the
circuit-breaker attempts the first request with ``verify=True``. On the
first ``SSLError`` it permanently flips to ``verify=False`` for the
process lifetime and logs a one-time warning.

Public API
----------
- ``http_post(url, **kwargs)`` — stateless POST wrapper.
- ``build_session(headers)`` — returns a ``requests.Session`` whose
  ``verify`` flag tracks the circuit-breaker and retries on ``SSLError``.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)

# Circuit-breaker state: True = verify certificates (default).
# Flipped to False permanently on the first SSLError encountered by any
# http_post or Session built by build_session.
_ssl_verify: bool = True


def _open_circuit() -> None:
    """Flip the circuit-breaker and emit the one-time warning."""
    global _ssl_verify
    if _ssl_verify:
        log.warning(
            "SSL certificate verification failed — disabling validation for the "
            "remainder of this process. Check your network/proxy if unexpected."
        )
        _ssl_verify = False


def http_post(url: str, **kwargs: Any) -> requests.Response:
    """POST *url* with SSL circuit-breaker fallback.

    On the first ``SSLError`` with ``verify=True`` the circuit is tripped,
    ``_ssl_verify`` is set to ``False``, and the request is retried once.
    Subsequent calls skip the first attempt entirely.
    """
    try:
        return requests.post(url, verify=_ssl_verify, **kwargs)
    except requests.exceptions.SSLError:
        _open_circuit()
        return requests.post(url, verify=False, **kwargs)


class _SSLCircuitBreakerAdapter(requests.adapters.HTTPAdapter):
    """Transport adapter that applies the SSL circuit-breaker to every request."""

    def send(self, request, **kwargs):  # type: ignore[override]
        kwargs.setdefault("verify", _ssl_verify)
        try:
            return super().send(request, **kwargs)
        except requests.exceptions.SSLError:
            _open_circuit()
            kwargs["verify"] = False
            return super().send(request, **kwargs)


def build_session(headers: dict[str, str] | None = None) -> requests.Session:
    """Return a ``requests.Session`` with SSL circuit-breaker behaviour.

    The session's ``verify`` attribute is initialised from ``_ssl_verify``
    at construction time. The custom adapter handles the per-request
    fallback and flag update.
    """
    session = requests.Session()
    session.verify = _ssl_verify
    adapter = _SSLCircuitBreakerAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if headers:
        session.headers.update(headers)
    return session
