from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
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
from app.tr_client import LoginFailedError, SessionExpiredError, TRClient
from app.tr_mapper import filter_by_lookback
from app.wallet_client import WalletClient

log = logging.getLogger(__name__)

# Serializes all in-process sync/login/resync runs so that logging handlers
# added by _prepare never overlap between concurrent bot-triggered calls.
_RUN_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Bootstrap helpers (shared by run() and run_login())
# ---------------------------------------------------------------------------


@contextmanager
def _prepare(cfg: Config) -> Generator[Notifier, None, None]:
    """Shared bootstrap for the sync/login entry points.

    Ensures the data dir exists, configures the SSL circuit-breaker and logging,
    and yields a ready-to-use ``Notifier``.  Removes the logging handlers it added
    on exit (normal or exceptional) so repeated in-process calls do not accumulate
    handlers on the root logger.
    """
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with _RUN_LOCK:
        http_client.configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
        handlers = setup_logging(cfg.data_dir)
        root = logging.getLogger()
        try:
            yield Notifier(cfg.telegram_bot_token, cfg.telegram_chat_id, cfg.owner_name)
        finally:
            for h in handlers:
                root.removeHandler(h)
                h.close()


def _connect(cfg: Config, notifier: Notifier) -> TRClient:
    """Thin wrapper used by run_login(); delegates to SyncRunner.connect()."""
    return SyncRunner(cfg, notifier).connect()


# ---------------------------------------------------------------------------
# Orchestrator entry points
# ---------------------------------------------------------------------------


def run(cfg: Config | None = None) -> int:
    if cfg is None:
        cfg = Config.from_env()
    with _prepare(cfg) as notifier:
        log.info("Starting sync for owner: %s", cfg.owner_name)

        since = datetime.now(UTC) - timedelta(days=cfg.lookback_days)

        with EventRepository(cfg.data_dir / _SYNC_DB) as repo:
            repo.purge_old_records(ttl_days=cfg.dedup_ttl_days)
            runner = SyncRunner(cfg, notifier)
            events = runner.fetch_events(since)

            recent_events = filter_by_lookback(events, since)
            new_events = repo.filter_unprocessed(recent_events)
            skipped_count = runner._notify_fetch_summary(
                since, recent_events, new_events
            )

            counts = _SyncCounts()
            try:
                wallet_client = WalletClient(api_key=cfg.wallet_api_key)
                batch = runner.build_batch(
                    new_events, repo, wallet_client=wallet_client
                )
                counts.excluded = (
                    batch.excluded_count
                )  # preserved for finally on exception
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


def run_login(cfg: Config | None = None) -> int:
    """Re-authenticate with Trade Republic on demand and persist the session.

    Used by the ``login`` command (triggered by the Telegram ``/login`` command).
    Resumes the session if still valid; otherwise runs the full 2FA login using
    the Telegram-based authenticator-code flow (or a push approval for accounts
    without an authenticator). Returns 0 on success, 1 on a recoverable failure.
    """
    if cfg is None:
        cfg = Config.from_env()
    with _prepare(cfg) as notifier:
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


def run_resync(date_str: str, cfg: Config | None = None) -> int:
    """Force a re-sync of all TR events for a specific day, bypassing dedup.

    Already-synced events are updated via PUT; never-synced events are inserted
    via POST.  All events are force-marked processed (upsert) afterwards.

    Args:
        date_str: ISO date string ``YYYY-MM-DD`` for the day to re-sync.
        cfg:      Optional pre-built :class:`Config`.  When ``None`` (default),
                  falls back to ``Config.from_env()`` for backwards compatibility.

    Returns:
        0 on success, 1 on invalid date or unrecoverable error.
    """
    try:
        date.fromisoformat(date_str)
    except ValueError:
        log.error("Invalid date for resync: %r (expected YYYY-MM-DD)", date_str)
        return 1

    if cfg is None:
        cfg = Config.from_env()
    with _prepare(cfg) as notifier:
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
