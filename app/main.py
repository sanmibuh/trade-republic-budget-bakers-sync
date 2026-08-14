from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from requests import HTTPError

from app import http_client
from app.config import Config
from app.logging_setup import setup_logging
from app.notifier import Notifier
from app.persistence import EventRepository, dedup_event_id
from app.tr_client import LoginFailedError, SessionExpiredError, TRClient
from app.tr_mapper import (
    KNOWN_EVENT_TYPES,
    build_records_for_event,
    extract_event_type,
    filter_by_lookback,
)
from app.twofa import (
    TelegramCodeProvider,
    TerminalCodeProvider,
    select_code_provider,
)
from app.wallet_client import WalletClient

try:
    from pytr.exceptions import AuthenticationError
except Exception:  # pragma: no cover

    class AuthenticationError(Exception):  # type: ignore[no-redef]  # pragma: no cover
        """Sentinel: raised only by pytr when it IS installed."""


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result value objects
# ---------------------------------------------------------------------------


@dataclass
class _SyncCounts:
    synced: int = 0
    excluded: int = 0
    failed: int = 0


@dataclass
class _Batch:
    records: list[dict]
    event_record_indices: list[list[int]]
    excluded_count: int


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


def _build_code_provider(
    cfg: Config, notifier: Notifier
) -> TerminalCodeProvider | TelegramCodeProvider | None:
    """Choose how the authenticator 2FA code is obtained for this environment."""
    telegram_configured = bool(cfg.telegram_bot_token and cfg.telegram_chat_id)
    return select_code_provider(
        data_dir=cfg.data_dir,
        notifier=notifier,
        instance=cfg.instance,
        isatty=sys.stdin.isatty(),
        telegram_configured=telegram_configured,
    )


# ---------------------------------------------------------------------------
# SyncRunner — sync orchestration class
# ---------------------------------------------------------------------------


