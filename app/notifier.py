from __future__ import annotations

import logging
import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _escape_markdown(value: str) -> str:
    escaped = value
    for token in ("\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        escaped = escaped.replace(token, f"\\{token}")
    return escaped


def send_telegram_message(bot_token: str | None, chat_id: str | None, message: str) -> bool:
    if not bot_token or not chat_id:
        return False

    try:
        response = requests.post(
            TELEGRAM_API.format(token=bot_token),
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "MarkdownV2",
                "disable_web_page_preview": True,
            },
            timeout=20,
            verify=False,
        )
        response.raise_for_status()
    except requests.HTTPError as exc:
        log.warning(
            "Telegram rejected message (HTTP %s): %s — message was: %r",
            exc.response.status_code if exc.response is not None else "?",
            exc.response.text if exc.response is not None else "",
            message,
        )
        return False
    except requests.RequestException as exc:
        log.warning("Telegram request failed: %s", exc)
        return False
    return True


class Notifier:
    """Holds Telegram credentials and owner context; exposes one method per notification type."""

    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        owner_name: str,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._owner_name = owner_name

    def _send(self, message: str) -> bool:
        return send_telegram_message(
            bot_token=self._bot_token,
            chat_id=self._chat_id,
            message=message,
        )

    def _safe_owner(self) -> str:
        return _escape_markdown(self._owner_name)

    def authentication_required(self) -> bool:
        safe = self._safe_owner()
        return self._send(
            "🚨 *Trade Republic Sync: Session Expired*\n\n"
            f"Owner: *{safe}*\n"
            "The saved Trade Republic session is no longer valid\\.\n"
            "Run the bootstrap command to renew the 2FA session\\."
        )

    def login_required(self) -> bool:
        safe = self._safe_owner()
        return self._send(
            "🔐 *Trade Republic Sync: Login Required*\n\n"
            f"Owner: *{safe}*\n"
            "No saved session found\\. A new 2FA login has been initiated\\.\n"
            "Check your Trade Republic app to approve the request\\."
        )

    def login_failed(self) -> bool:
        safe = self._safe_owner()
        return self._send(
            "❌ *Trade Republic Sync: Login Failed*\n\n"
            f"Owner: *{safe}*\n"
            "The 2FA code was incorrect or the login request was rejected\\.\n"
            "Run the bootstrap command again to retry\\."
        )

    def login_success(self) -> bool:
        safe = self._safe_owner()
        return self._send(
            "✅ *Trade Republic Sync: Login Successful*\n\n"
            f"Owner: *{safe}*\n"
            "Session saved\\. Future syncs will run automatically\\."
        )

    def error(self, exc: Exception) -> bool:
        safe = self._safe_owner()
        safe_error = _escape_markdown(f"{type(exc).__name__}: {exc}")
        return self._send(
            "❌ *Trade Republic Sync Failed*\n\n"
            f"Owner: *{safe}*\n"
            f"Error: `{safe_error}`"
        )

    def fetch_summary(
        self,
        *,
        since: str,
        until: str,
        fetched: int,
        new: int,
        skipped: int,
    ) -> bool:
        safe = self._safe_owner()
        safe_since = _escape_markdown(since)
        safe_until = _escape_markdown(until)
        return self._send(
            f"📥 *Trade Republic Sync: {safe}*\n\n"
            f"Period: `{safe_since}` → `{safe_until}`\n"
            f"Fetched: *{fetched}* events\n"
            f"New: *{new}* · Skipped \\(already synced\\): *{skipped}*"
        )

    def sync_complete(
        self,
        *,
        synced: int,
        failed: int,
        skipped: int,
        excluded: int = 0,
    ) -> bool:
        safe = self._safe_owner()
        if failed == 0:
            icon, status = "✅", "Success"
        elif synced == 0:
            icon, status = "❌", "All Failed"
        else:
            icon, status = "⚠️", "Partial"
        lines = [
            f"{icon} *Trade Republic Sync: {safe} — {_escape_markdown(status)}*\n",
            f"Saved: *{synced}* · Failed: *{_escape_markdown(str(failed))}* · Skipped: *{skipped}*",
        ]
        if excluded:
            lines.append(f"Excluded \\(zero amount\\): *{excluded}*")
        return self._send("\n".join(lines))


# ---------------------------------------------------------------------------
# Module-level functions kept for backwards compatibility / standalone use
# ---------------------------------------------------------------------------

def notify_authentication_required(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    return Notifier(bot_token, chat_id, owner_name).authentication_required()


def notify_login_required(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    return Notifier(bot_token, chat_id, owner_name).login_required()


def notify_login_failed(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    return Notifier(bot_token, chat_id, owner_name).login_failed()


def notify_login_success(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    return Notifier(bot_token, chat_id, owner_name).login_success()


def notify_error(bot_token: str | None, chat_id: str | None, owner_name: str, error: Exception) -> bool:
    return Notifier(bot_token, chat_id, owner_name).error(error)


def notify_fetch_summary(
    bot_token: str | None,
    chat_id: str | None,
    owner_name: str,
    since: str,
    until: str,
    fetched: int,
    new: int,
    skipped: int,
) -> bool:
    return Notifier(bot_token, chat_id, owner_name).fetch_summary(
        since=since, until=until, fetched=fetched, new=new, skipped=skipped
    )


def notify_sync_complete(
    bot_token: str | None,
    chat_id: str | None,
    owner_name: str,
    synced: int,
    failed: int,
    skipped: int,
    excluded: int = 0,
) -> bool:
    return Notifier(bot_token, chat_id, owner_name).sync_complete(
        synced=synced, failed=failed, skipped=skipped, excluded=excluded
    )
