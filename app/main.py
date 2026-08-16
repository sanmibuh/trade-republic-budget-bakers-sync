from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from app import http_client
from app.config import Config
from app.logging_setup import setup_logging
from app.notifier import Notifier
from app.persistence import EventRepository
from app.sync_runner import (  # noqa: F401 — re-exported for backward compatibility
    _SYNC_DB,
    AuthenticationError,
    SyncRunner,
    _Batch,
    _SyncCounts,
)
from app.tr_client import LoginFailedError, SessionExpiredError
from app.tr_mapper import filter_by_lookback
from app.wallet_client import WalletClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bootstrap helpers (shared by run() and run_login())
# ---------------------------------------------------------------------------


def _prepare(cfg: Config) -> Notifier:
    """Shared bootstrap for the sync/login entry points.

    Ensures the data dir exists, configures the SSL circuit-breaker and logging,
    and returns a ready-to-use ``Notifier``.
    """
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    http_client.configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    setup_logging(cfg.data_dir)
    return Notifier(cfg.telegram_bot_token, cfg.telegram_chat_id, cfg.owner_name)


def _connect(cfg: Config, notifier: Notifier):
    """Thin wrapper used by run_login(); delegates to SyncRunner.connect()."""
    return SyncRunner(cfg, notifier).connect()


# ---------------------------------------------------------------------------
# Orchestrator entry points
# ---------------------------------------------------------------------------


def run() -> int:
    cfg = Config.from_env()
    notifier = _prepare(cfg)
    log.info("Starting sync for owner: %s", cfg.owner_name)

    since = datetime.now(UTC) - timedelta(days=cfg.lookback_days)

    with EventRepository(cfg.data_dir / _SYNC_DB) as repo:
        repo.purge_old_records(ttl_days=cfg.dedup_ttl_days)
        runner = SyncRunner(cfg, notifier)
        events = runner.fetch_events(since)

        recent_events = filter_by_lookback(events, since)
        new_events = repo.filter_unprocessed(recent_events)
        skipped_count = len(recent_events) - len(new_events)
        log.info("%d new events to sync (after dedup)", len(new_events))

        notifier.fetch_summary(
            since=since.strftime("%Y-%m-%d"),
            until=datetime.now(UTC).strftime("%Y-%m-%d"),
            fetched=len(recent_events),
            new=len(new_events),
            skipped=skipped_count,
        )

        counts = _SyncCounts()
        try:
            wallet_client = WalletClient(api_key=cfg.wallet_api_key)
            batch = runner.build_batch(new_events, repo, wallet_client=wallet_client)
            counts.excluded = batch.excluded_count

            if batch.records:
                results = wallet_client.post_records(batch.records)
                results = runner._retry_category_failures(
                    batch.records,
                    results,
                    wallet_client,
                    categorizer=batch.categorizer,
                )
                log.debug("API results: %s", results)
                counts = runner.process_results(
                    results,
                    new_events,
                    batch.event_record_indices,
                    repo,
                    excluded_count=batch.excluded_count,
                )
            else:
                repo.commit()

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


def run_login() -> int:
    """Re-authenticate with Trade Republic on demand and persist the session.

    Used by the ``login`` command (triggered by the Telegram ``/login`` command).
    Resumes the session if still valid; otherwise runs the full 2FA login using
    the Telegram-based authenticator-code flow (or a push approval for accounts
    without an authenticator). Returns 0 on success, 1 on a recoverable failure.
    """
    cfg = Config.from_env()
    notifier = _prepare(cfg)
    log.info("Starting on-demand login for owner: %s", cfg.owner_name)

    try:
        _connect(cfg, notifier)
    except LoginFailedError:
        log.exception("Login failed")
        notifier.login_failed()
        return 1
    except SessionExpiredError:
        log.warning(
            "Session expired and no code provider available — bootstrap required"
        )
        notifier.authentication_required()
        return 1
    except Exception as exc:
        log.exception("Unexpected error during on-demand login")
        notifier.error(exc)
        return 1

    log.info("On-demand login completed successfully")
    return 0


def run_resync(date_str: str) -> int:
    """Force a re-sync of all TR events for a specific day, bypassing dedup.

    Already-synced events are updated via PUT; never-synced events are inserted
    via POST.  All events are force-marked processed (upsert) afterwards.

    Args:
        date_str: ISO date string ``YYYY-MM-DD`` for the day to re-sync.

    Returns:
        0 on success, 1 on invalid date or unrecoverable error.
    """
    try:
        date.fromisoformat(date_str)
    except ValueError:
        log.error("Invalid date for resync: %r (expected YYYY-MM-DD)", date_str)
        return 1

    cfg = Config.from_env()
    notifier = _prepare(cfg)
    log.info("Starting force resync for date=%s owner=%s", date_str, cfg.owner_name)

    try:
        with EventRepository(cfg.data_dir / _SYNC_DB) as repo:
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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
