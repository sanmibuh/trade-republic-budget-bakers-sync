# Roadmap

Pending ideas and improvements, roughly ordered by impact. No fixed dates or priorities — picked up as convenient.

---

## Bot

### 2FA via Telegram bot
Evaluate whether the bot can handle the Trade Republic 2FA flow: when a container requires authentication, the bot prompts the user for the PIN or approval and forwards it to the container. Security considerations must be assessed carefully — the bot would be transmitting sensitive credentials over Telegram.

---

## Deploy

### Separate Telegram credentials from sync env
`common.env` includes `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, which `sync-david` and `sync-eli` do not need (only the bot uses them for notifications, and only when set). Split into a `telegram.env` mounted only by the bot and, optionally, the sync services.
