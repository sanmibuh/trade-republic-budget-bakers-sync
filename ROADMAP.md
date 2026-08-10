# Roadmap

Pending ideas and improvements, roughly ordered by impact. No fixed dates or priorities — picked up as convenient.

Each item is a candidate to be evaluated before implementing. Follow the TDD workflow in `AGENTS.md`: write the test first, watch it fail, then implement.

---

## Robustness

### Retries with backoff in `WalletClient`
`post_records` / `_get_all` currently only handle the SSL circuit-breaker (`app/http_client.py`). A transient `502`/`503` or a network timeout aborts the whole batch. Wrap the POST/GET calls with a bounded retry + exponential backoff for transient failures (5xx, connection/read timeouts) — but **not** for `400` (client error, all records rejected). Keep `200/207/400/500` batch-result semantics intact (`app/wallet_client.py:62`).

### Backup integrity / atomic write
`_write_json` (`app/backup.py:62`) writes directly to the final path. If the process dies mid-write the backup JSON is left corrupt. Write to a `.tmp` file and `os.replace()` (atomic rename) into place. Optionally re-read the file afterwards and validate the resource counts against the in-memory payload (`_payload_counts`) before considering the backup done.

### Healthcheck / liveness signal
There is no way for Docker or the NAS to detect a hung daemon. Write a `health` file (or timestamp) to `DATA_DIR` after each successful sync/backup, containing last-run status + UTC timestamp. Expose it via a Docker `healthcheck` in `docker-compose.yml` so a stalled container can be restarted automatically.

### SQLite `IN (...)` variable limit
`filter_unprocessed` (`app/persistence.py:88`) builds a single `SELECT ... WHERE event_id IN (placeholders)`. SQLite caps bound variables (historically 999). Normal `LOOKBACK_DAYS=7` runs are fine, but a large backfill would exceed it. Chunk the ID list into batches when querying.

---

## Configuration

### Configurable dedup retention
`_TTL_DAYS = 60` is hardcoded in `app/persistence.py:15`. Expose it as an env var (e.g. `DEDUP_TTL_DAYS`), read via `Config.from_env()` in `app/config.py` (never `os.getenv` elsewhere). Pass it to `purge_old_records(ttl_days=...)`.

### Locale-independent detail extraction
Detail-row titles in `app/tr_mapper.py:204-208` (`Transaktion`, `Steuern`, `Angefallen`, `Angesammelt`) are hardcoded to the German TR locale. Accounts in other languages would break `_note_extras`. Abstract these into a locale map or match on a language-independent field if TR exposes one.

---

## Sync

### Force re-sync of a specific day (ignore dedup, upsert)
Allow forcing a sync for a given day from Telegram (and CLI), **bypassing the dedup filter** so already-processed events are re-fetched and re-sent. Instead of blindly inserting, each record should be **upserted**: check whether the corresponding record already exists in BudgetBakers and `PUT`/`PATCH` it instead of `POST`-ing a duplicate.

Design considerations / gaps found during review:
- **Dedup bypass**: add a code path in `main.run()` that skips `repo.filter_unprocessed` (`app/main.py:185`) for the forced date, but **still** calls `repo.mark_processed` afterwards so the state stays consistent.
- **Missing wallet record ID**: the `processed_events` table (`app/persistence.py:51`) stores TR event data but **not** the BudgetBakers record ID returned by the API. To update instead of insert, we need that mapping. Two options:
  1. Persist the returned record `id` in `processed_events` (schema migration) when `post_records` succeeds.
  2. On force-sync, query `WalletClient.get_records(day, day)` and match existing records by date/amount/note to decide insert vs. update.
- **No update method**: `WalletClient` only exposes `post_records` (POST, `app/wallet_client.py:29`). A `put_record` / `update_records` method against the BudgetBakers API is required. Verify the API supports record updates and its schema/limits.
- **Date param**: the forced day drives `since`/`until` (a single-day window) rather than `LOOKBACK_DAYS`.
- **Telegram**: new command (e.g. `/resync <YYYY-MM-DD>` or an inline date picker) reusing the instance keyboard from `/sync`.

### Dry-run mode
Add a `--dry-run` flag to `python -m app sync` (`app/__main__.py`). Builds the records via `build_records_for_event` and logs / prints what would be POSTed, without calling `WalletClient.post_records` and without marking events processed. Useful for debugging new event-type mappings before they hit BudgetBakers.

### Refactor `excluded` count handoff in the orchestrator
`main.run()` assigns `counts.excluded = batch.excluded_count` twice (`app/main.py:200` and `:206`) because `_process_results` returns a fresh `_SyncCounts` that resets `excluded`. This is fragile — deleting line 206 breaks it silently. Have `_process_results` accept/preserve the excluded count, or set it once after the call.

---

## Notifications

### Aggregated daily/weekly summary
Today the `Notifier` sends one message per run. Add an optional aggregated summary (daily or weekly) with totals across runs — fewer, higher-signal Telegram messages for users who sync frequently.

---

## Bot

### 2FA via Telegram bot
Evaluate whether the bot can handle the Trade Republic 2FA flow: when a container requires authentication, the bot prompts the user for the PIN or approval and forwards it to the container. Security considerations must be assessed carefully — the bot would be transmitting sensitive credentials over Telegram.

### Real `/status` command
`_cmd_status` (`app/bot.py:296`) only lists the configured container names. Enhance it via the Docker SDK to report whether each container is actually `running`, its last run outcome (parse the tail of `sync.log`), and the number of events synced recently.

### `/logs` command
Add a `/logs` command to the bot that returns the last N lines of `sync.log` for a chosen instance (reuse the instance inline-keyboard picker from `/sync`). Lets the user diagnose issues from Telegram without SSH-ing into the NAS. The bot already has Docker SDK access, so it can read the log either via `container.exec_run(["tail", "-n", "N", "/app/data/sync.log"])` or by reading the mounted data volume directly.

### Version / update check
Let the user check from Telegram whether a newer container image is published. The local version is the `VERSION` file (currently `6.1.0`) baked into the image; the published tags live at `ghcr.io/sanmibuh/tr-wallet-sync`. A `/version` (or `/update_check`) command would:
- Report the running image tag/version (read `VERSION` or the container image label via the Docker SDK).
- Query the GitHub Container Registry / GitHub Releases API for the latest published tag and compare (semver).
- Notify whether an upgrade is available (and optionally hint `./tr-sync.sh upgrade`).

Considerations: GHCR tag listing may require auth even for public images — the GitHub Releases API (`/repos/sanmibuh/<repo>/releases/latest`) is likely the simpler, unauthenticated source of truth since releases are the trigger for image builds (see `ARCHITECTURE.md` release workflow).
