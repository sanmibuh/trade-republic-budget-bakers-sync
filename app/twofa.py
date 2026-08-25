"""Two-factor authenticator code providers for the Trade Republic login flow.

Two strategies exist for obtaining the authenticator code during a v2 web login:

* ``TerminalCodeProvider`` — reads the code from stdin. Used for the interactive
  bootstrap (``docker compose run -it``), where a human is at the terminal.
* ``TelegramCodeProvider`` — sends a Telegram prompt asking the user to reply
  with ``/code <instance> <code>`` and then polls a file that the Telegram bot
  writes into the container. Used for scheduled cron syncs and bot-triggered
  syncs when the session is expired, where there is no terminal.

``select_code_provider`` picks the right strategy based on whether a TTY is
attached and whether Telegram is configured. If neither applies it returns
``None``, signalling the caller that an authenticator login cannot proceed.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Marker file created while TelegramCodeProvider is actively waiting for a code.
# Its presence signals that a login is in progress; submit-code checks for it
# before writing the code file, so stale /code submissions are rejected cleanly.
PENDING_FILENAME = ".tr_2fa_pending"

_DEFAULT_TIMEOUT = 300.0
_DEFAULT_POLL_INTERVAL = 3.0


class TerminalCodeProvider:
    """Reads the authenticator code from stdin (interactive bootstrap)."""

    def __init__(self) -> None:
        # No state to initialise — this provider reads directly from stdin.
        pass

    def get_code(self) -> str:
        return input().strip()


class TelegramCodeProvider:
    """Prompts via Telegram and polls a file for the authenticator code."""

    def __init__(
        self,
        code_file: Path | str,
        prompt: Callable[[], Any],
        *,
        pending_file: Path | str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        sleep: Callable[[float], Any] = time.sleep,
        now: Callable[[], float] = time.monotonic,
        on_timeout: Callable[[], Any] | None = None,
    ) -> None:
        self._code_file = Path(code_file)
        self._pending_file = (
            Path(pending_file)
            if pending_file is not None
            else self._code_file.parent / PENDING_FILENAME
        )
        self._prompt = prompt
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._now = now
        self._on_timeout = on_timeout

    def get_code(self) -> str:
        """Prompt the user and block until a code arrives or the timeout elapses."""
        self._clear()
        log.info("Requesting 2FA authenticator code via Telegram")
        self._prompt()
        self._set_pending()

        deadline = self._now() + self._timeout
        while self._now() < deadline:
            code = self._read()
            if code:
                self._clear()
                self._clear_pending()
                log.info("Received 2FA authenticator code")
                return code
            self._sleep(self._poll_interval)

        self._clear_pending()
        if self._on_timeout is not None:
            self._on_timeout()
        raise TimeoutError("Timed out waiting for the 2FA authenticator code")

    def _read(self) -> str:
        try:
            return self._code_file.read_text().strip()
        except FileNotFoundError:
            return ""

    def _clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._code_file.unlink()

    def _set_pending(self) -> None:
        self._pending_file.touch()

    def _clear_pending(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._pending_file.unlink()


def select_code_provider(
    *,
    code_file: Path,
    pending_file: Path,
    notifier: Any,
    instance: str,
    isatty: bool,
    telegram_configured: bool,
) -> TerminalCodeProvider | TelegramCodeProvider | None:
    """Choose the authenticator-code strategy for the current environment."""
    if isatty:
        return TerminalCodeProvider()
    if telegram_configured:
        return TelegramCodeProvider(
            code_file,
            lambda: notifier.login_code_request(instance),
            pending_file=pending_file,
            on_timeout=lambda: notifier.login_code_timeout(instance),
        )
    return None
