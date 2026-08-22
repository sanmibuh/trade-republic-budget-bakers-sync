# Trade Republic → BudgetBakers Wallet Sync

Automatically syncs your Trade Republic transactions into [BudgetBakers Wallet](https://budgetbakers.com/) as records. Runs as a Docker container — either as a one-shot command or as a scheduled daemon with a built-in cron.

Supports multiple accounts, Telegram notifications, and automatic deduplication so re-running never creates duplicate records.

Also includes a **wallet backup** feature that saves full JSON snapshots of your BudgetBakers data (accounts, categories, budgets, labels, records) on a daily schedule.

---

## Quickstart

### 1. Copy the example config

```sh
cp deploy/example/docker-compose.yml   docker-compose.yml
cp deploy/example/instances.yml.example instances.yml
```

Fill in `instances.yml` with your Trade Republic credentials, Wallet API key, and account IDs.
See `deploy/example/instances.yml.example` for the full reference with all available options.

### 2. First-time login (interactive 2FA)

`pytr` requires an interactive login the first time (or after session expiry):

```sh
./tr-sync.sh bootstrap <instance-name>
```

Approve the push notification in your Trade Republic app (or enter the authenticator code). The session is saved to the data volume and reused in all future runs.

### 3. Start the daemon

```sh
./tr-sync.sh up
```

---

## Configuration

All configuration lives in a single `instances.yml` file, mounted into the container at `/app/config/instances.yml`.

```yaml
# Telegram bot credentials (optional — enables notifications and remote control)
telegram_bot_token: ""
telegram_chat_id: ""

# Wallet backup config (optional)
backup:
  wallet_api_key: ""

# Cron expression for automatic backups (optional)
backup_schedule: "0 8 1 * *"

# Override default data directory (optional, default: /app/data)
# data_dir: /app/data

sync:
  instances:
    - name: david
      phone: "+49123456789"
      pin: "1234"
      wallet_api_key: "<Wallet API key>"
      wallet_cash_account_id: "<UUID>"
      wallet_portfolio_account_id: "<UUID>"
      # Optional per-instance overrides:
      # owner_name: "David"
      # lookback_days: 7
      # dedup_ttl_days: 60
      # schedule: "*/30 * * * *"
      # category_strategy: history   # auto-categorize from past records
      # labels:
      #   BANK_TRANSACTION_INCOMING: "<label-uuid>"
      #   CARD_TRANSACTION: "<label-uuid>"
      # allow_insecure_ssl: false
```

### Per-instance options

| Field | Default | Description |
|---|---|---|
| `name` | — | Unique instance identifier used in CLI commands and bot buttons |
| `phone` | — | Trade Republic account phone number |
| `pin` | — | Trade Republic account PIN |
| `wallet_api_key` | — | BudgetBakers Wallet API key |
| `wallet_cash_account_id` | — | BudgetBakers cash account UUID |
| `wallet_portfolio_account_id` | — | BudgetBakers portfolio account UUID |
| `owner_name` | `name` (capitalized) | Display name used in Telegram notifications |
| `lookback_days` | `7` | How many days back to fetch and sync |
| `dedup_ttl_days` | `60` | Days to keep deduplication records in SQLite |
| `schedule` | — | 5-field cron expression. If unset, the container runs once and exits. |
| `category_strategy` | `none` | `none` (disabled) or `history` (majority-vote from past records) |
| `labels` | — | Map of event type → BudgetBakers label UUID (see below) |
| `allow_insecure_ssl` | `false` | Skip TLS verification (for corporate proxies) |

### Global options

| Field | Default | Description |
|---|---|---|
| `telegram_bot_token` | — | Telegram bot token for notifications and remote control |
| `telegram_chat_id` | — | Telegram chat ID — only this chat can interact with the bot |
| `backup.wallet_api_key` | — | Wallet API key used by the backup service |
| `backup_schedule` | — | Cron expression for the backup job |
| `data_dir` | `/app/data` | Override the data directory inside the container |
| `TZ` *(env var)* | `UTC` | Container timezone — affects cron schedule interpretation |

### Labels per event type

Assign a BudgetBakers label UUID to records of a specific event type:

```yaml
labels:
  BANK_TRANSACTION_INCOMING: "<uuid>"
  BANK_TRANSACTION_OUTGOING: "<uuid>"
  CARD_TRANSACTION: "<uuid>"
```

Supported event types:
`BANK_TRANSACTION_INCOMING`, `BANK_TRANSACTION_OUTGOING`, `CARD_TRANSACTION`,
`INTEREST_PAYOUT`, `INTEREST_PAYMENT`, `BUY_ORDER`, `SELL_ORDER`, `SAVINGS_PLAN`,
`TRADING_SAVINGSPLAN_EXECUTED`, `SAVEBACK_AGGREGATE`, `SPARE_CHANGE_AGGREGATE`,
`SAVEBACK`, `PAYMENT_INBOUND`.

---

## Services architecture

A typical setup uses a single Docker Compose project with three services:

```
sync         — runs all sync instances defined in instances.yml (cron daemon or one-shot)
backup       — runs scheduled Wallet backups
telegram-bot — remote control via Telegram (no TR credentials needed)
```

All services share the same image and the same `instances.yml`. See `deploy/example/docker-compose.yml` for a ready-to-use template.

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
python -m app sync --instance david
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
| `/login` | Renew Trade Republic 2FA session — choose instance via inline buttons |
| `/logs` | Show today's logs for an instance — choose instance via inline buttons |
| `/code <instance> <code>` | Submit an authenticator code to a waiting login process |
| `/backup` | Force a Wallet backup — guided by inline buttons (monthly/yearly) |
| `/backup monthly [YYYY-MM]` | Monthly backup, optional period (default: previous month) |
| `/backup yearly [YYYY]` | Yearly backup, optional year (default: previous year) |
| `/status` | Show all instances and whether backup is available |
| `/help` | Show available commands |

Backup commands are only available when `backup.wallet_api_key` is set in `instances.yml`.

### Setup

Add the `telegram-bot` service to your `docker-compose.yml`:

```yaml
name: tr-sync

services:
  telegram-bot:
    image: ghcr.io/sanmibuh/tr-wallet-sync:<tag>
    entrypoint: ["python", "-m", "app", "bot"]
    volumes:
      - ./instances.yml:/app/config/instances.yml:ro
      - tr-data:/app/data
    restart: unless-stopped
```

> **Security:** The bot only responds to messages from `telegram_chat_id`. All other chats are silently ignored.

> **How it works:** The bot dispatches all operations (sync, login, resync, backup) as direct in-process Python calls — no Docker socket required.

---

## tr-sync.sh reference

The `tr-sync.sh` script is the main management tool for NAS deployments where `make` is not available.

```sh
./tr-sync.sh pull      [service]           # pull image(s) — omit for all
./tr-sync.sh bootstrap <instance-name>     # interactive 2FA login
./tr-sync.sh sync      <instance-name>     # one-shot sync
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
cp deploy/local/instances.yml.template deploy/local/instances.yml
# fill in deploy/local/instances.yml

make test                        # run test suite with coverage
make lint                        # run ruff linter
make run-sync INSTANCE=user1     # one-shot sync
make run-backup                  # one-shot backup auto
make run-bot                     # start the Telegram bot
make clean                       # remove __pycache__ and .pytest_cache
```

---

## NAS deployment

See [deploy/DEPLOY.md](deploy/DEPLOY.md) for the full step-by-step deploy guide.

See [ARCHITECTURE.md](ARCHITECTURE.md) for full technical details.
