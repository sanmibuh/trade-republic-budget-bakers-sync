# Architecture

Technical reference for developers and AI assistants. Covers module design, data flow, key decisions,
and deployment context.

> **Maintenance rule (for AI assistants and contributors):** update this file whenever a module is added, renamed, or
> removed; a key design decision changes; the SQLite schema changes; or a new workflow is introduced.
> Keep it in sync with the code — stale docs are worse than no docs.

---


## Data flow — Sync

```
TRClient.fetch_timeline_events()
  └── pytr Timeline (WebSocket) → event_callback collects events + details
        ↓
filter_by_lookback()         # drops events older than LOOKBACK_DAYS
        ↓
build_records_for_event()    # TR event dict → list[BudgetBakers record dict]
   └── _build_note()          # single source of truth for the note/description
   └── _HANDLERS[event_type]  # per-type handler builds record structure (accounts, payment type, counter-party)
   └── label applied generically post-handler if LABEL_<EVENT_TYPE> is set
   └── category_id applied generically post-handler when CATEGORY_STRATEGY=history
         ↓
HistoryCategorizer.get_category_id(note)   # majority-vote lookup from recent Wallet records
   └── CategoryCache.category_ids()         # 24h TTL wrapper around WalletClient.get_categories()
        ↓
EventRepository.dedup_event_id()   # filters already-synced events (SQLite)
        ↓
WalletClient.post_records()        # POST /v1/api/records (max 20 per request)
        ↓
EventRepository.mark_processed()   # INSERT OR IGNORE into processed_events
        ↓
Notifier.sync_complete()           # Telegram summary (optional)
```

## Data flow — Force Resync (single day)

```
python -m app resync YYYY-MM-DD
        ↓
SyncRunner.resync_day(date_str, repo, wallet_client)
        ↓
TRClient.fetch_timeline_events(since=date 00:00)
        ↓
filter_by_lookback(events, since, until=date+1 00:00)  # narrow to exact day
        ↓
build_records_for_event()    # same mapper as regular sync
        ↓
For each event:
  ├── wallet_record_id in DB?
  │     YES → WalletClient.put_record(id, record)   # PUT /v1/api/records/{id}
  │     NO  → WalletClient.post_records([record])   # POST /v1/api/records
        ↓
EventRepository.mark_processed_force()   # INSERT OR REPLACE (upsert)
        ↓
Notifier.sync_complete()                 # Telegram summary (optional)
```

## Data flow — Backup

```
python -m app backup <mode> [param]
        ↓
WalletClient.get_accounts/categories/budgets/labels/records()
  └── _get_all() — paginates via nextOffset until exhausted
        └── _collect_page() — extracts items from paginated dict response
        ↓
_fetch_snapshot()   # assembles payload dict with all resources + metadata
        ↓
_write_json()       # writes to data_dir/backups/{monthly|yearly}/wallet-{mode}-{period}.json
        ↓
run_yearly() only: removes covered wallet-monthly-{year}-*.json files
        ↓
Notifier.backup_complete()  # Telegram summary with filename (optional)
```

---

## Key design decisions

### OOP / SOLID
- `Notifier` holds `bot_token`, `chat_id`, `owner_name` as state — methods have no repetitive parameters.
- `EventRepository` is a context manager (`with EventRepository(...) as repo`) — encapsulates SQLite
  connection lifetime.
- `TRClient` keeps the pytr client as internal state — callers never touch pytr directly.
- `tr_mapper` uses a handler registry `_HANDLERS: dict[str, Callable]` and a single `_build_note()` function —
  adding a new TR event type requires only a handler (if the record structure differs) + one line in `_HANDLERS`;
  note logic is added to `_build_note` / `_note_extras` independently (Open/Closed principle).

### pytr integration
- `TRClient.fetch_timeline_events` uses `pytr.timeline.Timeline` with an `event_callback`.
- Python dict mutation by reference: `details` dict is populated inside `collected` after `tl_loop()` completes —
  no explicit merging needed.
