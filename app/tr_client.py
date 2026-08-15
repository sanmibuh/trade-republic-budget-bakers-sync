from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class LoginFailedError(Exception):
    """Raised when the interactive Trade Republic 2FA login fails."""


class SessionExpiredError(Exception):
    """Raised when a non-interactive run needs a 2FA authenticator code.

    The saved session could not be resumed and Trade Republic requires an
    authenticator code, but there is no terminal to prompt on (e.g. a scheduled
    cron sync). Rather than crashing on ``input()`` — which also hammers the
    login endpoint on every run and risks a rate-limit ban — we bail out cleanly
    so the caller can notify the user to run the interactive bootstrap.
    """


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
        code_provider: Any = None,
    ) -> None:
        """Establish a Trade Republic session (resume or full 2FA login).

        ``code_provider`` supplies the authenticator code when Trade Republic
        requires one (its ``get_code()`` returns the code as a string). When it
        is ``None`` and an authenticator code is required, ``SessionExpiredError``
        is raised instead of blocking — this is the case for a scheduled sync
        with no terminal and no Telegram fallback configured.
        """
        client = self._create_api_client()

        if client.resume_websession():
            log.info("Resumed existing Trade Republic session")
            self._api = client
            return

        log.info("No saved session found, starting interactive login")

        if on_login_required:
            on_login_required()

        self._perform_weblogin(client, code_provider)

        log.info("Login completed, session saved")

        if on_login_success:
            on_login_success()

        self._api = client

    def _create_api_client(self) -> Any:
        """Instantiate and return a configured TradeRepublicApi client."""
        from pytr.api import TradeRepublicApi

        return TradeRepublicApi(
            phone_no=self._phone_number,
            pin=self._pin,
            save_cookies=True,
            credentials_file=str(self._data_dir / "credentials.json"),
            cookies_file=str(self._data_dir / "cookies.txt"),
            use_v2_login=True,
        )

    def _perform_weblogin(self, client: Any, code_provider: Any) -> None:
        """Initiate weblogin and delegate to the appropriate 2FA flow."""
        try:
            client.initiate_weblogin()
            if client.weblogin_needs_authenticator:
                self._do_authenticator_login(client, code_provider)
            else:
                self._do_push_notification_login(client)
        except (LoginFailedError, SessionExpiredError):
            raise
        except Exception as exc:
            log.exception("Login failed with exception")
            raise LoginFailedError(f"2FA login failed: {exc}") from exc

    def _do_authenticator_login(self, client: Any, code_provider: Any) -> None:
        """Complete login using a TOTP authenticator code."""
        if code_provider is None:
            log.warning(
                "Authenticator 2FA code required but no code provider is "
                "available — run the bootstrap command to renew the session"
            )
            raise SessionExpiredError(
                "Authenticator code required but no code provider available"
            )
        log.info("Obtaining authenticator code")
        code = code_provider.get_code()
        log.debug("Submitting authenticator code")
        client.complete_weblogin(verify_code=code)
        log.debug("Polling login process for CONFIRMED status")
        # NOTE: _await_weblogin_confirmation is a private pytr method.
        # If pytr renames it in a future version this will raise AttributeError at runtime.
        client._await_weblogin_confirmation()
        client.save_websession()

    def _do_push_notification_login(self, client: Any) -> None:
        """Complete login by waiting for in-app push notification approval."""
        log.info("Waiting for push notification approval in Trade Republic app...")
        client.complete_weblogin()
        client.save_websession()

    def fetch_timeline_events(
        self, since: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Fetch timeline events with full details via pytr's Timeline class.

        Runs pytr's async Timeline loop in a single asyncio.run() call so that
        timeline_transactions, timeline_activity_log, and timeline_detail_v2
        subscriptions all share the same event loop — which is required by pytr's
        websocket architecture.

        Each returned event may contain a ``details`` key with the full
        timelineDetailV2 payload (e.g. counterparty info for bank transfers).
        """
        if self._api is None:
            raise RuntimeError(
                "TRClient.connect() must be called before fetch_timeline_events()"
            )

        from pytr.timeline import Timeline

        collected: list[dict[str, Any]] = []
        not_before = since.timestamp() if since is not None else 0.0

        timeline = Timeline(
            tr=self._api,
            output_path=self._data_dir,
            not_before=not_before,
            store_event_database=False,
            event_callback=collected.append,
        )

        try:
            asyncio.run(timeline.tl_loop())
        except Exception as exc:
            raise RuntimeError(f"Timeline fetch failed: {exc}") from exc

        log.debug("Got %d events from Timeline", len(collected))
        return collected
