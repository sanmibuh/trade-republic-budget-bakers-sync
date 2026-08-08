# Architecture

Technical reference for developers and AI assistants. Covers module design, data flow, key decisions, and deployment context.

---

## Module structure

```
app/
  config.py         # Config dataclass — reads all env vars in one place
  persistence.py    # EventRepository (SQLite dedup)
  tr_mapper.py      # TR event → BudgetBakers record mapping
  tr_client.py      # TRClient — pytr wrapper: login, 2FA, timeline fetch
  wallet_client.py  # WalletClient — BudgetBakers HTTP API (POST records + GET backup)
  backup.py         # Backup logic: auto / monthly / yearly modes + CLI entry point
  notifier.py       # Notifier — Telegram notifications (transversal)
  logging_setup.py  # Rotating file + console logging; configure_logging() for CLIs
  main.py           # Sync orchestrator: wires all modules, minimal logic

docker/
  base/Dockerfile   # python:3.11-slim + pip deps; published as python-trade-republic
  app/Dockerfile    # installs cron, copies app code + entrypoint.sh
  app/entrypoint.sh # one-shot vs crond mode; supports SYNC_SCHEDULE, BACKUP_SCHEDULE, CMD

tests/
  test_config.py
  test_persistence.py
  test_tr_mapper.py
  test_wallet_client.py
  test_main.py
  test_backup.py
  test_notifier.py
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
python -m app.backup <mode> [param]
        ↓
WalletClient.get_accounts/categories/budgets/labels/records()
  └── _get_all() — paginates via nextOffset until exhausted
        ↓
_fetch_snapshot()   # assembles payload dict with all resources + metadata
        ↓
_write_json()       # writes to data_dir/backups/{monthly|yearly}/wallet-{mode}-{period}.json
        ↓
run_yearly() only: removes covered wallet-monthly-{year}-*.json files
        ↓
Notifier.backup_complete()  # Telegram summary (optional)
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

### Scheduled daemon
- `docker/app/entrypoint.sh`: supports two independent cron jobs:
  - `SYNC_SCHEDULE` — registers the sync job (`python -m app.main`)
  - `BACKUP_SCHEDULE` — registers the backup job (`python -m app.backup auto`)
  - Both optional and independent; if neither is set, runs one-shot sync and exits.
  - `CMD=backup [mode] [param]` — runs a one-shot backup and exits (used by `tr-sync.sh backup`).
- `TZ` env var must be set in the container for cron to interpret hours in local time (default is UTC).

### Backup strategy
- **`auto` mode**: designed for daily cron. Always overwrites current + previous month. In February, generates the yearly backup for the previous year if it does not yet exist (idempotent).
- **`monthly` / `yearly` modes**: explicit, always execute, intended for manual runs and backfill.
- Files are plain JSON, permanent, never purged (unlike `sync.db` which purges after 60 days).
- Yearly cleanup removes the 12 monthly files whose period is covered by the yearly backup.

### BudgetBakers API — GET (backup)
- Base URL: `{base_url}/v1/api/{resource}`
- Pagination via `nextOffset` in the response dict; plain list responses have no pagination.
- `_get_all()` handles both response shapes: plain `list` (no pagination) and `{"data": [...], "nextOffset": N}`.

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
| `ghcr.io/sanmibuh/python-trade-republic` | `python:3.11-slim` + pip deps | Major releases |
| `ghcr.io/sanmibuh/tr-wallet-sync` | `python-trade-republic` + app code + cron | Minor/patch releases |

`cron` is intentionally installed only in the app image (`docker/app/Dockerfile`), not in the base image — it is an app concern.

Must rebuild the app image after any code change:
```bash
make build SERVICE=<name>
```

---

## Data volume

`/app/data` (mounted from host) contains:
- `sync.db` — SQLite database with `processed_events` table (purged after 60 days)
- `sync.log` — rotating log file
- pytr session/cookie files (login state)
- `backups/monthly/` — monthly JSON snapshots (permanent)
- `backups/yearly/` — yearly JSON snapshots (permanent)

---

## Test suite

210 tests across 7 files — all passing.

```
tests/test_config.py         # Config dataclass, _read_label_ids, LABELABLE_EVENT_TYPES
tests/test_persistence.py    # EventRepository, dedup_event_id, mark_processed
tests/test_tr_mapper.py      # _HANDLERS, IBAN extraction, label_ids, filter_by_lookback
tests/test_wallet_client.py  # post_records batching; GET methods + pagination
tests/test_main.py           # filter_by_lookback, _build_batch; cfg mocks use label_ids={}
tests/test_backup.py         # date helpers, run_monthly, run_yearly, run_auto (all cases)
tests/test_notifier.py       # all notification types including backup_complete
```

Run:
```bash
make test
```

---

## Relevant files (quick reference)

| File | Role |
|---|---|
| `app/main.py` | Sync orchestrator; passes `cfg.label_ids` to `build_records_for_event` |
| `app/backup.py` | Backup logic: `run_auto`, `run_monthly`, `run_yearly`; CLI entry point |
| `app/tr_client.py` | `TRClient` with `event_callback`; no module-level functions |
| `app/tr_mapper.py` | `_HANDLERS`, `_ZERO_AMOUNT_TYPES`, `KNOWN_EVENT_TYPES`, `_make_record` |
| `app/persistence.py` | `EventRepository`, `dedup_event_id`; `INSERT OR IGNORE` |
| `app/config.py` | `Config` dataclass; `label_ids: dict[str, str]`; `_read_label_ids()` |
| `app/wallet_client.py` | `post_records` (sync) + `get_*` methods with pagination (backup) |
| `app/logging_setup.py` | `setup_logging(data_dir)` for daemon; `configure_logging()` for CLI entry points |
| `docker/app/entrypoint.sh` | Handles `SYNC_SCHEDULE`, `BACKUP_SCHEDULE`, `CMD` |
