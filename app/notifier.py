from __future__ import annotations

import logging
from typing import TypedDict

import requests

from app.http_client import http_post

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class _FetchContext(TypedDict):
    since: str
    until: str
    fetched: int
    new: int
    skipped: int


def escape_markdown(value: str) -> str:
    escaped = value
    for token in (
        "\\",
        "_",
        "*",
        "[",
        "]",
        "(",
        ")",
        "~",
        "`",
        ">",
        "#",
        "+",
        "-",
        "=",
        "|",
        "{",
        "}",
        ".",
        "!",
    ):
        escaped = escaped.replace(token, f"\\{token}")
    return escaped


def _escape_code(value: str) -> str:
    """Escape a value for a MarkdownV2 inline-code span.

    Inside `` `...` `` only backslash and backtick are special; every other
    character (``-``, ``.``, ``_`` …) is rendered literally, so escaping them
    the normal way would show the backslashes to the user.
    """
    return value.replace("\\", "\\\\").replace("`", "\\`")


def send_telegram_message(
    bot_token: str | None,
    chat_id: str | None,
    message: str,
    reply_markup: dict | None = None,
) -> bool:
    if not bot_token or not chat_id:
        return False

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "MarkdownV2",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        response = http_post(
            TELEGRAM_API.format(token=bot_token), json=payload, timeout=20
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
        self._fetch_context: _FetchContext | None = None

    def _send(self, message: str) -> bool:
        return send_telegram_message(
            bot_token=self._bot_token,
            chat_id=self._chat_id,
            message=message,
        )

    def _send_with_markup(self, message: str, reply_markup: dict) -> bool:
        return send_telegram_message(
            bot_token=self._bot_token,
            chat_id=self._chat_id,
            message=message,
            reply_markup=reply_markup,
        )

    def _safe_owner(self) -> str:
        return escape_markdown(self._owner_name)

    def _header(self, icon: str, title: str) -> str:
        return f"{icon} *Trade Republic Sync: {escape_markdown(title)}*\n\nOwner: *{self._safe_owner()}*\n"

    def authentication_required(self) -> bool:
        return self._send(
            self._header("🚨", "Session Expired")
            + "The saved Trade Republic session is no longer valid\\.\n"
            "Run the bootstrap command to renew the 2FA session\\."
        )

    def login_required(self) -> bool:
        return self._send(
            self._header("🔐", "Login Required")
            + "No saved session found\\. A new 2FA login has been initiated\\.\n"
            "Check your Trade Republic app to approve the request\\."
        )

    def login_failed(self) -> bool:
        return self._send(
            self._header("❌", "Login Failed")
            + "The 2FA code was incorrect or the login request was rejected\\.\n"
            "Run the bootstrap command again to retry\\."
        )

    def login_code_request(self, instance: str) -> bool:
        safe_instance = _escape_code(instance)
        return self._send_with_markup(
            self._header("🔐", "2FA Code Required") + f"Instance: `{safe_instance}`\n"
            "Just reply here with your 6\\-digit authenticator code\\.\n"
            "_\\(Or use `/code "
            f"{safe_instance}"
            " <code>` from any message\\.\\)_",
            reply_markup={
                "force_reply": True,
                "input_field_placeholder": "6-digit code",
            },
        )

    def login_code_timeout(self, instance: str) -> bool:
        safe_instance = _escape_code(instance)
        return self._send(
            self._header("⏱", "2FA Timeout")
            + f"The code request for `{safe_instance}` has expired\\.\n"
            "Run `/sync` to trigger a new authentication attempt\\."
        )

    def login_success(self) -> bool:
        return self._send(
            self._header("✅", "Login Successful")
            + "Session saved\\. Future syncs will run automatically\\."
        )

    def error(self, exc: Exception) -> bool:
        safe_error = escape_markdown(f"{type(exc).__name__}: {exc}")
        return self._send(self._header("❌", "Sync Failed") + f"Error: `{safe_error}`")

    def fetch_summary(
        self,
        *,
        since: str,
        until: str,
        fetched: int,
        new: int,
        skipped: int,
    ) -> None:
        """Buffer fetch context to be included in the final sync_complete message."""
        self._fetch_context = _FetchContext(
            since=since,
            until=until,
            fetched=fetched,
            new=new,
            skipped=skipped,
        )

    def unknown_event_type(self, event_type: str) -> bool:
        safe_type = _escape_code(event_type)
        return self._send(
            self._header("⚠️", "Unknown Event Type")
            + f"Event type `{safe_type}` is not recognised\\.\n"
            "It has been processed using the default cash handler\\.\n"
            "Please report this type so a proper handler can be added\\."
        )

    def missing_api_result(self, event_id: str, missing_indices: list[int]) -> bool:
        safe_id = _escape_code(event_id)
        safe_indices = _escape_code(", ".join(str(i) for i in missing_indices))
        return self._send(
            self._header("⚠️", "Incomplete API Response")
            + f"Event `{safe_id}` has no result for record index\\(es\\): `{safe_indices}`\\.\n"
            "The event has not been marked as processed and will be retried\\."
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
            f"{icon} *Trade Republic Sync: {safe} — {escape_markdown(status)}*\n",
        ]
        if self._fetch_context is not None:
            ctx = self._fetch_context
            safe_since = _escape_code(ctx["since"])
            safe_until = _escape_code(ctx["until"])
            lines.append(
                f"Period: `{safe_since}` → `{safe_until}`\n"
                f"Fetched: *{ctx['fetched']}* · New: *{ctx['new']}* · Already synced: *{ctx['skipped']}*\n"
            )
        lines.append(
            f"Saved: *{synced}* · Failed: *{escape_markdown(str(failed))}* · Skipped: *{skipped}*"
        )
        if excluded:
            lines.append(f"Excluded \\(zero amount\\): *{excluded}*")
        return self._send("\n".join(lines))

    def backup_complete(
        self,
        *,
        mode: str,
        period: str,
        date_from: str,
        date_to: str,
        counts: dict[str, int],
        filename: str | None = None,
    ) -> bool:
        safe = self._safe_owner()
        safe_mode = escape_markdown(mode.capitalize())
        safe_period = _escape_code(period)
        safe_from = _escape_code(date_from)
        safe_to = _escape_code(date_to)
        records = counts.get("records", 0)
        accounts = counts.get("accounts", 0)
        categories = counts.get("categories", 0)
        budgets = counts.get("budgets", 0)
        labels = counts.get("labels", 0)
        lines = [
            f"📆 *Wallet Backup Complete: {safe}*\n",
            f"Mode: *{safe_mode}* · Period: `{safe_period}`",
            f"Range: `{safe_from}` → `{safe_to}`\n",
            f"Records: *{records}* · Accounts: *{accounts}* · Categories: *{categories}*",
            f"Budgets: *{budgets}* · Labels: *{labels}*",
        ]
        if counts.get("monthly_removed"):
            removed = escape_markdown(str(counts["monthly_removed"]))
            lines.append(f"Monthly files removed: *{removed}*")
        if filename:
            lines.append(f"File: `{_escape_code(filename)}`")
        return self._send("\n".join(lines))
