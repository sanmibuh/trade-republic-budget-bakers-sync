from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class LoginFailedError(Exception):
    """Raised when the interactive Trade Republic 2FA login fails."""


class TRClient:
    """Wraps pytr: handles login/session-resume and timeline fetching.

    Usage::

        client = TRClient(phone_number, pin, data_dir)
        client.connect(
            on_login_required=notifier.login_required,
            on_login_success=notifier.login_success,
        )
        events = client.fetch_timeline_events(since=since)
    """

    def __init__(self, phone_number: str, pin: str, data_dir: Path) -> None:
        self._phone_number = phone_number
        self._pin = pin
        self._data_dir = data_dir
        self._api: Any = None

    def connect(
        self,
        on_login_required: Any = None,
        on_login_success: Any = None,
    ) -> None:
        """Establish a Trade Republic session (resume or full 2FA login)."""
        from pytr.api import TradeRepublicApi

        client = TradeRepublicApi(
            phone_no=self._phone_number,
            pin=self._pin,
            save_cookies=True,
            credentials_file=str(self._data_dir / "credentials.json"),
            cookies_file=str(self._data_dir / "cookies.txt"),
            use_v2_login=True,
        )

        if client.resume_websession():
            log.info("Resumed existing Trade Republic session")
            self._api = client
            return

        log.info("No saved session found, starting interactive login")

        if on_login_required:
            on_login_required()

        try:
            client.initiate_weblogin()

            if client.weblogin_needs_authenticator:
                print("Enter the code from your authenticator app: ", end="", flush=True)
                code = input().strip()
                log.debug("Submitting authenticator code")
                client.complete_weblogin(verify_code=code)
                log.debug("Polling login process for CONFIRMED status")
                client._await_weblogin_confirmation()
                client.save_websession()
            else:
                log.info("Waiting for push notification approval in Trade Republic app...")
                print("Waiting for you to approve the login in the Trade Republic app...")
                client.complete_weblogin()

            log.info("Login completed, session saved")
        except LoginFailedError:
            raise
        except Exception as exc:
            log.exception("Login failed with exception: %s", exc)
            raise LoginFailedError(f"2FA login failed: {exc}") from exc

        if on_login_success:
            on_login_success()

        self._api = client

    def fetch_timeline_events(self, since: datetime) -> list[dict[str, Any]]:
        """Fetch all timeline events from Trade Republic."""
        if self._api is None:
            raise RuntimeError("TRClient.connect() must be called before fetch_timeline_events()")

        log.debug("Calling settings() to prime the web session token")
        try:
            self._api.settings()
            log.debug("settings() succeeded — web session token is ready")
        except Exception as exc:
            log.warning("settings() failed before websocket subscription: %s", exc)

        candidates = ["timeline_transactions", "timeline_activity_log"]

        for method_name in candidates:
            method = getattr(self._api, method_name, None)
            if method is None:
                log.debug("Method %s not found on client, skipping", method_name)
                continue
            log.debug("Fetching timeline via %s", method_name)
            try:
                result = self._resolve(method())
                events = self._parse_result(result)
                log.debug("Got %d raw events from %s", len(events), method_name)
                return events
            except Exception as exc:
                log.warning("Method %s failed: %s — stopping", method_name, exc)
                raise RuntimeError(f"Timeline fetch via {method_name} failed: {exc}") from exc

        raise RuntimeError("No supported timeline method worked for the current pytr connection")

    def _resolve(self, coroutine_or_result: Any) -> Any:
        run_blocking = getattr(self._api, "run_blocking", None)
        if run_blocking is not None and hasattr(coroutine_or_result, "__await__"):
            return run_blocking(coroutine_or_result, timeout=30.0)
        return coroutine_or_result

    @staticmethod
    def _parse_result(result: Any) -> list[dict[str, Any]]:
        if result is None:
            return []
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if isinstance(result, dict):
            items = result.get("items") or result.get("data") or []
            return [item for item in items if isinstance(item, dict)]
        return []


# ---------------------------------------------------------------------------
# Module-level functions kept for backwards compatibility
# ---------------------------------------------------------------------------

def connect_trade_republic(
    phone_number: str,
    pin: str,
    data_dir: Path,
    on_login_required: Any = None,
    on_login_success: Any = None,
) -> Any:
    client = TRClient(phone_number, pin, data_dir)
    client.connect(on_login_required=on_login_required, on_login_success=on_login_success)
    return client


def fetch_timeline_events(client: Any, since: datetime) -> list[dict[str, Any]]:
    if isinstance(client, TRClient):
        return client.fetch_timeline_events(since)
    # Legacy: raw pytr client passed directly
    tr = TRClient.__new__(TRClient)
    tr._api = client
    return tr.fetch_timeline_events(since)
