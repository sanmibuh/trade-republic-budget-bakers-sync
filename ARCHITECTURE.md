# Architecture

Technical reference for developers and AI assistants. Covers module design, data flow, key decisions, and deployment context.

---

## Module structure

```
app/
  __main__.py       # CLI entry point — click group with `sync`, `backup`, and `bot` subcommands
  config.py         # Config dataclass — reads all env vars in one place
  persistence.py    # EventRepository (SQLite dedup)
  tr_mapper.py      # TR event → BudgetBakers record mapping
  tr_client.py      # TRClient — pytr wrapper: login, 2FA, timeline fetch
  wallet_client.py  # WalletClient — BudgetBakers HTTP API (POST records + GET backup)
  backup.py         # Backup logic: auto / monthly / yearly modes
  notifier.py       # Notifier — Telegram notifications (transversal)
  bot.py            # TelegramBot — long-polling bot for remote command execution
  logging_setup.py  # Rotating file + console logging; configure_logging() for CLIs
  main.py           # Sync orchestrator: wires all modules, minimal logic

docker/
  base/Dockerfile   # python:3.11-slim + git + docker CLI + pip deps; published as python-trade-republic
  app/Dockerfile    # installs cron, copies app code + entrypoint.sh
  app/entrypoint.sh # MODE=sync|backup|bot; one-shot if schedule not set
```

---

## Data flow — Sync

