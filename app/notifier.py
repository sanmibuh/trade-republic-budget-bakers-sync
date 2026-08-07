from __future__ import annotations

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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
    except requests.RequestException:
        return False
    return True


def notify_authentication_required(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    safe_owner_name = _escape_markdown(owner_name)
    message = (
        "🚨 *Trade Republic Sync: Session Expired*\n\n"
        f"Owner: *{safe_owner_name}*\n"
        "The saved Trade Republic session is no longer valid\\.\n"
        "Run the bootstrap command to renew the 2FA session\\."
    )
    return send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)


def notify_login_required(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    safe_owner_name = _escape_markdown(owner_name)
    message = (
        "🔐 *Trade Republic Sync: Login Required*\n\n"
        f"Owner: *{safe_owner_name}*\n"
        "No saved session found\\. A new 2FA login has been initiated\\.\n"
        "Check your Trade Republic app to approve the request\\."
    )
    return send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)


def notify_login_failed(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    safe_owner_name = _escape_markdown(owner_name)
    message = (
        "❌ *Trade Republic Sync: Login Failed*\n\n"
        f"Owner: *{safe_owner_name}*\n"
        "The 2FA code was incorrect or the login request was rejected\\.\n"
        "Run the bootstrap command again to retry\\."
    )
    return send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)


def notify_login_success(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    safe_owner_name = _escape_markdown(owner_name)
    message = (
        "✅ *Trade Republic Sync: Login Successful*\n\n"
        f"Owner: *{safe_owner_name}*\n"
        "Session saved\\. Future syncs will run automatically\\."
    )
    return send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)


def notify_error(bot_token: str | None, chat_id: str | None, owner_name: str, error: Exception) -> bool:
    safe_owner_name = _escape_markdown(owner_name)
    safe_error = _escape_markdown(f"{type(error).__name__}: {error}")
    message = (
        "❌ *Trade Republic Sync Failed*\n\n"
        f"Owner: *{safe_owner_name}*\n"
        f"Error: `{safe_error}`"
    )
    return send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)