- `pytr` requires interactive 2FA on first login (push notification or authenticator code).
  Session is persisted to `/app/data` and reused automatically.
- Trade Republic web-login sessions have a hard **24h cap** (`tr_refresh` cookie `exp = iat + 86400`);
  `GET /api/v1/auth/web/session` only rotates the short-lived `tr_session`, never the refresh token,
  so a session cannot be extended past 24h — re-authentication is unavoidable.
- `TRClient.connect` obtains the authenticator code from a `code_provider` (see `app/twofa.py`) instead of calling
  `input()` directly. `select_code_provider` picks the strategy: a TTY (interactive bootstrap,
  `docker compose run -it`) → `TerminalCodeProvider` (stdin); no TTY but Telegram configured →
  `TelegramCodeProvider`; neither → `None`, and `connect` raises `SessionExpiredError`.
  `main._fetch_events` maps that to `notifier.authentication_required()` and exits cleanly instead of crashing on
  `EOFError` and re-hitting the login endpoint every run (429 ban risk). Push-approval accounts (Eli, no
  authenticator) still complete automatically in cron if approved in the app in time.

### 2FA via Telegram
- `app/twofa.py` provides the authenticator-code strategies and `select_code_provider`.
- `TelegramCodeProvider` sends a Telegram prompt (`notifier.login_code_request(instance)`) asking the user to reply
  with `/code <instance> <code>`, then polls `data_dir/.tr_2fa_code` (default 300s timeout, 3s poll) until the code
  file appears; it clears the file before prompting and after reading. While actively waiting it creates a
  `data_dir/.tr_2fa_pending` marker; that marker is removed on success or timeout. On expiry, it calls `on_timeout`
  (if set) — wired by `select_code_provider` to `notifier.login_code_timeout(instance)`, which sends a cancellation
  message in Telegram — then raises `TimeoutError`.
- `submit-code` (`python -m app submit-code <code>`) checks for `.tr_2fa_pending` before writing the code file; if
  the marker is absent (no login in progress), it exits 1 with "No active login request for this instance" so stale
  `/code` submissions are rejected cleanly.
