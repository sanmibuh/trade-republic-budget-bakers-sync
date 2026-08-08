# Trade Republic → BudgetBakers Wallet Sync

Dockerized Python service that syncs Trade Republic transactions into BudgetBakers Wallet records. Supports one-shot execution or a built-in scheduled daemon via `CRON_SCHEDULE`.

## Architecture

```
app/
  config.py         # Config dataclass — reads all env vars in one place
  persistence.py    # EventRepository (SQLite dedup)
  tr_mapper.py      # TR event → BudgetBakers record mapping; filter_by_lookback
  tr_client.py      # TRClient class — pytr wrapper: login, 2FA, timeline fetch
  wallet_client.py  # WalletClient class — BudgetBakers HTTP (POST /v1/api/records)
  notifier.py       # Notifier class — Telegram notifications (transversal)
  logging_setup.py  # Rotating file + console logging (transversal)
  main.py           # Orchestrator: wires all modules together, minimal logic
```

Key design decisions:

- **`Notifier`** holds `bot_token`, `chat_id`, `owner_name` — methods have no repetitive parameters
- **`EventRepository`** encapsulates the SQLite connection as a context manager (`with EventRepository(...) as repo`)
- **`TRClient`** keeps the pytr client as internal state — callers never touch it directly
- **`tr_mapper` registry** — adding a new TR event type requires only a handler function + one line in `_HANDLERS`; no existing logic changes (Open/Closed)

**Data volumes (mounted from host):**

- `/app/data` — pytr session/cookies + SQLite DB (`sync.db`) + `sync.log`

**Docker images:**

- `ghcr.io/sanmibuh/python-trade-republic` — base image (Python 3.11 + system deps + pip packages). Published on major releases.
- `ghcr.io/sanmibuh/tr-wallet-sync` — app image. Published on minor/patch releases.

## Required environment variables

| Variable | Description |
|---|---|
| `OWNER_NAME` | Display name used in logs and Telegram messages |
| `PHONE_NUMBER` | Trade Republic account phone number (e.g. `+49123456789`) |
| `PIN` | Trade Republic account PIN |
| `WALLET_API_KEY` | BudgetBakers Wallet API key |
| `WALLET_CASH_ACCOUNT_ID` | BudgetBakers cash account UUID |
| `WALLET_PORTFOLIO_ACCOUNT_ID` | BudgetBakers portfolio account UUID |

Optional:

| Variable | Default | Description |
|---|---|---|
| `LOOKBACK_DAYS` | `7` | How many days back to fetch events |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for notifications |
| `CRON_SCHEDULE` | — | Cron expression for scheduled mode (e.g. `0 8 * * *`). If unset, runs once and exits. |
| `LABEL_<EVENT_TYPE>` | — | BudgetBakers label UUID to apply to records of that event type (e.g. `LABEL_BANK_TRANSACTION_INCOMING`, `LABEL_CARD_TRANSACTION`). All optional. |

Supported event types for `LABEL_*`:
`BANK_TRANSACTION_INCOMING`, `BANK_TRANSACTION_OUTGOING`, `CARD_TRANSACTION`,
`INTEREST_PAYOUT`, `INTEREST_PAYMENT`, `BUY_ORDER`, `SELL_ORDER`, `SAVINGS_PLAN`,
`TRADING_SAVINGSPLAN_EXECUTED`, `SAVEBACK_AGGREGATE`, `SPARE_CHANGE_AGGREGATE`,
`SAVEBACK`, `PAYMENT_INBOUND`.

## Execution modes

### One-shot

The container runs a single sync and exits. Suitable for being triggered externally (e.g. from a host cron, CI, or manually).

```bash
make sync SERVICE=username1
```

### Scheduled daemon

Set `CRON_SCHEDULE` to a standard 5-field cron expression. The container stays running and executes the sync at the configured times.

```yaml
environment:
  CRON_SCHEDULE: "0 8 * * *"      # every day at 08:00
  # CRON_SCHEDULE: "0 8,20 * * *" # every day at 08:00 and 20:00
```

```bash
make up   SERVICE=username1   # start daemon
make logs SERVICE=username1   # follow logs
make down SERVICE=username1   # stop daemon
```

