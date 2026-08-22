from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from app.config import Config
from app.notifier import Notifier
from app.persistence import EventRepository, migrate_legacy_databases
from app.sync_runner import (  # noqa: F401 — re-exported for backward compatibility
    _SYNC_DB,
    AuthenticationError,
    SyncRunner,
    _Batch,
    _SyncCounts,
)
from app.tr_mapper import filter_by_lookback
from app.wallet_client import WalletClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------


def _prepare(cfg: Config) -> Notifier:
    """Shared bootstrap for the sync entry points.

    Ensures the data dir exists and returns a ready-to-use ``Notifier``.

    SSL is configured once at process startup by each CLI entry point or by
    ``TelegramBot.__init__()`` — not here — so repeated in-process calls from
    the bot share a single, stable SSL policy without racing.

    Logging is configured once at process startup by each CLI entry point —
    not here — so repeated in-process calls (e.g. from the bot) reuse the
    shared log file without any handler lifecycle management.
    """
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    return Notifier(cfg.telegram_bot_token, cfg.telegram_chat_id, cfg.owner_name)


# ---------------------------------------------------------------------------
# Orchestrator entry points
# ---------------------------------------------------------------------------


def run(cfg: Config) -> int:
    notifier = _prepare(cfg)
    log.info("Starting sync for owner: %s", cfg.owner_name)
    migrate_legacy_databases(cfg.shared_db_path)

    since = datetime.now(UTC) - timedelta(days=cfg.lookback_days)

    with EventRepository(cfg.shared_db_path, instance=cfg.instance) as repo:
        repo.purge_old_records(ttl_days=cfg.dedup_ttl_days)
        runner = SyncRunner(cfg, notifier)
        events = runner.fetch_events(since)

        recent_events = filter_by_lookback(events, since)
        new_events = repo.filter_unprocessed(recent_events)
        skipped_count = runner._notify_fetch_summary(since, recent_events, new_events)

        counts = _SyncCounts()
        try:
            wallet_client = WalletClient(api_key=cfg.wallet_api_key)
            batch = runner.build_batch(new_events, repo, wallet_client=wallet_client)
            counts.excluded = batch.excluded_count  # preserved for finally on exception
            counts = runner._submit_batch(
                batch, wallet_client, repo, new_events=new_events
            )
            log.info(
                "Sync complete. synced=%d excluded=%d failed=%d",
                counts.synced,
                counts.excluded,
                counts.failed,
            )
        except Exception as exc:
            log.exception("Error syncing events to wallet")
            notifier.error(exc)
            raise
        finally:
            sent = notifier.sync_complete(
                synced=counts.synced,
                failed=counts.failed,
                skipped=skipped_count,
                excluded=counts.excluded,
            )
            if not sent:
                log.warning(
                    "sync_complete notification not sent (no credentials or request failed)"
                )

    return 0


def run_resync(date_str: str, cfg: Config) -> int:
    """Force a re-sync of all TR events for a specific day, bypassing dedup.

    Already-synced events are updated via PUT; never-synced events are inserted
    via POST.  All events are force-marked processed (upsert) afterwards.

    Args:
        date_str: ISO date string ``YYYY-MM-DD`` for the day to re-sync.
        cfg:      Pre-built :class:`Config` for the instance.

    Returns:
        0 on success, 1 on invalid date or unrecoverable error.
    """
    try:
        date.fromisoformat(date_str)
    except ValueError:
        log.error("Invalid date for resync: %r (expected YYYY-MM-DD)", date_str)
        return 1

    notifier = _prepare(cfg)
    log.info("Starting force resync for date=%s owner=%s", date_str, cfg.owner_name)
    migrate_legacy_databases(cfg.shared_db_path)

    try:
        with EventRepository(cfg.shared_db_path, instance=cfg.instance) as repo:
            runner = SyncRunner(cfg, notifier)
            wallet_client = WalletClient(api_key=cfg.wallet_api_key)
            counts = runner.resync_day(date_str, repo, wallet_client)

        log.info(
            "Resync complete. date=%s synced=%d excluded=%d failed=%d",
            date_str,
            counts.synced,
            counts.excluded,
            counts.failed,
        )
        notifier.sync_complete(
            synced=counts.synced,
            failed=counts.failed,
            skipped=0,
            excluded=counts.excluded,
        )
    except Exception as exc:
        log.exception("Error during resync for date=%s", date_str)
        notifier.error(exc)
        return 1

    return 0
