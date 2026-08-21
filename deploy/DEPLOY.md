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

### In this repository (`deploy/`)

```
deploy/
  DEPLOY.md           # this guide
  local/              # local dev environment
    local.env.template  # template — copy to local.env and fill in values
    local.env           # real credentials (never committed)
    data/               # written by local runs (never committed)
  example/            # example config files (no secrets)
    docker-compose.yml
    instances.yml.example
    tr-sync.sh
```

### On the NAS (not in git)

The NAS folder (e.g. `/share/docker/tr-sync/`) is managed outside git.
Its layout mirrors the versioned snapshots kept there:

```
/share/docker/tr-sync/    # example NAS path — adjust to your setup
  current/                # work in progress — rename to vXXX after deploy
    docker-compose.yml
    instances.yml         # all credentials (never committed to git)
    tr-sync.sh            # management script
  v201/                   # v2.0.1 — deployed, read-only snapshot
    ...
```

`current/` is always the work in progress. After deploying, rename it with the release tag (`v300`, `v710`, etc.) as a read-only snapshot.

---

## First install from scratch

### 1. Copy files to the NAS

Copy `docker-compose.yml`, `tr-sync.sh`, and `instances.yml.example` from `deploy/example/` to a folder on the NAS
(e.g. `/share/docker/tr-sync/`).

### 2. Create `instances.yml`

Copy the example and fill in real values:

```sh
cp instances.yml.example instances.yml
```

Edit `instances.yml` with your credentials:

- `telegram_bot_token` / `telegram_chat_id` — Telegram bot credentials (global, shared by all instances)
- Per instance: Trade Republic phone, PIN, and BudgetBakers Wallet API key + account IDs

See `instances.yml.example` for the full format.

### 3. Create the data directory

```sh
mkdir -p data
```

### 4. Bootstrap — first-time interactive 2FA login

```sh
./tr-sync.sh bootstrap user1
./tr-sync.sh bootstrap user2
```

Approve the push notification in the Trade Republic app (or enter the authenticator code).
The session is saved to the data volume and reused automatically.

### 5. Start the container

```sh
./tr-sync.sh up
```

---

## Deploying a new version

### 1. Update the image tag in `current/docker-compose.yml`

```yaml
image: ghcr.io/sanmibuh/tr-wallet-sync:v3.1.0
```

### 2. Copy updated files to the NAS

Copy `docker-compose.yml` and `tr-sync.sh` from `current/` to the NAS folder.
Leave `instances.yml` untouched.

### 3. Upgrade on the NAS

```sh
cd /share/docker/tr-sync

./tr-sync.sh upgrade
```

### 4. Rename `current/` with the release tag

```sh
mv deploy/nas/current deploy/nas/v310
```

Then copy the new version contents into a fresh `current/` when preparing the next release.

---

## Day-to-day operations

```sh
./tr-sync.sh logs              # follow container logs
./tr-sync.sh sync user1        # force a manual one-shot sync for instance "user1"
./tr-sync.sh down              # stop the container
./tr-sync.sh up                # start the container
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

Trade Republic sessions expire on a hard 24h cap, so a scheduled sync will eventually fail with a session-expired error. Two ways to renew:

**From Telegram (no SSH needed):**

1. Send `/login` to the bot and pick the instance (or wait for the automatic prompt when a cron sync bails out).
2. The bot replies asking for the authenticator code. Reply with the 6-digit code as a plain message.
   Push-approval accounts (no authenticator) just approve in the app — no code needed.
3. If multiple logins are pending simultaneously (unlikely), the bot will ask you to disambiguate.
   In that case use the explicit form: `/code <instance> <code>` — e.g. `/code user1 123456`.

**From the NAS (interactive bootstrap):**

```sh
./tr-sync.sh bootstrap user1   # renew 2FA login for instance "user1"
```

---

## Production schedules

Defined in `instances.yml` — **not** in `docker-compose.yml` environment variables.

| Key in `instances.yml`    | Example value       | Purpose                                          |
|---------------------------|---------------------|--------------------------------------------------|
| `sync.schedule`           | `0 8,14,21 * * *`   | Global default sync schedule (overridable per instance) |
| `backup_schedule`         | `0 3 * * *`         | Automatic daily backup (omit to disable)         |

`SYNC_SCHEDULE` and `BACKUP_SCHEDULE` environment variables are **ignored** in
multi-instance mode; set schedules in `instances.yml` instead.

---

## Migration notes

### Consolidating runtime data under `data/` (v8.0.0+)

Runtime paths have changed. Run the following from the compose root (`/share/docker/tr-sync/`) before upgrading:

**Instance data** — moved from `data/<name>/` to `data/sync/<name>/`:

```sh
# From /share/docker/tr-sync/
mkdir -p data/sync
# Repeat for each instance (david, eli, …)
mv data/david data/sync/david
mv data/eli   data/sync/eli
```

**Logs** — moved from `logs/` at the compose root into `data/`:

```sh
# From /share/docker/tr-sync/
mv logs/sync.log* data/
rmdir logs   # only if empty
```

**Backups** — moved from `data/backup/backups/` to `data/backups/`:

```sh
# From /share/docker/tr-sync/
mkdir -p data/backups
mv data/backup/backups/* data/backups/
rmdir data/backup/backups data/backup   # only if empty
```