- Cross-container hand-off: in the legacy multi-container setup the bot and the sync containers do **not** share
  the data volume. The `/code` bot command runs `python -m app submit-code <code>` inside the target container via
  the Docker SDK `exec_run`. In the **single-container deployment** (Phase 4 of #145) all services share the same
  process space and data volume — the bot writes the code file directly, no Docker SDK needed.
- On-demand renewal: `/login` (bot) → `python -m app login` → `main.run_login()` triggers the 2FA flow explicitly.
  Scheduled cron syncs that hit an expired session trigger the same flow automatically (Eli via push, David via
  `/code`).
- `run()` (sync) and `run_login()` share `_prepare(cfg)` (data dir + SSL circuit-breaker + logging + `Notifier`)
  as a module-level bootstrap helper.
- Sync orchestration logic lives in the `SyncRunner` class (`sync_runner.py`). Its public methods — `connect`,
  `fetch_events`, `build_batch`, `process_results` — accept `cfg` and `notifier` injected via the constructor,
  making dependencies explicit and easy to mock. `run()` and `run_login()` are thin wrappers that call `_prepare`,
  construct a `SyncRunner`, and delegate to it. `main.py` re-exports `SyncRunner`, `_SyncCounts`, `_Batch`, and
  `AuthenticationError` for backward compatibility.

### Deduplication
- `processed_events` table in SQLite:
  `(event_id, event_type, event_timestamp, amount, raw, synced_at, wallet_record_id)`.
- `INSERT OR IGNORE` — idempotent. Re-running never creates duplicate records in BudgetBakers.
- `wallet_record_id` stores the Wallet API record ID returned by `post_records` on success. For events that produce
  multiple records (e.g. investment with cash + portfolio split), IDs are stored comma-separated. NULL for
  zero-amount excluded events. Enables insert-vs-update decisions when reprocessing a date range.
- Schema migrations are applied automatically on each `EventRepository` open via `PRAGMA table_info` +
  `ALTER TABLE` — safe to run repeatedly, no migration state needed.
- `EventRepository.mark_processed_force` — `INSERT OR REPLACE` upsert variant; used by the resync path to
  update `wallet_record_id` for already-processed events.
- Old records without `details` are not retroactively updated (correct by design).

---

## Database schema

### `sync.db` — dedup database (`app/persistence.py`)

```sql
CREATE TABLE processed_events (
    event_id         TEXT PRIMARY KEY,   -- TR native ID or hash:<sha256> fallback
    event_type       TEXT NOT NULL DEFAULT '',
    event_timestamp  TEXT NOT NULL DEFAULT '',
    amount           TEXT NOT NULL DEFAULT '',
    raw              TEXT NOT NULL DEFAULT '',  -- full TR event JSON for auditing
    synced_at        TEXT NOT NULL,             -- UTC ISO timestamp of sync
    wallet_record_id TEXT  -- Wallet API record ID(s); comma-separated for multi-record events; NULL for excluded
);

CREATE INDEX idx_synced_at ON processed_events (synced_at);

CREATE TABLE auth_state (
    instance    TEXT PRIMARY KEY,   -- logical instance name (e.g. "david")
    status      TEXT NOT NULL,      -- 'ok' | 'failed' | 'expired'
    updated_at  TEXT NOT NULL       -- UTC ISO timestamp of last status change
);
```

**TTL**: `processed_events` records older than 60 days are purged on each sync run (`purge_old_records`).
`auth_state` rows are upserted on every connect — one row per instance, no TTL.

**Migrations**: applied automatically on `EventRepository` open — new columns are added via `ALTER TABLE` if
missing, detected with `PRAGMA table_info`; new tables are created via `CREATE TABLE IF NOT EXISTS`.

### Labels
- Per-instance `label_ids` are read from the YAML (`labels:` map under each instance) by `_parse_instance_labels()` →
  `cfg.label_ids: dict[str, str]`.
- `LABELABLE_EVENT_TYPES` tuple in `config.py` lists all 13 supported types.
- Labels are applied generically in `build_records_for_event` post-handler — individual handlers don't
  know about labels.
- `_make_record` builds the base record dict; labels are applied generically in `build_records_for_event`
  post-handler via `labelIds` (list, even for a single label).

### CLI entry point (`app/__main__.py`)
- Single entry point via `python -m app` using **click** with subcommands:
  - `python -m app sync` — runs `main.run()`
  - `python -m app sync --instance <name>` — resolves config from `INSTANCES_CONFIG_PATH` YAML and runs sync for that instance
  - `python -m app backup <mode> [param]` — dispatches to `backup.run_auto/run_monthly/run_yearly`
  - `python -m app bot` — starts the Telegram bot
   - `python -m app login` — runs `main.run_login()`, an on-demand 2FA session renewal
   - `python -m app submit-code <code>` — writes the authenticator code to `data_dir/.tr_2fa_code` for a waiting
     login/sync process to pick up
   - `python -m app resync YYYY-MM-DD` — runs `main.run_resync(date_str)`, force re-syncing a specific day
   - `python -m app list-instances` — prints all instance names from `INSTANCES_CONFIG_PATH`, one per line.
  - `python -m app list-schedules` — prints `name<TAB>schedule` for every instance that has a schedule,
    one per line. Used by `entrypoint.sh` to register one cron job per instance with its own schedule.
  - `python -m app get-backup-schedule` — prints the `backup_schedule` from `INSTANCES_CONFIG_PATH`, or nothing.
    Used by `entrypoint.sh` to conditionally register the backup cron job.
- All imports inside command functions are deferred — startup is fast and dependencies are only loaded when needed.
- `click.Choice(["auto", "monthly", "yearly"])` provides free input validation and help text.
- `entrypoint.sh` and `tr-sync.sh` both use `python -m app <subcommand>` — single consistent interface.

### Scheduled daemon
- `docker/app/entrypoint.sh`: reads `instances.yml` at `/app/config/instances.yml` (hardcoded path):
  - Calls `python -m app list-schedules` to get per-instance `name<TAB>schedule` pairs.
  - Registers one cron job per instance with its own schedule: `python -m app sync --instance <name>`.
  - Calls `python -m app get-backup-schedule`; if a schedule is returned, registers a backup cron job.
  - Starts the cron daemon in the background (`cron -f &`), then starts the Telegram bot also in
    the background (`python -m app bot &`). The shell remains as PID 1 and supervises both children:
    if either process exits unexpectedly the shell kills the other and exits non-zero so Docker can
    restart the container. A `SIGTERM`/`SIGINT` trap ensures both children are stopped cleanly on
    `docker stop`.
  - All instances share one log file: `{DATA_DIR}/sync.log`.
  - Instance data lives under `{DATA_DIR}/sync/{instance_name}/`.
  - Exits with an error if no instance has a schedule defined.
- `TZ` env var must be set in the container for cron to interpret hours in local time (default is UTC).

### Backup strategy
- **`auto` mode**: designed for daily cron. Always overwrites current + previous month. In February, generates the
  yearly backup for the previous year if it does not yet exist (idempotent).
- **`monthly` / `yearly` modes**: explicit, always execute, intended for manual runs and backfill.
- Files are plain JSON, permanent, never purged (unlike `sync.db` which purges after 60 days).
- Yearly cleanup removes the 12 monthly files whose period is covered by the yearly backup.

### Telegram bot
- `app/bot.py`: long-polling bot and command handlers; wires `app/bot_keyboards.py`; executes all sync/login/resync/backup operations via direct in-process Python calls (no Docker SDK, no `exec_run`).
- `app/bot_keyboards.py`: stateless inline keyboard builder functions (backup type/period pickers, instance pickers, resync date picker); no dependency on bot state.
- `BotConfig` reads `telegram_bot_token` and `telegram_chat_id` from `instances.yml`. `INSTANCES_CONFIG_PATH` (hardcoded) is the path to the instances YAML file; backup config is derived from `BackupConfig.from_instances_yaml()`.
- Each sync instance is represented as `InstanceConfig(name, config: Config)` — the bot calls `main.run()`, `main.run_login()`, and `main.run_resync()` directly with the instance's `Config`.
- Backup command (`/backup [monthly|yearly] [period]`) uses a two-step inline keyboard: first choose type
  (Monthly / Yearly), then choose the period. Direct args (`/backup monthly 2026-07`) skip the keyboards.
- Sync/login/resync commands (`/sync`, `/login`, `/resync`) show an inline instance picker.
- `/status` reports auth state per instance — ✅ session valid, ⚠️ needs login, ❓ DB unreadable (corrupted/locked) — by reading `sync.db` directly
  via `EventRepository` and checking cookie expiry via `has_valid_session` (`_check_session_direct`).
- `/login` renews an expired 2FA session on demand (instance picker); a 6-digit code sent as a plain message is
  forwarded to the target instance's `data_dir/.tr_2fa_code` file (see *2FA via Telegram*).
- `/resync [YYYY-MM-DD]` force re-syncs a specific day: instance picker → date picker (last 7 days) → executes
  `main.run_resync()` in a background thread. With a date arg, jumps straight to the instance picker.
  Callback data format: `resync_pick_date:<instance>` (date picker step) and `resync:<date>:<instance>` (execute step).

### Config
- All configuration is read exclusively from `instances.yml` (mounted at `INSTANCES_CONFIG_PATH`).
- No environment variables are read for credentials or operational config — only `TZ` (timezone for cron) is used at the OS level.
- `INSTANCES_CONFIG_PATH` — module-level constant in `config.py`; hardcoded to `/app/config/instances.yml`.

### Backup config derivation

`BotConfig.from_env` builds `backup_cfg` from `instances.yml`:

1. `BackupConfig` is derived from the first instance's `wallet_api_key`; `data_dir` and Telegram
   credentials are taken from the YAML global fields.
2. **No instances in YAML** → `backup_cfg` is `None` and `/backup` commands are disabled.

The `backup` CLI command (`python -m app backup`) loads config via `_resolve_backup_cfg()`:
- Loads `InstancesConfig` from the hardcoded `INSTANCES_CONFIG_PATH` and derives `BackupConfig` from it.
- Any YAML validation or I/O error surfaces as a `click.UsageError`.

### Multi-instance YAML config (#145, #162)

`InstancesConfig` (`app/config.py`) supports loading N sync instances from a single YAML file,
enabling a single-container deployment without per-instance Docker services. The bot (`app/bot.py`)
reads this file from `INSTANCES_CONFIG_PATH` (hardcoded to `/app/config/instances.yml`) and dispatches
all operations (sync, login, resync, backup) as direct in-process Python calls.

**File format** (`instances.yml`):

```yaml
# Global shared settings
data_dir: /app/data           # optional, default /app/data
telegram_bot_token: "..."     # required
telegram_chat_id: "..."       # required
allow_insecure_ssl: false     # optional, default false

backup_schedule: "0 3 * * *"  # optional — cron expression; omit to disable automatic backups

sync:
  schedule: "0 8,14,21 * * *" # global default; overridable per instance
  wallet_api_key: "..."        # global default; overridable per instance
  lookback_days: 7             # global default; overridable per instance
  category_strategy: history   # global default (none | history); overridable per instance

  instances:
    - name: user1              # used as subdirectory name and instance identifier
      phone: "+34600000000"
      pin: "1234"
      wallet_cash_account_id: "..."
      wallet_portfolio_account_id: "..."
      owner_name: "User1"      # optional, defaults to name.capitalize()
      dedup_ttl_days: 60       # optional, default 60
      labels:                  # optional
        BANK_TRANSACTION_INCOMING: label-id-123
      # Per-instance overrides (all optional):
      # wallet_api_key: "..."
      # lookback_days: 14
      # category_strategy: none
      # schedule: "5 8,14,21 * * *"  # stagger to avoid parallel TR logins

    - name: user2
      phone: "+34611111111"
      pin: "5678"
      wallet_cash_account_id: "..."
      wallet_portfolio_account_id: "..."
```

The `sync:` section is **required** — files without it raise `ValueError` with a clear message
pointing to `instances.yml.example`.

**Key behaviours:**
- Each instance gets its own `data_dir/sync/{name}/` subdirectory (session files, `sync.db`).
- Global `telegram_*` and `allow_insecure_ssl` are inherited by all instances.
- `sync.*` fields (`wallet_api_key`, `lookback_days`, `category_strategy`, `schedule`) are global
  defaults; each instance can override any of them individually.
- `InstancesConfig.to_config(name)` returns a fully populated `Config` ready for `run()`.
- `InstancesConfig.sync_schedule` — the global schedule (or `None`); individual instances expose it
  via `InstanceConfig.schedule` after inheritance resolution at load time.
- `InstancesConfig.backup_schedule` — the backup cron expression (or `None`).
- `run(cfg)` and `run_login(cfg)` require an injected `Config`; there is no fallback to env-var based construction.
- CLI: `python -m app sync --instance david` and `python -m app login --instance david` resolve
  config from `INSTANCES_CONFIG_PATH` (`/app/config/instances.yml`).
- Validation on load: missing required fields, duplicate names, and invalid `category_strategy`
  all raise `ValueError` with a descriptive message.

### OWNER_NAME
- `OWNER_NAME` is optional — defaults to `"Backup"` when not set.
- Sync services (`sync-david`, `sync-eli`) set it explicitly for per-owner notifications.
- The backup service omits it; notifications show `"Backup"` as the owner.

### Logging (`app/logging_setup.py`)
- `setup_logging(log_dir)` — called **once at process startup** by each CLI entry point; sets up rotating file handler + console handler. Returns `None`.
  - **Bot process**: called in the `bot` CLI command with `instances_yaml.data_dir` → all in-process sync/login/resync/backup calls share a single `{DATA_DIR}/sync.log`.
  - **Standalone sync / login**: called with `instances_yaml.data_dir` when `--instance` is used.
  - **Standalone resync**: called with `cfg.data_dir` (always env-var driven, root data dir).
  - **Standalone backup**: called with `cfg.data_dir` (root data dir).
  - **Short-lived commands** (`submit-code`, `check-pending`, `list-instances`): do not call `setup_logging` — they run without any logging configuration.
- `configure_logging()` — minimal console-only fallback; not called by any CLI command.
- Because logging is configured once at startup and never torn down, `_prepare` in `main.py` needs no handler lifecycle management — there is no handler accumulation risk between in-process calls.
- The `/logs` Telegram command reads today's lines from the shared `{DATA_DIR}/sync.log` directly (no instance picker — all instances write to the same file).

### SSL circuit-breaker (`app/http_client.py`)
- `SSLCircuitBreaker` — class that encapsulates circuit state (`verify`, `allow_insecure`) and policy.
  - `configure(allow_insecure_ssl)` — sets the policy and resets any previously tripped state.
  - `open()` — trips the breaker (idempotent, logs a one-time warning).
  - `verify` property — current boolean state (`True` = verify certificates).
  - `allow_insecure` property — whether the circuit is allowed to open on `SSLError`.
- `breaker` — module-level singleton instance shared across all HTTP calls.
- `configure(allow_insecure_ssl)` — module-level convenience that delegates to `breaker.configure()`.
  Called once at startup from `main.run()` and the `backup` CLI command.
- `http_post(url, **kwargs)` — wraps `requests.post`; on `SSLError`, only falls back to `verify=False` when
  `breaker.allow_insecure` is `True`. Otherwise the error propagates.
- `build_session(headers)` — returns a `requests.Session` with a custom `_SSLCircuitBreakerAdapter` that applies
  the same fallback logic per-request using the shared `breaker` singleton.
- Both `notifier.py` (Telegram) and `wallet_client.py` (BudgetBakers) use this module — the singleton is shared.
- Controlled via `allow_insecure_ssl` in `instances.yml` (default `false`). Set to `true` only in environments with broken
  certificate chains (e.g. corporate VPN).

### BudgetBakers API — POST (sync)
- `POST /v1/api/records` — max 20 records per request.
- `paymentType` is required on every record.
- `labelIds` is a list (even for a single label).

### BudgetBakers API — PUT (resync)
- `PUT /v1/api/records/{id}` — update a single existing record.
- Used by the forced resync path when an event already has a stored `wallet_record_id`.
- Returns the updated record dict; errors propagate via `raise_for_status`.

### Known limitations
- TR CSV export has raw terminal descriptors (e.g. `"ETAM LINGERIE BUENOS A"`) but the pytr WebSocket API only
  exposes normalized merchant names (e.g. `"Etam"`). No fix possible without combining CSV + API sources.

### Auto-categorization (`category_strategy`)

Records sent via the BudgetBakers API land without a category (Wallet's AI categorization only activates for
bank-connected syncs).  The `category_strategy` YAML field enables automatic pre-assignment:

| `category_strategy` | Behaviour |
|---|---|
| `none` (default) | No automatic categorization |
| `history` | Enables history-based lookup via `HistoryCategorizer` |

> `llm` is planned as a future strategy (LLM as fallback) but is **not implemented yet** — setting it
> will raise `ValueError` at startup.

**`history` strategy flow** (implemented in `app/categorizer.py`):
1. On the first record of a sync run, `HistoryCategorizer` fetches all Wallet records for the last
   `history_days` (default 90) days and builds an in-memory `note → [categoryId, ...]` index.
2. Only `categoryId` values present in the current `CategoryCache` (valid, non-deleted categories)
   are indexed.
3. For each new record, the most frequent `categoryId` among the last `top_n` (default 5) matches
   is assigned.  Falls back to no category if there are no matches.
4. `CategoryCache` wraps `WalletClient.get_categories()` with a 24 h TTL to avoid repeated API calls.
   Call `invalidate()` to force a reload on the next access.

The `categoryId` is stamped onto records inside `SyncRunner.build_batch` via `_apply_category`
(after `build_records_for_event` returns, same phase as `labelIds`).  The `HistoryCategorizer` is
constructed once per `build_batch` call and reused across all events in that batch.

If the Wallet API rejects a record due to an invalid `categoryId` (e.g. a category was deleted after
the cache loaded), `SyncRunner._retry_category_failures` invalidates the category cache and retries
the affected records once without a `categoryId`.

`CATEGORY_STRATEGY` is validated in `config.py`; unknown values raise `ValueError` at startup.

---

## Docker images

| Image | Base | Published on |
|---|---|---|
| `ghcr.io/sanmibuh/python-trade-republic` | `python:3.11-slim` + git + pip deps | `v5.0.0` |
| `ghcr.io/sanmibuh/tr-wallet-sync` | `python-trade-republic` + app code + cron | Minor/patch releases |

`cron` is intentionally installed only in the app image (`docker/app/Dockerfile`), not in the base image — it is
an app concern.

Must rebuild the app image after any code change. Build and push are automated via GitHub Actions:
- **Major release** (`vX.0.0`): rebuilds both `python-trade-republic` (base) and `tr-wallet-sync` (app).
- **Minor / patch release** (`vX.Y.Z`): rebuilds only `tr-wallet-sync`.

Releases are triggered automatically by bumping the `VERSION` file and pushing to `main` — the `release.yml`
workflow creates the tag and GitHub release, which in turn triggers the publish workflows.

### Release workflow

`prepare-release.yml` — disponible desde GitHub Actions UI o `gh workflow run`. Muestra un desplegable para elegir
el tipo de bump. No requiere introducir la versión manualmente — se calcula sola desde `VERSION`:

| Bump | Ejemplo (`6.1.0` → ) |
|---|---|
| `patch` — bug fixes, tweaks | `6.1.1` |
| `minor` — nuevas features, backwards-compatible | `6.2.0` |
| `major` — breaking changes, rebuild de imagen base | `7.0.0` |

El workflow:
1. Calcula la siguiente versión.
2. Actualiza `VERSION`.
3. Abre un PR `release-{version}` → `main` listo para revisar y mergear.

Merging the PR triggers `release.yml`, which creates the tag and GitHub Release, which in turn triggers the Docker
image publish workflows.

---

## Data volume

`/app/data` (mounted from host) contains:
- `sync/{name}/sync.db` — SQLite database per instance with `processed_events` and `auth_state` tables
- `sync.log` — rotating log file shared by all services (bot, sync, backup); written to `{DATA_DIR}/sync.log`
- `sync/{name}/` — pytr session/cookie files per instance (login state)
- `sync/{name}/.tr_2fa_pending` — transient marker created by `TelegramCodeProvider` while waiting for a code
- `sync/{name}/.tr_2fa_code` — transient file where `submit-code` drops the authenticator code
- `backups/monthly/` — monthly JSON snapshots (permanent)
- `backups/yearly/` — yearly JSON snapshots (permanent)

---

## Test-Driven Development (TDD)

All new features and bug fixes follow a TDD workflow:

1. **Write the test first** — define the expected behaviour before writing any implementation.
2. **Watch it fail** — confirm the test fails for the right reason (not a setup error).
3. **Implement** — write the minimum code to make the test pass.
4. **Refactor** — clean up with tests as the safety net.

This ensures:
- Every code path has a test that actually caught a real failure at some point.
- Tests document intent, not just current behaviour.
- Coverage stays meaningful — no tests written after the fact just to hit a number.

When working with an AI assistant: **always ask for tests before implementation**, or provide the tests yourself
and ask the assistant to make them pass.

---

## Tests

Run:
```bash
make test
```

Local one-shot runs (requires `deploy/local/local.env`):
```bash
make run-backup                        # backup auto
make run-backup-yearly  PARAM=2025     # specific year
make run-backup-monthly PARAM=2026-07  # specific month
make run-sync                          # one-shot sync
make run-bot                           # Telegram bot
```

See `deploy/DEPLOY.md` for setup instructions.