class SyncRunner:
    """Encapsulates the sync orchestration steps.

    All dependencies (``cfg``, ``notifier``) are injected via the constructor,
    making them explicit and easy to mock in tests.
    """

    def __init__(self, cfg: Config, notifier: Notifier) -> None:
        self._cfg = cfg
        self._notifier = notifier

    def connect(self) -> TRClient:
        """Create a ``TRClient`` and establish a session (resume or full 2FA login)."""
        tr_client = TRClient(self._cfg.phone_number, self._cfg.pin, self._cfg.data_dir)
        tr_client.connect(
            on_login_required=self._notifier.login_required,
            on_login_success=self._notifier.login_success,
            code_provider=_build_code_provider(self._cfg, self._notifier),
        )
        return tr_client

    def fetch_events(self, since: datetime) -> list[dict[str, Any]]:
        """Connect to Trade Republic and return filtered timeline events.

        Raises SystemExit(1) on recoverable auth errors; re-raises on unexpected ones.
        """
        try:
            tr_client = self.connect()
            log.info("Trade Republic session established")
            events = tr_client.fetch_timeline_events(since=since)
            log.info("Fetched %d timeline events", len(events))
        except LoginFailedError:
            log.exception("Login failed")
            self._notifier.login_failed()
            raise SystemExit(1) from None
        except SessionExpiredError:
            log.warning(
                "Session expired and no interactive terminal available — bootstrap required"
            )
            self._notifier.authentication_required()
            raise SystemExit(1) from None
        except AuthenticationError:
            log.exception("Authentication error")
            self._notifier.authentication_required()
            raise SystemExit(1) from None
        except HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            log.exception("HTTP error (status=%s)", status)
            if status == 401:
                self._notifier.authentication_required()
                raise SystemExit(1) from exc
            self._notifier.error(exc)
            raise
        except Exception as exc:
            log.exception("Unexpected error during TR connection/fetch")
            self._notifier.error(exc)
            raise
        else:
            return events

    def build_batch(
        self,
        new_events: list[dict[str, Any]],
        repo: EventRepository,
    ) -> _Batch:
        """Convert new events into API records, marking zero-amount ones as excluded."""
        all_records: list[dict] = []
        event_record_indices: list[list[int]] = [[] for _ in new_events]
        excluded_count = 0

        for event_idx, event in enumerate(new_events):
            event_type = extract_event_type(event)
            if event_type and event_type not in KNOWN_EVENT_TYPES:
                log.warning(
                    "Unknown TR event type %r — notifying and falling back to cash handler",
                    event_type,
                )
                self._notifier.unknown_event_type(event_type)

            recs = build_records_for_event(
                event,
                cash_account_id=self._cfg.wallet_cash_account_id,
                portfolio_account_id=self._cfg.wallet_portfolio_account_id,
                label_ids=self._cfg.label_ids,
            )
            if not recs:
                repo.mark_processed(event)
                excluded_count += 1
                log.info("Excluded zero-amount event %s", dedup_event_id(event))
                continue
            for r in recs:
                event_record_indices[event_idx].append(len(all_records))
                all_records.append(r)

        return _Batch(all_records, event_record_indices, excluded_count)

    def process_results(
        self,
        results: list[dict[str, Any]],
        new_events: list[dict[str, Any]],
        event_record_indices: list[list[int]],
        repo: EventRepository,
    ) -> _SyncCounts:
        """Interpret API results, mark successful events as processed, log failures."""
        results_by_index = {r.get("inputIndex", i): r for i, r in enumerate(results)}

        synced = 0
        failed = 0
        for event_idx, event in enumerate(new_events):
            record_indices = event_record_indices[event_idx]
            if not record_indices:
                continue
            missing = [i for i in record_indices if i not in results_by_index]
            if missing:
                eid = dedup_event_id(event)
                log.error(
                    "Event %s has no API result for record index(es): %s", eid, missing
                )
                self._notifier.missing_api_result(eid, missing)
                failed += 1
                continue
            failures = [
                results_by_index[i]
                for i in record_indices
                if results_by_index[i].get("error")
            ]
            if not failures:
                wallet_ids = [
                    results_by_index[i]["id"]
                    for i in record_indices
                    if results_by_index[i].get("id")
                ]
                wallet_record_id = ",".join(wallet_ids) if wallet_ids else None
                repo.mark_processed(event, wallet_record_id=wallet_record_id)
                synced += 1
            else:
                failed += 1
                eid = dedup_event_id(event)
                for f in failures:
                    log.error(
                        "Event %s record %d failed: %s",
                        eid,
                        f.get("inputIndex"),
                        f.get("error"),
                    )

        repo.commit()
        return _SyncCounts(synced=synced, failed=failed)

    def resync_day(
        self,
        date_str: str,
        repo: EventRepository,
        wallet_client: WalletClient,
    ) -> _SyncCounts:
        """Re-sync all TR events for a specific day, bypassing dedup.

        For events already in the database with a ``wallet_record_id``, each
        sub-record is updated via ``PUT``.  For new or previously-excluded events
        (no wallet ID), the records are inserted via ``POST``.

        All events are force-marked processed (upsert) so subsequent regular
        syncs continue to skip them correctly.

        Args:
            date_str: ISO date string ``YYYY-MM-DD`` for the day to resync.
            repo:     Open ``EventRepository`` for dedup lookups and persistence.
            wallet_client: Authenticated ``WalletClient`` instance.

        Returns:
            A ``_SyncCounts`` with synced / excluded / failed tallies.
        """
        since = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
        until = since + timedelta(days=1)

        events = self.fetch_events(since)
        day_events = filter_by_lookback(events, since, until=until)

        log.info(
            "Resync %s: %d events fetched (dedup bypassed)", date_str, len(day_events)
        )

        synced = 0
        excluded = 0
        failed = 0

        for event in day_events:
            event_type = extract_event_type(event)
            if event_type and event_type not in KNOWN_EVENT_TYPES:
                log.warning(
                    "Unknown TR event type %r — falling back to cash handler",
                    event_type,
                )
                self._notifier.unknown_event_type(event_type)

            recs = build_records_for_event(
                event,
                cash_account_id=self._cfg.wallet_cash_account_id,
                portfolio_account_id=self._cfg.wallet_portfolio_account_id,
                label_ids=self._cfg.label_ids,
            )

            if not recs:
                repo.mark_processed_force(event, wallet_record_id=None)
                excluded += 1
                log.info("Resync: excluded zero-amount event %s", dedup_event_id(event))
                continue

            existing_ids_raw = repo.get_wallet_record_id(event)
            existing_ids = (
                [wid for wid in existing_ids_raw.split(",") if wid]
                if existing_ids_raw
                else []
            )

            if existing_ids:
                # Update existing records one-by-one via PUT.
                event_failed = False
                new_ids: list[str] = []
                for i, record in enumerate(recs):
                    wid = existing_ids[i] if i < len(existing_ids) else None

                    if wid:
                        try:
                            resp = wallet_client.put_record(wid, record)
                            new_ids.append(resp.get("id") or wid)
                        except Exception as exc:
                            log.error(
                                "PUT failed for event %s record %s: %s",
                                dedup_event_id(event),
                                wid,
                                exc,
                            )
                            event_failed = True
                            break
                    else:
                        try:
                            results = wallet_client.post_records([record])
                            if results and results[0].get("id"):
                                new_ids.append(results[0]["id"])
                        except Exception as exc:
                            log.error(
                                "POST failed for extra record of event %s: %s",
                                dedup_event_id(event),
                                exc,
                            )
                            event_failed = True
                            break

                if event_failed:
                    failed += 1
                else:
                    wallet_record_id = ",".join(new_ids) if new_ids else None
                    repo.mark_processed_force(event, wallet_record_id=wallet_record_id)
                    synced += 1
            else:
                # No prior wallet record — insert via POST.
                try:
                    results = wallet_client.post_records(recs)
                    wallet_ids = [r["id"] for r in results if r.get("id")]
                    wallet_record_id = ",".join(wallet_ids) if wallet_ids else None
                    repo.mark_processed_force(event, wallet_record_id=wallet_record_id)
                    synced += 1
                except Exception as exc:
                    log.error(
                        "POST failed for event %s: %s", dedup_event_id(event), exc
                    )
                    failed += 1

        repo.commit()
        return _SyncCounts(synced=synced, excluded=excluded, failed=failed)


