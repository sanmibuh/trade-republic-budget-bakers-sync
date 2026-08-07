# Trade Republic → BudgetBakers Wallet Sync

Dockerized Python service that runs a one-shot sync from Trade Republic (`pytr`) into BudgetBakers Wallet records.

## Architecture

- `app/main.py` — orchestration entrypoint; reads env vars, fetches timeline events, applies lookback and dedup, writes CSV backup.
- `app/tr_client.py` — Trade Republic client bootstrap/login and timeline retrieval fallback.
- `app/wallet_client.py` — BudgetBakers API client and Trade Republic event → Wallet record mapping.
- `app/notifier.py` — Telegram notification helper for authentication/session renewal alerts.
- `/app/data` (mounted volume) — persistent `pytr` session/cookies + SQLite dedup DB (`processed_events.db`).
- `/app/output` (mounted volume) — monthly append-only CSV backups (`TradeRepublic_<OWNER_NAME>_<YYYY-MM>.csv`).

## Required environment variables

- `OWNER_NAME`
- `PHONE_NUMBER`
- `WALLET_API_KEY`
- `WALLET_CASH_ACCOUNT_ID`
- `WALLET_PORTFOLIO_ACCOUNT_ID`

Optional:

- `LOOKBACK_DAYS` (default: `7`)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Build and run (single account)

```bash
docker build -t tr-wallet-sync .

docker run --rm \
  -e OWNER_NAME="username1" \
  -e PHONE_NUMBER="+49123456789" \
  -e WALLET_API_KEY="<wallet_api_key>" \
  -e WALLET_CASH_ACCOUNT_ID="<cash_account_id>" \
  -e WALLET_PORTFOLIO_ACCOUNT_ID="<portfolio_account_id>" \
  -e LOOKBACK_DAYS="7" \
  -e TELEGRAM_BOT_TOKEN="<telegram_bot_token>" \
  -e TELEGRAM_CHAT_ID="<telegram_chat_id>" \
  -v "$(pwd)/data/username1:/app/data" \
  -v "$(pwd)/output/username1:/app/output" \
  tr-wallet-sync
```

## Trade Republic session/token bootstrap

`pytr` stores the authenticated Trade Republic session in `/app/data` (mounted from your host).  
For a first-time setup (or after session expiry), run the container interactively so login/2FA can be completed and persisted:

```bash
docker run --rm -it \
  -e OWNER_NAME="username1" \
  -e PHONE_NUMBER="+49123456789" \
  -e WALLET_API_KEY="<wallet_api_key>" \
  -e WALLET_CASH_ACCOUNT_ID="<cash_account_id>" \
  -e WALLET_PORTFOLIO_ACCOUNT_ID="<portfolio_account_id>" \
  -v "$(pwd)/data/username1:/app/data" \
  -v "$(pwd)/output/username1:/app/output" \
  tr-wallet-sync
```

After successful authentication, keep using the same `./data/username1` mount so the saved session/token can be reused in non-interactive runs.

## Periodic execution (cron)

Example crontab entry to run every day at 06:00:

```cron
0 6 * * * cd /absolute/path/to/trade-republic-budget-bakers-sync && /usr/bin/docker run --rm -e OWNER_NAME=username1 -e PHONE_NUMBER=+49123456789 -e WALLET_API_KEY=<wallet_api_key> -e WALLET_CASH_ACCOUNT_ID=<cash_account_id> -e WALLET_PORTFOLIO_ACCOUNT_ID=<portfolio_account_id> -e LOOKBACK_DAYS=7 -v /absolute/path/to/trade-republic-budget-bakers-sync/data/username1:/app/data -v /absolute/path/to/trade-republic-budget-bakers-sync/output/username1:/app/output tr-wallet-sync >> /var/log/tr-wallet-sync-username1.log 2>&1
```