```
TRClient.fetch_timeline_events()
  └── pytr Timeline (WebSocket) → event_callback collects events + details
        ↓
filter_by_lookback()         # drops events older than LOOKBACK_DAYS
        ↓
build_records_for_event()    # TR event dict → list[BudgetBakers record dict]
  └── _HANDLERS[event_type]  # per-type handler builds the record
  └── label applied generically post-handler if LABEL_<EVENT_TYPE> is set
        ↓
EventRepository.dedup_event_id()   # filters already-synced events (SQLite)
        ↓
WalletClient.post_records()        # POST /v1/api/records (max 20 per request)
        ↓
EventRepository.mark_processed()   # INSERT OR IGNORE into processed_events
        ↓
Notifier.sync_complete()           # Telegram summary (optional)
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
- `EventRepository` is a context manager (`with EventRepository(...) as repo`) — encapsulates SQLite connection lifetime.
- `TRClient` keeps the pytr client as internal state — callers never touch pytr directly.
- `tr_mapper` uses a handler registry `_HANDLERS: dict[str, Callable]` — adding a new TR event type requires only a handler function + one line in `_HANDLERS`; no existing logic changes (Open/Closed principle).

### pytr integration
- `TRClient.fetch_timeline_events` uses `pytr.timeline.Timeline` with an `event_callback`.
- Python dict mutation by reference: `details` dict is populated inside `collected` after `tl_loop()` completes — no explicit merging needed.
- `pytr` requires interactive 2FA on first login (push notification or authenticator code). Session is persisted to `/app/data` and reused automatically.

### Deduplication
- `processed_events` table in SQLite: `(event_id, event_type, event_timestamp, amount, raw, synced_at)`.
- `INSERT OR IGNORE` — idempotent. Re-running never creates duplicate records in BudgetBakers.
- Old records without `details` are not retroactively updated (correct by design).

### Labels
- `LABEL_<EVENT_TYPE>` env vars (e.g. `LABEL_BANK_TRANSACTION_INCOMING`) are read by `_read_label_ids()` → `cfg.label_ids: dict[str, str]`.
- `LABELABLE_EVENT_TYPES` tuple in `config.py` lists all 13 supported types.
- Labels are applied generically in `build_records_for_event` post-handler — individual handlers don't know about labels.
- `_make_record` accepts `label_ids: list[str] | None`; BudgetBakers API expects `labelIds` as a list.

### CLI entry point (`app/__main__.py`)
- Single entry point via `python -m app` using **click** with three subcommands:
  - `python -m app sync` — runs `main.run()`
  - `python -m app backup <mode> [param]` — dispatches to `backup.run_auto/run_monthly/run_yearly`
  - `python -m app bot` — starts the Telegram bot
- All imports inside command functions are deferred — startup is fast and dependencies are only loaded when needed.
- `click.Choice(["auto", "monthly", "yearly"])` provides free input validation and help text.
- `entrypoint.sh` and `tr-sync.sh` both use `python -m app <subcommand>` — single consistent interface.

### Scheduled daemon
- `docker/app/entrypoint.sh`: behaviour controlled by `MODE` env var:
  - `MODE=sync` — registers `SYNC_SCHEDULE` cron job (`python -m app sync`). One-shot if `SYNC_SCHEDULE` not set.
  - `MODE=backup` — registers `BACKUP_SCHEDULE` cron job (`python -m app backup auto`). One-shot if `BACKUP_SCHEDULE` not set.
  - `MODE=bot` — starts the Telegram bot (`python -m app bot`).
  - `CMD=<command>` — overrides all modes, runs a one-shot command and exits.
- `TZ` env var must be set in the container for cron to interpret hours in local time (default is UTC).

### Backup strategy
- **`auto` mode**: designed for daily cron. Always overwrites current + previous month. In February, generates the yearly backup for the previous year if it does not yet exist (idempotent).
- **`monthly` / `yearly` modes**: explicit, always execute, intended for manual runs and backfill.
- Files are plain JSON, permanent, never purged (unlike `sync.db` which purges after 60 days).
- Yearly cleanup removes the 12 monthly files whose period is covered by the yearly backup.

### Telegram bot
- `app/bot.py`: long-polling bot dispatching commands to Docker containers via `docker exec`.
- `BotConfig` reads `INSTANCES` (sync instances), `CONTAINER_PREFIX`, and `BACKUP_SERVICE` from env.
- `CONTAINER_PREFIX` must match the Docker Compose project `name:` field (e.g. `tr-sync`) — fixed in `docker-compose.yml` via `name: tr-sync` so it never changes regardless of the directory name.
- Container naming: sync → `{prefix}-sync-{name}-1`, backup → `{prefix}-{backup_service}-1`.
- Backup commands (`/backup_monthly`, `/backup_yearly`) execute directly on the backup container — no instance picker.
- Sync commands (`/sync`) show an inline keyboard to pick the instance.
- The Docker socket (`/var/run/docker.sock`) is required for `docker exec`.
- Base URL: `{base_url}/v1/api/{resource}`
- Pagination via `nextOffset` in the response dict; plain list responses have no pagination.
- `_get_all()` handles both response shapes: plain `list` (no pagination) and `{"data": [...], "nextOffset": N}`.
- `_collect_page()` is a `@staticmethod` extracted from `_get_all` to keep cognitive complexity ≤ 15: appends items from a paginated dict page into the results list and returns the next offset.

### OWNER_NAME
- `OWNER_NAME` is optional — defaults to `"Backup"` when not set.
- Sync services (`sync-david`, `sync-eli`) set it explicitly for per-owner notifications.
- The backup service omits it; notifications show `"Backup"` as the owner.

### BudgetBakers API — POST (sync)
- `POST /v1/api/records` — max 20 records per request.
- `paymentType` is required on every record.
- `labelIds` is a list (even for a single label).
- `verify=False` + `InsecureRequestWarning` suppressed (self-signed cert on some deployments).

### Known limitations
- TR CSV export has raw terminal descriptors (e.g. `"ETAM LINGERIE BUENOS A"`) but the pytr WebSocket API only exposes normalized merchant names (e.g. `"Etam"`). No fix possible without combining CSV + API sources.

---

## Docker images

| Image | Base | Published on |
|---|---|---|
| `ghcr.io/sanmibuh/python-trade-republic` | `python:3.11-slim` + git + docker CLI + pip deps | `v3.0.0` |
| `ghcr.io/sanmibuh/tr-wallet-sync` | `python-trade-republic` + app code + cron | Minor/patch releases |

`cron` is intentionally installed only in the app image (`docker/app/Dockerfile`), not in the base image — it is an app concern.

Must rebuild the app image after any code change. Build and push are automated via GitHub Actions:
- **Major release** (`vX.0.0`): rebuilds both `python-trade-republic` (base) and `tr-wallet-sync` (app).
- **Minor / patch release** (`vX.Y.Z`): rebuilds only `tr-wallet-sync`.

Releases are triggered automatically by bumping the `VERSION` file and pushing to `main` — the `release.yml` workflow creates the tag and GitHub release, which in turn triggers the publish workflows.

---

## Data volume

`/app/data` (mounted from host) contains:
- `sync.db` — SQLite database with `processed_events` table (purged after 60 days)
- `sync.log` — rotating log file
- pytr session/cookie files (login state)
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

When working with an AI assistant: **always ask for tests before implementation**, or provide the tests yourself and ask the assistant to make them pass.

---

## Test suite

323 tests across 11 files — all passing.

```
tests/test_config.py          # Config.from_env, OWNER_NAME default, _required_env
tests/test_persistence.py     # EventRepository, dedup_event_id, mark_processed, purge
tests/test_tr_mapper.py       # _HANDLERS, IBAN extraction, label_ids, filter_by_lookback, _gross_tax_note
tests/test_wallet_client.py   # post_records batching; _get_all + _collect_page pagination branches
tests/test_main.py            # _fetch_events (all error branches), _build_batch, _process_results, run()
tests/test_backup.py          # date helpers, _parse_monthly/yearly_param, run_monthly/yearly/auto
tests/test_notifier.py        # all notification types including backup_complete with filename
tests/test_bot.py             # BotConfig.from_env, TelegramBot commands and callback handling
tests/test_cli.py             # click CLI: help, sync, backup subcommands via CliRunner
tests/test_logging_setup.py   # setup_logging, configure_logging (idempotency)
tests/test_tr_client.py       # TRClient login, fetch, error branches
```

Run:
```bash
make test
```

---

## Relevant files (quick reference)

| File | Role |
|---|---|
| `app/__main__.py` | click CLI: `sync`, `backup`, and `bot` subcommands; single entry point |
| `app/main.py` | Sync orchestrator; passes `cfg.label_ids` to `build_records_for_event` |
| `app/backup.py` | Backup logic: `run_auto`, `run_monthly`, `run_yearly`; `_parse_monthly/yearly_param` |
| `app/tr_client.py` | `TRClient` with `event_callback`; no module-level functions |
| `app/tr_mapper.py` | `_HANDLERS`, `_ZERO_AMOUNT_TYPES`, `KNOWN_EVENT_TYPES`, `_make_record` |
| `app/persistence.py` | `EventRepository`, `dedup_event_id`; `INSERT OR IGNORE` |
| `app/config.py` | `Config` dataclass; `label_ids: dict[str, str]`; `_read_label_ids()` |
| `app/wallet_client.py` | `post_records` (sync) + `_get_all`/`_collect_page` + `get_*` (backup) |
| `app/logging_setup.py` | `setup_logging(data_dir)` for daemon; `configure_logging()` for CLI entry points |
| `docker/app/entrypoint.sh` | `MODE=sync\|backup\|bot`; cron or one-shot depending on schedule vars |
| `VERSION` | Single source of truth for the release version; bumping it on `main` triggers the full release + build pipeline |
