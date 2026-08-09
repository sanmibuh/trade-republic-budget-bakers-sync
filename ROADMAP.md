# Roadmap

Pending ideas and improvements, roughly ordered by impact. No fixed dates or priorities — picked up as convenient.

---

## Sync

### Reduce notification noise on sync
Every sync run sends two separate messages: a _"fetched N events"_ summary and a _"success, saved M"_ confirmation. Merge them into a single message sent at the end, combining both period, fetch counts, and save results.

---

## Bot

### 2FA via Telegram bot
Evaluate whether the bot can handle the Trade Republic 2FA flow: when a container requires authentication, the bot prompts the user for the PIN or approval and forwards it to the container. Security considerations must be assessed carefully — the bot would be transmitting sensitive credentials over Telegram.

---

## Deploy

### Health checks in docker-compose
Services have no `healthcheck` defined. Docker cannot tell whether a container is actually working or hung. Add a minimal check (e.g. verify the cron process is alive).

### Separate Telegram credentials from sync env
`common.env` includes `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, which `sync-david` and `sync-eli` do not need (only the bot uses them for notifications, and only when set). Split into a `telegram.env` mounted only by the bot and, optionally, the sync services.
