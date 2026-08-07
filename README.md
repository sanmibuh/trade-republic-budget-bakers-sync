# Trade Republic → BudgetBakers Wallet Sync

Dockerized Python service that runs a one-shot sync from Trade Republic (`pytr`) into BudgetBakers Wallet records.

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

## Quickstart (Docker Compose)

### 1. Create your `docker-compose.yml`

> `docker-compose.yml` is gitignored — never commit secrets.

```yaml
services:
  username1:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    environment:
      OWNER_NAME: "username1"
      PHONE_NUMBER: "+49123456789"
      PIN: "<your_tr_pin>"
      WALLET_API_KEY: "<wallet_api_key>"
      WALLET_CASH_ACCOUNT_ID: "<cash_account_id>"
      WALLET_PORTFOLIO_ACCOUNT_ID: "<portfolio_account_id>"
      LOOKBACK_DAYS: "7"
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

### 3. Run a sync

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
| `sync` | One-shot sync run |
| `test` | Run the test suite |
| `clean` | Remove `__pycache__` and `.pytest_cache` |

## Building from source

If you prefer to build locally instead of pulling from ghcr:

```bash
make build-base          # builds python-trade-republic:latest
make build SERVICE=username1
make sync  SERVICE=username1
```

## Periodic execution (cron)

Example crontab entry to run every day at 06:00:

```cron
0 6 * * * cd /path/to/trade-republic-budget-bakers-sync && make sync SERVICE=username1 >> /var/log/tr-sync-username1.log 2>&1
```

Or with plain Docker if you prefer not to use Make:

```cron
0 6 * * * docker run --rm --env-file /path/to/username1.env \
  -v /path/to/username1/data:/app/data \
  ghcr.io/sanmibuh/tr-wallet-sync:latest >> /var/log/tr-sync-username1.log 2>&1
```

## Multiple accounts

Add one service per account in `docker-compose.yml`:

```yaml
services:
  username1:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    environment:
      OWNER_NAME: "username1"
      PHONE_NUMBER: "${USERNAME1_PHONE:?required}"
      PIN: "${USERNAME1_PIN:?required}"
      WALLET_API_KEY: "${USERNAME1_WALLET_API_KEY:?required}"
      WALLET_CASH_ACCOUNT_ID: "${USERNAME1_CASH_ID:?required}"
      WALLET_PORTFOLIO_ACCOUNT_ID: "${USERNAME1_PORTFOLIO_ID:?required}"
    volumes:
      - ./username1/data:/app/data

  username2:
    image: ghcr.io/sanmibuh/tr-wallet-sync:latest
    environment:
      OWNER_NAME: "username2"
      PHONE_NUMBER: "${USERNAME2_PHONE:?required}"
      PIN: "${USERNAME2_PIN:?required}"
      WALLET_API_KEY: "${USERNAME2_WALLET_API_KEY:?required}"
      WALLET_CASH_ACCOUNT_ID: "${USERNAME2_CASH_ID:?required}"
      WALLET_PORTFOLIO_ACCOUNT_ID: "${USERNAME2_PORTFOLIO_ID:?required}"
    volumes:
      - ./username2/data:/app/data
```

Then bootstrap and sync each independently:

```bash
make bootstrap SERVICE=username1
make bootstrap SERVICE=username2

make sync SERVICE=username1
make sync SERVICE=username2
```
