# Trade Republic → BudgetBakers Wallet Sync

Automatically syncs your Trade Republic transactions into [BudgetBakers Wallet](https://budgetbakers.com/) as records. Runs as a Docker container — either as a one-shot command or as a scheduled daemon with a built-in cron.

Supports multiple accounts, Telegram notifications, and automatic deduplication so re-running never creates duplicate records.

Also includes a **wallet backup** feature that saves full JSON snapshots of your BudgetBakers data (accounts, categories, budgets, labels, records) on a daily schedule.

---

## Quickstart

### 1. Create your `docker-compose.yml`

> `docker-compose.yml` is gitignored — never commit secrets.

```yaml
services:
  myaccount:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    restart: unless-stopped
    environment:
      OWNER_NAME: "Alice"
      PHONE_NUMBER: "+49123456789"
      PIN: "<your_tr_pin>"
      WALLET_API_KEY: "<wallet_api_key>"
      WALLET_CASH_ACCOUNT_ID: "<cash_account_id>"
      WALLET_PORTFOLIO_ACCOUNT_ID: "<portfolio_account_id>"
      LOOKBACK_DAYS: "7"
      TZ: "Europe/Berlin"
      SYNC_SCHEDULE: "0 8 * * *"          # every day at 08:00 local time
      BACKUP_SCHEDULE: "0 3 * * *"        # every day at 03:00 local time (optional)
      TELEGRAM_BOT_TOKEN: "<bot_token>"   # optional
      TELEGRAM_CHAT_ID: "<chat_id>"       # optional
    volumes:
      - ./myaccount:/app/data
```

### 2. First-time login (interactive 2FA)

`pytr` requires an interactive login the first time (or after session expiry):

```bash
make bootstrap SERVICE=myaccount
```

Approve the push notification in your Trade Republic app (or enter the authenticator code). The session is saved to `./myaccount/` and reused in all future runs.

### 3. Start the daemon

```bash
make up SERVICE=myaccount
```

Or run a single sync manually:

```bash
make sync SERVICE=myaccount
```

---

## Environment variables

### Required

| Variable | Description |
|---|---|
| `OWNER_NAME` | Display name used in logs and Telegram messages |
| `PHONE_NUMBER` | Trade Republic account phone number (e.g. `+49123456789`) |
| `PIN` | Trade Republic account PIN |
| `WALLET_API_KEY` | BudgetBakers Wallet API key |
| `WALLET_CASH_ACCOUNT_ID` | BudgetBakers cash account UUID |
| `WALLET_PORTFOLIO_ACCOUNT_ID` | BudgetBakers portfolio account UUID |

### Optional

| Variable | Default | Description |
|---|---|---|
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

## Wallet backup

When `BACKUP_SCHEDULE` is set, the container runs a daily backup in **`auto` mode**:

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

```bash
make backup SERVICE=myaccount MODE=auto           # same as the scheduled job
make backup SERVICE=myaccount MODE=monthly        # previous month
make backup SERVICE=myaccount MODE=monthly PARAM=2026-07
make backup SERVICE=myaccount MODE=yearly         # previous year
make backup SERVICE=myaccount MODE=yearly PARAM=2025
```

The container exposes a unified CLI via `python -m app`:

```
python -m app --help
python -m app sync
python -m app backup auto
python -m app backup monthly [YYYY-MM]
python -m app backup yearly  [YYYY]
python -m app bot              # start the Telegram remote-control bot
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
| `/status` | Show all instances and whether backup is available for each |
| `/help` | Show available commands |

Backup commands are only available when `BACKUP_SERVICE` is configured in the bot's environment. The bot runs all backup operations on the dedicated backup container — not per sync instance.

### Setup

Add the `telegram-bot` service to your `docker-compose.yml`:

```yaml
name: my-project

services:
  telegram-bot:
    image: ghcr.io/sanmibuh/tr-wallet-sync:<tag>
    entrypoint: ["python", "-m", "app", "bot"]
    environment:
      TELEGRAM_BOT_TOKEN: "<bot_token>"
      TELEGRAM_CHAT_ID: "<your_chat_id>"
      # Comma-separated list of sync instance names defined below
      INSTANCES: "alice,bob"
      # Must match the Docker Compose project name (name: field above)
      CONTAINER_PREFIX: "my-project"
      # Name of the backup service (omit or leave empty to disable backup commands)
      BACKUP_SERVICE: "backup"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    restart: unless-stopped

  sync-alice:
    image: ghcr.io/sanmibuh/tr-wallet-sync:<tag>
    environment:
      MODE: sync
      OWNER_NAME: "Alice"
      # ... rest of alice config

  sync-bob:
    image: ghcr.io/sanmibuh/tr-wallet-sync:<tag>
    environment:
      MODE: sync
      OWNER_NAME: "Bob"
      # ... rest of bob config

  backup:
    image: ghcr.io/sanmibuh/tr-wallet-sync:<tag>
    environment:
      MODE: backup
      BACKUP_SCHEDULE: "0 3 * * *"
      # ... rest of backup config
```

Start the bot:

```bash
docker compose up -d telegram-bot
```

> **Security:** The bot only responds to messages from `TELEGRAM_CHAT_ID`. All other chats are silently ignored.

> **How it works:** The bot uses `docker exec` to run commands inside the target containers. The container's own Notifier then sends the result notification to Telegram, just like a scheduled run would.

---

## Multiple accounts

Add one service per account in `docker-compose.yml`. Use a 5-minute offset on the sync schedule to avoid hitting the BudgetBakers API simultaneously:

```yaml
services:
  alice:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    restart: unless-stopped
    environment:
      OWNER_NAME: "Alice"
      PHONE_NUMBER: "+49123456789"
      PIN: "<alice_pin>"
      WALLET_API_KEY: "<alice_wallet_api_key>"
      WALLET_CASH_ACCOUNT_ID: "<alice_cash_id>"
      WALLET_PORTFOLIO_ACCOUNT_ID: "<alice_portfolio_id>"
      TZ: "Europe/Berlin"
      SYNC_SCHEDULE: "0 8 * * *"
      BACKUP_SCHEDULE: "0 3 * * *"
    volumes:
      - ./alice:/app/data

  bob:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    restart: unless-stopped
    environment:
      OWNER_NAME: "Bob"
      PHONE_NUMBER: "+49987654321"
      PIN: "<bob_pin>"
      WALLET_API_KEY: "<bob_wallet_api_key>"
      WALLET_CASH_ACCOUNT_ID: "<bob_cash_id>"
      WALLET_PORTFOLIO_ACCOUNT_ID: "<bob_portfolio_id>"
      TZ: "Europe/Berlin"
      SYNC_SCHEDULE: "5 8 * * *"       # 5-minute offset
      BACKUP_SCHEDULE: "30 3 * * *"    # 30-minute offset
    volumes:
      - ./bob:/app/data
```

Bootstrap and manage each independently:

```bash
make bootstrap SERVICE=alice
make bootstrap SERVICE=bob

make up SERVICE=alice
make up SERVICE=bob
```

---

## Makefile targets

```
make <target> SERVICE=<name>
```

`SERVICE` is required for all targets except `build-base`, `test`, and `clean`.

| Target | Description |
|---|---|
| `build-base` | Build the base Docker image (`python-trade-republic`) |
| `build` | Build the app image — includes cron and docker CLI |
| `build-all` | Full rebuild — base + app, no cache |
| `bootstrap` | Interactive first-time login |
| `sync` | One-shot sync run (ignores `SYNC_SCHEDULE`) |
| `backup` | One-shot backup — requires `MODE=auto\|monthly\|yearly` and optional `PARAM=` |
| `up` | Start as scheduled daemon |
| `down` | Stop the daemon |
| `logs` | Follow daemon logs |
| `test` | Run the test suite |
| `clean` | Remove `__pycache__` and `.pytest_cache` |

---

## Building from source

If you prefer to build locally instead of pulling from ghcr:

```bash
make build-base
make build   SERVICE=myaccount
make up      SERVICE=myaccount
```

---

## NAS deployment (QNAP, Synology, etc.)

For NAS deployments where `make` is not available, use the included `tr-sync.sh` script instead:

```bash
./tr-sync.sh pull      myaccount
./tr-sync.sh bootstrap myaccount
./tr-sync.sh up        myaccount
./tr-sync.sh logs      myaccount
./tr-sync.sh sync      myaccount          # manual one-shot sync
./tr-sync.sh backup    myaccount auto     # manual backup (auto mode)
./tr-sync.sh backup    myaccount monthly 2026-07
./tr-sync.sh backup    myaccount yearly 2025
./tr-sync.sh down      myaccount
```

Internally these run `python -m app <subcommand>` inside the container.

See `ARCHITECTURE.md` for full technical details.
