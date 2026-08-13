"""Two-factor authenticator code providers for the Trade Republic login flow.

Two strategies exist for obtaining the authenticator code during a v2 web login:

* ``TerminalCodeProvider`` — reads the code from stdin. Used for the interactive
  bootstrap (``docker compose run -it``), where a human is at the terminal.
* ``TelegramCodeProvider`` — sends a Telegram prompt asking the user to reply
  with ``/code <instance> <code>`` and then polls a file that the Telegram bot
  writes into the container. Used for scheduled cron syncs and the on-demand
  ``/login`` command, where there is no terminal.

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

# File (inside the container's data dir) where the Telegram bot writes the
# authenticator code so the waiting login process can pick it up.
CODE_FILENAME = ".tr_2fa_code"

_DEFAULT_TIMEOUT = 300.0
_DEFAULT_POLL_INTERVAL = 3.0


class TerminalCodeProvider:
    """Reads the authenticator code from stdin (interactive bootstrap)."""

    def get_code(self) -> str:
        return input().strip()


class TelegramCodeProvider:
    """Prompts via Telegram and polls a file for the authenticator code."""

    def __init__(
        self,
        code_file: Path | str,
        prompt: Callable[[], Any],
        *,
        timeout: float = _DEFAULT_TIMEOUT,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        sleep: Callable[[float], Any] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._code_file = Path(code_file)
        self._prompt = prompt
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._sleep = sleep
        self._now = now

    def get_code(self) -> str:
        """Prompt the user and block until a code arrives or the timeout elapses."""
        self._clear()
        log.info("Requesting 2FA authenticator code via Telegram")
        self._prompt()

        deadline = self._now() + self._timeout
        while self._now() < deadline:
            code = self._read()
            if code:
                self._clear()
                log.info("Received 2FA authenticator code")
                return code
            self._sleep(self._poll_interval)

        raise TimeoutError("Timed out waiting for the 2FA authenticator code")

    def _read(self) -> str:
        try:
            return self._code_file.read_text().strip()
        except FileNotFoundError:
            return ""

    def _clear(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._code_file.unlink()


def select_code_provider(
    *,
    data_dir: Path,
    notifier: Any,
    instance: str,
    isatty: bool,
    telegram_configured: bool,
) -> TerminalCodeProvider | TelegramCodeProvider | None:
    """Choose the authenticator-code strategy for the current environment."""
    if isatty:
        return TerminalCodeProvider()
    if telegram_configured:
        code_file = data_dir / CODE_FILENAME
        return TelegramCodeProvider(
            code_file,
            lambda: notifier.login_code_request(instance),
        )
    return None
