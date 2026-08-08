# Trade Republic → BudgetBakers Wallet Sync

Automatically syncs your Trade Republic transactions into [BudgetBakers Wallet](https://budgetbakers.com/) as records. Runs as a Docker container — either as a one-shot command or as a scheduled daemon with a built-in cron.

Supports multiple accounts, Telegram notifications, and automatic deduplication so re-running never creates duplicate records.

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
      CRON_SCHEDULE: "0 8 * * *"         # every day at 08:00 local time
      TELEGRAM_BOT_TOKEN: "<bot_token>"  # optional
      TELEGRAM_CHAT_ID: "<chat_id>"      # optional
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
| `CRON_SCHEDULE` | — | 5-field cron expression. If unset, runs once and exits. |
| `TELEGRAM_BOT_TOKEN` | — | Telegram bot token for sync notifications |
| `TELEGRAM_CHAT_ID` | — | Telegram chat ID for sync notifications |
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

## Multiple accounts

Add one service per account in `docker-compose.yml`. Use a 5-minute offset on the cron schedule to avoid hitting the BudgetBakers API simultaneously:

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
      CRON_SCHEDULE: "0 8 * * *"
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
      CRON_SCHEDULE: "5 8 * * *"    # 5-minute offset
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
| `build` | Build the app image (assumes base exists) |
| `build-all` | Full rebuild — base + app, no cache |
| `bootstrap` | Interactive first-time login |
| `sync` | One-shot sync run (ignores `CRON_SCHEDULE`) |
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
./tr-sync.sh sync      myaccount   # manual one-shot
./tr-sync.sh down      myaccount
```

See `ARCHITECTURE.md` for full technical details.