If you use Docker Compose locally, place your compose file in your infrastructure folder and schedule `docker compose -f /path/to/your-compose.yml run --rm <service-name>` instead.

## Docker Compose examples

`docker-compose.yml` is intentionally not committed. You can create your own compose file depending on how many accounts you run.

### Example: single account

```yaml
services:
  tr-sync-username1:
    build: .
    environment:
      OWNER_NAME: "username1"
      PHONE_NUMBER: "${USERNAME1_PHONE_NUMBER:?USERNAME1_PHONE_NUMBER is required}"
      WALLET_API_KEY: "${USERNAME1_WALLET_API_KEY:?USERNAME1_WALLET_API_KEY is required}"
      WALLET_CASH_ACCOUNT_ID: "${USERNAME1_WALLET_CASH_ACCOUNT_ID:?USERNAME1_WALLET_CASH_ACCOUNT_ID is required}"
      WALLET_PORTFOLIO_ACCOUNT_ID: "${USERNAME1_WALLET_PORTFOLIO_ACCOUNT_ID:?USERNAME1_WALLET_PORTFOLIO_ACCOUNT_ID is required}"
      TELEGRAM_BOT_TOKEN: "${USERNAME1_TELEGRAM_BOT_TOKEN:-}"
      TELEGRAM_CHAT_ID: "${USERNAME1_TELEGRAM_CHAT_ID:-}"
      LOOKBACK_DAYS: "7"
    volumes:
      - ./data/username1:/app/data
      - ./output/username1:/app/output
```

### Example: multiple accounts

```yaml
services:
  tr-sync-username1:
    build: .
    environment:
      OWNER_NAME: "username1"
      PHONE_NUMBER: "${USERNAME1_PHONE_NUMBER:?USERNAME1_PHONE_NUMBER is required}"
      WALLET_API_KEY: "${USERNAME1_WALLET_API_KEY:?USERNAME1_WALLET_API_KEY is required}"
      WALLET_CASH_ACCOUNT_ID: "${USERNAME1_WALLET_CASH_ACCOUNT_ID:?USERNAME1_WALLET_CASH_ACCOUNT_ID is required}"
      WALLET_PORTFOLIO_ACCOUNT_ID: "${USERNAME1_WALLET_PORTFOLIO_ACCOUNT_ID:?USERNAME1_WALLET_PORTFOLIO_ACCOUNT_ID is required}"
      TELEGRAM_BOT_TOKEN: "${USERNAME1_TELEGRAM_BOT_TOKEN:-}"
      TELEGRAM_CHAT_ID: "${USERNAME1_TELEGRAM_CHAT_ID:-}"
      LOOKBACK_DAYS: "7"
    volumes:
      - ./data/username1:/app/data
      - ./output/username1:/app/output

  tr-sync-username2:
    build: .
    environment:
      OWNER_NAME: "username2"
      PHONE_NUMBER: "${USERNAME2_PHONE_NUMBER:?USERNAME2_PHONE_NUMBER is required}"
      WALLET_API_KEY: "${USERNAME2_WALLET_API_KEY:?USERNAME2_WALLET_API_KEY is required}"
      WALLET_CASH_ACCOUNT_ID: "${USERNAME2_WALLET_CASH_ACCOUNT_ID:?USERNAME2_WALLET_CASH_ACCOUNT_ID is required}"
      WALLET_PORTFOLIO_ACCOUNT_ID: "${USERNAME2_WALLET_PORTFOLIO_ACCOUNT_ID:?USERNAME2_WALLET_PORTFOLIO_ACCOUNT_ID is required}"
      TELEGRAM_BOT_TOKEN: "${USERNAME2_TELEGRAM_BOT_TOKEN:-}"
      TELEGRAM_CHAT_ID: "${USERNAME2_TELEGRAM_CHAT_ID:-}"
      LOOKBACK_DAYS: "7"
    volumes:
      - ./data/username2:/app/data
      - ./output/username2:/app/output
```
