from __future__ import annotations

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(bot_token: str | None, chat_id: str | None, message: str) -> bool:
    if not bot_token or not chat_id:
        return False

    response = requests.post(
        TELEGRAM_API.format(token=bot_token),
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()
    return True


def notify_authentication_required(bot_token: str | None, chat_id: str | None, owner_name: str) -> bool:
    message = (
        "🚨 *Trade Republic Sync Authentication Required*\n\n"
        f"Owner: *{owner_name}*\n"
        "Trade Republic session is no longer valid (401/AuthenticationError).\n"
        "Please manually renew the 2FA session in terminal and rerun the sync container."
    )
    return send_telegram_message(bot_token=bot_token, chat_id=chat_id, message=message)
