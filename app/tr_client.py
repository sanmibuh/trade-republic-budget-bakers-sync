from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)


def connect_trade_republic(
    phone_number: str,
    pin: str,
    data_dir: Path,
    on_login_required: Callable[[], None] | None = None,
    on_login_success: Callable[[], None] | None = None,
) -> Any:
    from pytr.api import TradeRepublicApi

    client = TradeRepublicApi(
        phone_no=phone_number,
        pin=pin,
        save_cookies=True,
        credentials_file=str(data_dir / "credentials.json"),
        cookies_file=str(data_dir / "cookies.txt"),
        use_v2_login=True,
    )

    # Try to reuse an existing session first.
    if client.resume_websession():
        log.info("Resumed existing Trade Republic session")
        return client

    log.info("No saved session found, starting interactive login")

    # No saved session — notify and run the interactive login flow.
    if on_login_required:
        on_login_required()

    try:
        client.initiate_weblogin()

        if client.weblogin_needs_authenticator:
            print("Enter the code from your authenticator app: ", end="", flush=True)
            code = input().strip()
            log.debug("Submitting authenticator code")
            client.complete_weblogin(verify_code=code)
            # complete_weblogin() for authenticator verifies the code but never
            # polls for CONFIRMED status on the login process. That final GET
            # /processes/{id} response is what transitions the session from
            # "pending" to fully active (and may set additional cookies).
            # _await_weblogin_confirmation() returns immediately if already
            # CONFIRMED, but still makes the request that establishes the session.
            log.debug("Polling login process for CONFIRMED status")
            client._await_weblogin_confirmation()
            client.save_websession()
        else:
            log.info("Waiting for push notification approval in Trade Republic app...")
            print("Waiting for you to approve the login in the Trade Republic app...")
            client.complete_weblogin()

        log.info("Login completed, session saved")
        # complete_weblogin() already calls save_websession() internally.
    except LoginFailedError:
        raise
    except Exception as exc:
        log.exception("Login failed with exception: %s", exc)
        raise LoginFailedError(f"2FA login failed: {exc}") from exc

    if on_login_success:
        on_login_success()

    return client


class LoginFailedError(Exception):
    """Raised when the interactive Trade Republic 2FA login fails."""


def fetch_timeline_events(client: Any, since: datetime) -> list[dict[str, Any]]:
    # Before opening a websocket subscription, the web session token must be
    # present in the cookie jar. pytr's _web_request calls
    # GET /api/v1/auth/web/session on its first HTTP request and that endpoint
    # sets (or refreshes) the auth token cookie.  Without it the websocket
    # server replies with AUTHENTICATION_ERROR: No auth token.
    log.debug("Calling settings() to prime the web session token")
    try:
        client.settings()
        log.debug("settings() succeeded — web session token is ready")
    except Exception as exc:
        log.warning("settings() failed before websocket subscription: %s", exc)

    # The web login connection (connect_id=31) does not support the `timeline`
    # subscription type — it requires `timelineTransactions` or
    # `timelineActivityLog`. Try both in order.
    candidates = ["timeline_transactions", "timeline_activity_log"]

    for method_name in candidates:
        method = getattr(client, method_name, None)
        if method is None:
            log.debug("Method %s not found on client, skipping", method_name)
            continue
        log.debug("Fetching timeline via %s", method_name)
        try:
            result = _resolve(client, method())
            events = _parse_result(result)
            log.debug("Got %d raw events from %s", len(events), method_name)
            return events
        except Exception as exc:
            log.warning("Method %s failed: %s — client event loop may be tainted, stopping", method_name, exc)
            # After any asyncio.run() failure the client's internal lock is
            # attached to the now-closed loop. Stop trying rather than retrying
            # with the same client.
            raise RuntimeError(f"Timeline fetch via {method_name} failed: {exc}") from exc

    raise RuntimeError("No supported timeline method worked for the current pytr connection")


def _resolve(client: Any, coroutine_or_result: Any) -> Any:
    """Run a coroutine via run_blocking if needed, otherwise return as-is."""
    run_blocking = getattr(client, "run_blocking", None)
    if run_blocking is not None and hasattr(coroutine_or_result, "__await__"):
        return run_blocking(coroutine_or_result, timeout=30.0)
    return coroutine_or_result


def _parse_result(result: Any) -> list[dict[str, Any]]:
    """Normalise a pytr timeline response into a flat list of event dicts."""
    if result is None:
        return []
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        items = result.get("items") or result.get("data") or []
        return [item for item in items if isinstance(item, dict)]
    return []
