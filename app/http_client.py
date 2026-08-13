"""Shared HTTP utilities with an SSL circuit-breaker.

Both `notifier` and `wallet_client` may run in environments where the
certificate chain is broken (e.g. corporate VPN on a developer machine).
Rather than disabling SSL verification globally from the start, the
circuit-breaker attempts the first request with ``verify=True``. On the
first ``SSLError`` it permanently flips to ``verify=False`` for the
process lifetime and logs a one-time warning.

Public API
----------
- ``SSLCircuitBreaker`` — encapsulates circuit-breaker state and policy.
- ``breaker`` — module-level singleton instance.
- ``configure(allow_insecure_ssl)`` — configure the singleton at startup.
- ``http_post(url, **kwargs)`` — stateless POST wrapper.
- ``build_session(headers)`` — returns a ``requests.Session`` whose
  ``verify`` flag tracks the circuit-breaker and retries on ``SSLError``.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

log = logging.getLogger(__name__)


class SSLCircuitBreaker:
    """Encapsulates the SSL circuit-breaker state and policy.

    Start with ``verify=True`` (closed circuit). Call ``configure()`` once at
    application startup to set the policy. When an ``SSLError`` is caught and
    ``allow_insecure_ssl`` is ``True``, call ``open()`` to permanently disable
    certificate verification for the process lifetime.
    """

    def __init__(self) -> None:
        self._verify: bool = True
        self._allow_insecure: bool = False

    def configure(self, *, allow_insecure_ssl: bool) -> None:
        """Set the insecure-SSL policy and reset any previously tripped state."""
        self._allow_insecure = allow_insecure_ssl
        self._verify = True

    @property
    def verify(self) -> bool:
        """Current circuit state: ``True`` = verify certificates."""
        return self._verify

    @property
    def allow_insecure(self) -> bool:
        """Whether the circuit is allowed to open on an ``SSLError``."""
        return self._allow_insecure

    def open(self) -> None:
        """Trip the breaker and emit a one-time warning.

        Idempotent — subsequent calls after the first trip are no-ops.
        """
        if self._verify:
            log.warning(
                "SSL certificate verification failed — disabling validation for the "
                "remainder of this process. Check your network/proxy if unexpected."
            )
            self._verify = False


# Module-level singleton used by http_post and build_session.
breaker = SSLCircuitBreaker()


def configure(*, allow_insecure_ssl: bool) -> None:
    """Configure the module-level SSL circuit-breaker policy.

    Must be called once at application startup, after reading config.
    Resets any previously tripped circuit-breaker state.
    """
    breaker.configure(allow_insecure_ssl=allow_insecure_ssl)


def http_post(url: str, **kwargs: Any) -> requests.Response:
    """POST *url* with SSL circuit-breaker fallback.

    On the first ``SSLError`` with ``verify=True`` the circuit is tripped and
    the request is retried once with ``verify=False``. Only activates when
    ``allow_insecure_ssl`` is True; otherwise the ``SSLError`` propagates.
    Subsequent calls skip the first attempt entirely when the circuit is open.
    """
    try:
        return requests.post(url, verify=breaker.verify, **kwargs)
    except requests.exceptions.SSLError:
        if not breaker.allow_insecure:
            raise
        breaker.open()
        return requests.post(
            url, verify=False, **kwargs
        )  # NOSONAR — intentional fallback after circuit-breaker trips


class _SSLCircuitBreakerAdapter(requests.adapters.HTTPAdapter):
    """Transport adapter that applies the SSL circuit-breaker to every request."""

    def __init__(self, cb: SSLCircuitBreaker) -> None:
        super().__init__()
        self._cb = cb

    def send(self, request, **kwargs):  # type: ignore[override]
        kwargs["verify"] = self._cb.verify  # always use current circuit state
        try:
            return super().send(request, **kwargs)
        except requests.exceptions.SSLError:
            if not self._cb.allow_insecure:
                raise
            self._cb.open()
            kwargs["verify"] = (
                False  # NOSONAR — intentional fallback after circuit-breaker trips
            )
            return super().send(request, **kwargs)


def build_session(headers: dict[str, str] | None = None) -> requests.Session:
    """Return a ``requests.Session`` with SSL circuit-breaker behaviour.

    The session's ``verify`` attribute is initialised from the current
    circuit state at construction time. The custom adapter handles the
    per-request fallback and state update.
    """
    session = requests.Session()
    session.verify = breaker.verify
    adapter = _SSLCircuitBreakerAdapter(breaker)
    session.mount("https://", adapter)
    if headers:
        session.headers.update(headers)
    return session