# ---------------------------------------------------------------------------
# Orchestrator entry points
# ---------------------------------------------------------------------------


def run() -> int:
    cfg = Config.from_env()
    notifier = _prepare(cfg)
    log.info("Starting sync for owner: %s", cfg.owner_name)

    since = datetime.now(UTC) - timedelta(days=cfg.lookback_days)

    with EventRepository(cfg.data_dir / "sync.db") as repo:
        repo.purge_old_records()
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
            batch = runner.build_batch(new_events, repo)
            counts.excluded = batch.excluded_count

            if batch.records:
                results = WalletClient(api_key=cfg.wallet_api_key).post_records(
                    batch.records
                )
                log.debug("API results: %s", results)
                counts = runner.process_results(
                    results, new_events, batch.event_record_indices, repo
                )
                counts.excluded = batch.excluded_count
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


def _connect(cfg: Config, notifier: Notifier) -> TRClient:
    """Thin wrapper used by run_login(); delegates to SyncRunner.connect()."""
    return SyncRunner(cfg, notifier).connect()


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
        datetime.fromisoformat(date_str)
    except ValueError:
        log.error("Invalid date for resync: %r (expected YYYY-MM-DD)", date_str)
        return 1

    cfg = Config.from_env()
    notifier = _prepare(cfg)
    log.info("Starting force resync for date=%s owner=%s", date_str, cfg.owner_name)

    try:
        with EventRepository(cfg.data_dir / "sync.db") as repo:
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
