# Roadmap

Pending ideas and improvements, roughly ordered by impact. No fixed dates or priorities — picked up as convenient.

---

## Sync

### Better handling of unknown event types
Currently, unknown event types (e.g. `CARD_REFUND`) trigger an ⚠️ notification and are processed with the default cash handler. Two improvements:
- **Prefill the description** as `Refund: <original title>` instead of the raw event title.
- **Link to the original transaction**: investigate whether the Trade Republic API includes a reference to the origin transaction in the refund event, and if so, surface it in the description or as a note.

### Reduce notification noise on sync
Every sync run sends two separate messages: a _"fetched N events"_ summary and a _"success, saved M"_ confirmation. Merge them into a single message sent at the end, combining both period, fetch counts, and save results.

---

## Bot

### Bot does not confirm command result
When the user runs `/sync david` or `/backup auto`, the bot replies _"Executing..."_ but never sends a confirmation when the command finishes. The user has no way to know whether it succeeded, failed, or is still running. `_docker_exec_silent` captures the result but does not forward it to the chat.

### Blocking execution in the bot
`subprocess.run` blocks the polling thread for the entire duration of the sync or backup. If the command takes long (full sync, yearly backup), the bot stops responding to other messages during that time. Fix: run commands in a separate thread and notify when done.

### 2FA via Telegram bot
Evaluate whether the bot can handle the Trade Republic 2FA flow: when a container requires authentication, the bot prompts the user for the PIN or approval and forwards it to the container. Security considerations must be assessed carefully — the bot would be transmitting sensitive credentials over Telegram.

### Replace Docker socket with an internal HTTP API
The bot currently accesses the Docker socket (`/var/run/docker.sock`) to run `docker exec`. A more robust alternative: each container exposes a minimal HTTP endpoint that the bot calls directly, removing the need for the socket. Discarded for now due to complexity, but it would eliminate the security risk of exposing the socket.

---

## CI/CD

### GitHub Actions for image build and push
Automate the build and push of `python-trade-republic` and `tr-wallet-sync` to ghcr.io when a tag is created on GitHub. Currently done manually.

---

## Deploy

### Health checks in docker-compose
Services have no `healthcheck` defined. Docker cannot tell whether a container is actually working or hung. Add a minimal check (e.g. verify the cron process is alive).

### Separate Telegram credentials from sync env
`common.env` includes `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, which `sync-david` and `sync-eli` do not need (only the bot uses them for notifications, and only when set). Split into a `telegram.env` mounted only by the bot and, optionally, the sync services.
