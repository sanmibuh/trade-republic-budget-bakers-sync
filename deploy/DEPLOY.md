# Deploy guide

Step-by-step guide for deploying on a NAS (QNAP, Synology, etc.) and running locally for development.

---

## Local development (no Docker)

Use `make` to run sync, backup, or bot commands locally against real credentials.

### Setup

```sh
cp deploy/local/local.env.template deploy/local/local.env
# fill in WALLET_API_KEY and any other vars you need
```

`deploy/local/local.env` is git-ignored. `deploy/local/data/` (written by local runs) is also ignored.

### Run commands

```sh
make run-backup                        # backup auto
make run-backup-yearly  PARAM=2025     # backup specific year
make run-backup-monthly PARAM=2026-07  # backup specific month
make run-sync                          # one-shot sync
make run-bot                           # start Telegram bot
```

---

## Folder structure

```
deploy/
  DEPLOY.md           # this guide
  local/              # local dev environment
    local.env.template  # template — copy to local.env and fill in values
    local.env           # real credentials (never committed)
    data/               # written by local runs (never committed)
  example/            # example config files (no secrets)
    docker-compose.yml
    common.env.example
    sync-1.env.example
    sync-2.env.example
  nas/
    current/          # next version — rename to vXXX after deploy
      docker-compose.yml
      common.env      # real credentials (never committed)
      sync-1.env      # per-account credentials (never committed)
      sync-2.env
      tr-sync.sh      # management script
    v201/             # v2.0.1 — deployed, read-only
      ...
```

`current/` is always the work in progress. After deploying, rename it with the release tag (`v300`, `v310`, etc.) as a read-only snapshot.

---

## First install from scratch

### 1. Copy files to the NAS

Copy the contents of `deploy/example/` to a folder on the NAS (e.g. `/share/docker/tr-sync/`).

### 2. Fill in credentials

Rename the example files and fill in real values:

```sh
cp common.env.example  common.env
cp sync-1.env.example  sync-1.env
cp sync-2.env.example  sync-2.env
```

- `common.env` — Telegram bot token, chat ID, Wallet API key
- `sync-1.env` — Trade Republic phone and PIN for account 1
- `sync-2.env` — Trade Republic phone and PIN for account 2

Also update `docker-compose.yml` with the real `WALLET_CASH_ACCOUNT_ID` and `WALLET_PORTFOLIO_ACCOUNT_ID` for each account.

### 3. Bootstrap (one-time interactive 2FA login)

```sh
./tr-sync.sh bootstrap sync-1
./tr-sync.sh bootstrap sync-2
```

Approve the push notification in the Trade Republic app (or enter the authenticator code). The session is saved to the data volume and reused automatically.

### 4. Start all services

```sh
./tr-sync.sh up
```

Or start individually:

```sh
./tr-sync.sh up sync-1
./tr-sync.sh up sync-2
./tr-sync.sh up backup
./tr-sync.sh up telegram-bot
```

---

## Deploying a new version

### 1. Update the image tag in `current/docker-compose.yml`

```yaml
image: ghcr.io/sanmibuh/tr-wallet-sync:v3.1.0
```

### 2. Copy updated files to the NAS

Copy `docker-compose.yml` and `tr-sync.sh` from `current/` to the NAS folder. Leave `.env` files untouched.

### 3. Upgrade on the NAS

```sh
cd /share/docker/tr-sync

./tr-sync.sh upgrade          # pull + down + up for all services
```

Or per service if you want more control:

```sh
./tr-sync.sh upgrade sync-1
./tr-sync.sh upgrade telegram-bot
```

### 4. Rename `current/` with the release tag

```sh
mv deploy/nas/current deploy/nas/v310
```

Then copy the new version contents into a fresh `current/` when preparing the next release.

---

## Day-to-day operations

```sh
./tr-sync.sh logs  sync-1        # follow logs in real time
./tr-sync.sh sync  sync-1        # force a manual one-shot sync
./tr-sync.sh down  sync-1        # stop a service
./tr-sync.sh up    sync-1        # start a service
./tr-sync.sh down                # stop all services
./tr-sync.sh up                  # start all services
./tr-sync.sh logs  telegram-bot  # follow bot logs
```

---

## Manual backups

```sh
./tr-sync.sh backup auto             # smart backup (current + previous month)
./tr-sync.sh backup monthly 2026-07  # specific month
./tr-sync.sh backup yearly  2025     # specific year
```

---

## Session expired (re-login)

```sh
./tr-sync.sh bootstrap sync-1   # renew 2FA login for account 1
```

---

## Production schedules

Defined in `docker-compose.yml` under each service's `environment:`.

| Service      | SYNC_SCHEDULE       | BACKUP_SCHEDULE |
|--------------|---------------------|-----------------|
| sync-1       | `0 8,14,21 * * *`   | —               |
| sync-2       | `5 8,14,21 * * *`   | —               |
| backup       | —                   | `0 3 * * *`     |
| telegram-bot | —                   | —               |