This mode is the recommended approach for NAS deployments (QNAP, Synology, etc.) where an external cron cannot easily invoke Docker.

## Quickstart (Docker Compose)

### 1. Create your `docker-compose.yml`

> `docker-compose.yml` is gitignored — never commit secrets.

```yaml
services:
  username1:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    restart: unless-stopped
    environment:
      OWNER_NAME: "username1"
      PHONE_NUMBER: "+49123456789"
      PIN: "<your_tr_pin>"
      WALLET_API_KEY: "<wallet_api_key>"
      WALLET_CASH_ACCOUNT_ID: "<cash_account_id>"
      WALLET_PORTFOLIO_ACCOUNT_ID: "<portfolio_account_id>"
      LOOKBACK_DAYS: "7"
      CRON_SCHEDULE: "0 8 * * *"
      TELEGRAM_BOT_TOKEN: "<telegram_bot_token>"
      TELEGRAM_CHAT_ID: "<telegram_chat_id>"
    volumes:
      - ./username1/data:/app/data
```

### 2. First-time login (interactive 2FA)

`pytr` needs an interactive login the first time (or after session expiry):

```bash
make bootstrap SERVICE=username1
```

Approve the push notification in your Trade Republic app (or enter the authenticator code).
The session is saved to `./username1/data` and reused in future runs.

### 3. Start the daemon

```bash
make up SERVICE=username1
```

Or for a manual one-shot run without the daemon:

```bash
make sync SERVICE=username1
```

## Makefile targets

```
make <target> SERVICE=<name>
```

`SERVICE` is required for all targets except `build-base`, `test`, and `clean`.

| Target | Description |
|---|---|
| `build-base` | Build the base Docker image (`python-trade-republic`) |
| `build` | Build the app image (assumes base exists) |
| `build-all` | Full rebuild — base + app, no cache |
| `bootstrap` | Interactive first-time login |
| `sync` | One-shot sync run (ignores `CRON_SCHEDULE`) |
| `up` | Start as scheduled daemon (uses `CRON_SCHEDULE` from `docker-compose.yml`) |
| `down` | Stop the daemon |
| `logs` | Follow daemon logs |
| `test` | Run the test suite |
| `clean` | Remove `__pycache__` and `.pytest_cache` |

## Building from source

If you prefer to build locally instead of pulling from ghcr:

```bash
make build-base          # builds python-trade-republic:latest
make build SERVICE=username1
make up    SERVICE=username1
```

## Multiple accounts

Add one service per account in `docker-compose.yml`:

```yaml
services:
  username1:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    restart: unless-stopped
    environment:
      OWNER_NAME: "username1"
      PHONE_NUMBER: "${USERNAME1_PHONE:?required}"
      PIN: "${USERNAME1_PIN:?required}"
      WALLET_API_KEY: "${USERNAME1_WALLET_API_KEY:?required}"
      WALLET_CASH_ACCOUNT_ID: "${USERNAME1_CASH_ID:?required}"
      WALLET_PORTFOLIO_ACCOUNT_ID: "${USERNAME1_PORTFOLIO_ID:?required}"
      CRON_SCHEDULE: "0 8 * * *"
    volumes:
      - ./username1/data:/app/data

  username2:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    restart: unless-stopped
    environment:
      OWNER_NAME: "username2"
      PHONE_NUMBER: "${USERNAME2_PHONE:?required}"
      PIN: "${USERNAME2_PIN:?required}"
      WALLET_API_KEY: "${USERNAME2_WALLET_API_KEY:?required}"
      WALLET_CASH_ACCOUNT_ID: "${USERNAME2_CASH_ID:?required}"
      WALLET_PORTFOLIO_ACCOUNT_ID: "${USERNAME2_PORTFOLIO_ID:?required}"
      CRON_SCHEDULE: "0 8 * * *"
    volumes:
      - ./username2/data:/app/data
```

Bootstrap and manage each independently:

```bash
make bootstrap SERVICE=username1
make bootstrap SERVICE=username2

make up   SERVICE=username1
make up   SERVICE=username2
```
