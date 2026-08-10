# Trade Republic → BudgetBakers Wallet Sync

Automatically syncs your Trade Republic transactions into [BudgetBakers Wallet](https://budgetbakers.com/) as records. Runs as a Docker container — either as a one-shot command or as a scheduled daemon with a built-in cron.

Supports multiple accounts, Telegram notifications, and automatic deduplication so re-running never creates duplicate records.

Also includes a **wallet backup** feature that saves full JSON snapshots of your BudgetBakers data (accounts, categories, budgets, labels, records) on a daily schedule.

---

## Quickstart

### 1. Copy the example config

```sh
cp deploy/example/docker-compose.yml   docker-compose.yml
cp deploy/example/wallet.env.example   wallet.env
cp deploy/example/telegram.env.example telegram.env
cp deploy/example/sync-1.env.example   sync-1.env
```

Fill in `wallet.env` (Wallet API key), `telegram.env` (Telegram credentials) and `sync-1.env` (Trade Republic phone + PIN). Update `WALLET_CASH_ACCOUNT_ID` and `WALLET_PORTFOLIO_ACCOUNT_ID` in `docker-compose.yml`.

### 2. First-time login (interactive 2FA)

`pytr` requires an interactive login the first time (or after session expiry):

```sh
./tr-sync.sh bootstrap sync-1
```

Approve the push notification in your Trade Republic app (or enter the authenticator code). The session is saved to `./1/` and reused in all future runs.

### 3. Start the daemon

```sh
./tr-sync.sh up sync-1
```

Or start all services at once:

```sh
./tr-sync.sh up
```

---

## Environment variables

### Required (sync services)

| Variable | Description |
|---|---|
| `PHONE_NUMBER` | Trade Republic account phone number (e.g. `+49123456789`) |
| `PIN` | Trade Republic account PIN |
| `WALLET_API_KEY` | BudgetBakers Wallet API key |
| `WALLET_CASH_ACCOUNT_ID` | BudgetBakers cash account UUID |
| `WALLET_PORTFOLIO_ACCOUNT_ID` | BudgetBakers portfolio account UUID |

### Optional

| Variable | Default | Description |
|---|---|---|
| `OWNER_NAME` | `Backup` | Display name used in Telegram notifications |
| `MODE` | — | `sync`, `backup`, or `bot` — required by entrypoint |
| `LOOKBACK_DAYS` | `7` | How many days back to fetch and sync |
| `TZ` | `UTC` | Container timezone — affects cron schedule interpretation (e.g. `Europe/Berlin`) |
| `SYNC_SCHEDULE` | — | 5-field cron expression for the sync job. If unset, runs once and exits. |
| `BACKUP_SCHEDULE` | — | 5-field cron expression for the daily backup job (runs `auto` mode). |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for notifications |
| `LABEL_<EVENT_TYPE>` | — | BudgetBakers label UUID to attach to records of that event type (see below) |

### Labels per event type

Set a `LABEL_` variable for any event type you want to tag automatically:

```yaml
LABEL_BANK_TRANSACTION_INCOMING: "<uuid>"
LABEL_BANK_TRANSACTION_OUTGOING: "<uuid>"
LABEL_CARD_TRANSACTION: "<uuid>"
```

Supported event types:
`BANK_TRANSACTION_INCOMING`, `BANK_TRANSACTION_OUTGOING`, `CARD_TRANSACTION`,
`INTEREST_PAYOUT`, `INTEREST_PAYMENT`, `BUY_ORDER`, `SELL_ORDER`, `SAVINGS_PLAN`,
`TRADING_SAVINGSPLAN_EXECUTED`, `SAVEBACK_AGGREGATE`, `SPARE_CHANGE_AGGREGATE`,
`SAVEBACK`, `PAYMENT_INBOUND`.

---

## Services architecture

The recommended setup uses four separate services, each with a single responsibility:

```
sync-1        — syncs account 1 (has TR credentials, no backup schedule)
sync-2        — syncs account 2 (has TR credentials, no backup schedule)
backup        — runs daily wallet backups (no TR credentials)
telegram-bot  — remote control via Telegram (no TR credentials)
```

See `deploy/example/docker-compose.yml` for a ready-to-use template.

---

## Wallet backup

The `backup` service runs a daily backup in **`auto` mode**:

- Overwrites the backup for the current month (partial, fresh data)
- Overwrites the backup for the previous month
- In February: generates a yearly backup for the previous year (only once — idempotent) and removes the covered monthly files

Backups are stored as JSON files inside the data volume:

```
/app/data/backups/
  monthly/
    wallet-monthly-2026-07.json
    wallet-monthly-2026-08.json
  yearly/
    wallet-yearly-2025.json
```

Each file contains: `mode`, `date_from`, `date_to`, `generated_at`, `accounts`, `categories`, `budgets`, `labels`, `records`.

### Manual backups

```sh
./tr-sync.sh backup auto             # same as the scheduled job
./tr-sync.sh backup monthly          # previous month
./tr-sync.sh backup monthly 2026-07  # specific month
./tr-sync.sh backup yearly           # previous year
./tr-sync.sh backup yearly 2025      # specific year
```

The container also exposes a unified CLI:

```
python -m app --help
python -m app sync
python -m app backup auto
python -m app backup monthly [YYYY-MM]
python -m app backup yearly  [YYYY]
python -m app bot
```

---

## Telegram bot (remote control)

An optional `telegram-bot` service lets you trigger sync and backup operations on demand from Telegram, without accessing the server.

### Commands

| Command | Description |
|---|---|
| `/sync` | Force a Trade Republic sync — choose instance via inline buttons |
| `/backup_monthly [YYYY-MM]` | Force a monthly backup (default: previous month) |
| `/backup_yearly [YYYY]` | Force a yearly backup (default: previous year) |
| `/status` | Show all instances and whether backup is available |
| `/help` | Show available commands |

Backup commands are only available when `BACKUP_SERVICE` is configured. The bot runs all backup operations on the dedicated backup container — not per sync instance.

### Setup

Add the `telegram-bot` service to your `docker-compose.yml`:

```yaml
name: tr-sync

services:
  telegram-bot:
    image: ghcr.io/sanmibuh/tr-wallet-sync:<tag>
    entrypoint: ["python", "-m", "app", "bot"]
    env_file:
      - telegram.env
    environment:
      # Comma-separated list of sync instance names (without "sync-" prefix)
      INSTANCES: "1,2"
      # Must match the Docker Compose project name (name: field above)
      CONTAINER_PREFIX: "tr-sync"
      # Name of the backup service (leave empty to disable backup commands)
      BACKUP_SERVICE: "backup"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    restart: unless-stopped
```

> **Security:** The bot only responds to messages from `TELEGRAM_CHAT_ID`. All other chats are silently ignored.

> **How it works:** The bot uses `docker exec` to run commands inside the target containers. The container's own Notifier then sends the result notification to Telegram, just like a scheduled run would.

---

## tr-sync.sh reference

The `tr-sync.sh` script is the main management tool for NAS deployments where `make` is not available.

```sh
./tr-sync.sh pull      [service]           # pull image(s) — omit for all
./tr-sync.sh bootstrap <sync-service>      # interactive 2FA login
./tr-sync.sh sync      <sync-service>      # one-shot sync
./tr-sync.sh backup    <mode> [param]      # one-shot backup
./tr-sync.sh up        [service]           # start daemon(s)
./tr-sync.sh down      [service]           # stop daemon(s)
./tr-sync.sh upgrade   [service]           # pull + down + up
./tr-sync.sh logs      <service>           # follow logs
```

---

## Local development

For running tests and one-shot commands locally (no Docker):

```sh
make test        # run test suite with coverage
make lint        # run ruff linter
make run-sync    # one-shot sync (env vars must be set)
make run-backup  # one-shot backup auto
make run-bot     # start the Telegram bot
make clean       # remove __pycache__ and .pytest_cache
```

---

## NAS deployment

See [deploy/DEPLOY.md](deploy/DEPLOY.md) for the full step-by-step deploy guide.

See [ARCHITECTURE.md](ARCHITECTURE.md) for full technical details.
