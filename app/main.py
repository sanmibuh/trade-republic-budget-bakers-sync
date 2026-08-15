from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from requests import HTTPError

from app import http_client
from app.categorizer import HistoryCategorizer
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

_SYNC_DB = "sync.db"


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

    def _write_auth_state(self, status: str) -> None:
        """Persist auth *status* to ``sync.db`` for ``/status`` reporting.

        Opens a short-lived connection so it works both inside and outside the
        main ``EventRepository`` context used by :func:`run`.  Failures are
        logged as warnings and swallowed — auth_state is best-effort
        observability and must never interrupt the main sync flow.
        """
        try:
            with EventRepository(self._cfg.data_dir / _SYNC_DB) as repo:
                repo.set_auth_state(self._cfg.instance, status)
        except Exception:
            log.warning(
                "Failed to persist auth_state=%r for instance %r",
                status,
                self._cfg.instance,
                exc_info=True,
            )

    def _write_failed_sync_run(self) -> None:
        """Persist a failed sync run to ``sync.db`` for ``/status`` reporting.

        Best-effort: failures are logged as warnings and never interrupt the
        main sync flow.
        """
        try:
            with EventRepository(self._cfg.data_dir / _SYNC_DB) as repo:
                repo.set_sync_run(
                    self._cfg.instance,
                    status="failed",
                    saved=0,
                    failed=0,
                    excluded=0,
                )
        except Exception:
            log.warning(
                "Failed to persist failed sync_run for instance %r",
                self._cfg.instance,
                exc_info=True,
            )

    def connect(self) -> TRClient:
        """Create a ``TRClient`` and establish a session (resume or full 2FA login)."""
        tr_client = TRClient(self._cfg.phone_number, self._cfg.pin, self._cfg.data_dir)
        try:
            tr_client.connect(
                on_login_required=self._notifier.login_required,
                on_login_success=self._notifier.login_success,
                code_provider=_build_code_provider(self._cfg, self._notifier),
            )
        except SessionExpiredError:
            self._write_auth_state("expired")
            raise
        except (LoginFailedError, AuthenticationError):
            self._write_auth_state("failed")
            raise
        else:
            self._write_auth_state("ok")
        return tr_client

    def _handle_http_error(self, exc: HTTPError) -> None:
        """Log and dispatch an HTTP error from a TR API call.

        Raises ``SystemExit(1)`` for 401 responses (auth failure); otherwise
        notifies via the error channel so the caller can re-raise the original
        exception.
        """
        status = exc.response.status_code if exc.response is not None else None
        log.exception("HTTP error (status=%s)", status)
        if status == 401:
            self._notifier.authentication_required()
            self._write_failed_sync_run()
            raise SystemExit(1) from exc
        self._notifier.error(exc)

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
            self._write_failed_sync_run()
            raise SystemExit(1) from None
        except SessionExpiredError:
            log.warning(
                "Session expired and no interactive terminal available — bootstrap required"
            )
            self._notifier.authentication_required()
            self._write_failed_sync_run()
            raise SystemExit(1) from None
        except AuthenticationError:
            log.exception("Authentication error")
            self._notifier.authentication_required()
            self._write_failed_sync_run()
            raise SystemExit(1) from None
        except HTTPError as exc:
            self._handle_http_error(exc)
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
        *,
        wallet_client: WalletClient | None = None,
    ) -> _Batch:
        """Convert new events into API records, marking zero-amount ones as excluded.

        When ``cfg.category_strategy == "history"`` and a *wallet_client* is
        provided, a :class:`~app.categorizer.HistoryCategorizer` is used to
        look up a category for each record based on its ``note``.
        """
        all_records: list[dict] = []
        event_record_indices: list[list[int]] = [[] for _ in new_events]
        excluded_count = 0

        categorizer: HistoryCategorizer | None = None
        if self._cfg.category_strategy == "history" and wallet_client is not None:
            categorizer = HistoryCategorizer(wallet_client)

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

            if categorizer is not None:
                note = recs[0].get("note", "")
                category_id = categorizer.get_category_id(note)
                if category_id:
                    for r in recs:
                        r["categoryId"] = category_id

            for r in recs:
                event_record_indices[event_idx].append(len(all_records))
                all_records.append(r)

        return _Batch(all_records, event_record_indices, excluded_count)

    def _process_event_result(
        self,
        event: dict[str, Any],
        record_indices: list[int],
        results_by_index: dict[int, dict],
        repo: EventRepository,
    ) -> tuple[int, int]:
        """Process the API result for one event.

        Returns:
            ``(synced_delta, failed_delta)`` — exactly one of the two is 1;
            both are 0 when *record_indices* is empty (excluded event).
        """
        if not record_indices:
            return 0, 0
        missing = [i for i in record_indices if i not in results_by_index]
        if missing:
            eid = dedup_event_id(event)
            log.error(
                "Event %s has no API result for record index(es): %s", eid, missing
            )
            self._notifier.missing_api_result(eid, missing)
            return 0, 1
        failures = [
            results_by_index[i]
            for i in record_indices
            if results_by_index[i].get("error")
        ]
        if failures:
            eid = dedup_event_id(event)
            for f in failures:
                log.error(
                    "Event %s record %d failed: %s",
                    eid,
                    f.get("inputIndex"),
                    f.get("error"),
                )
            return 0, 1
        wallet_ids = [
            results_by_index[i]["id"]
            for i in record_indices
            if results_by_index[i].get("id")
        ]
        wallet_record_id = ",".join(wallet_ids) if wallet_ids else None
        repo.mark_processed(event, wallet_record_id=wallet_record_id)
        return 1, 0

    def process_results(
        self,
        results: list[dict[str, Any]],
        new_events: list[dict[str, Any]],
        event_record_indices: list[list[int]],
        repo: EventRepository,
        excluded_count: int = 0,
    ) -> _SyncCounts:
        """Interpret API results, mark successful events as processed, log failures."""
        results_by_index = {r.get("inputIndex", i): r for i, r in enumerate(results)}

        synced = 0
        failed = 0
        for event_idx, event in enumerate(new_events):
            s, f = self._process_event_result(
                event, event_record_indices[event_idx], results_by_index, repo
            )
            synced += s
            failed += f

        repo.commit()
        counts = _SyncCounts(synced=synced, excluded=excluded_count, failed=failed)
        if counts.failed == 0:
            status = "success"
        elif counts.synced == 0:
            status = "failed"
        else:
            status = "partial"
        try:
            repo.set_sync_run(
                self._cfg.instance,
                status=status,
                saved=counts.synced,
                failed=counts.failed,
                excluded=counts.excluded,
            )
        except Exception:
            log.warning(
                "Failed to persist sync_run=%r for instance %r",
                status,
                self._cfg.instance,
                exc_info=True,
            )
        return counts

    # ------------------------------------------------------------------
    # resync_day helpers
    # ------------------------------------------------------------------

    def _warn_unknown_event_type(self, event: dict[str, Any]) -> None:
        """Log a warning and notify if *event* carries an unrecognised type."""
        event_type = extract_event_type(event)
        if event_type and event_type not in KNOWN_EVENT_TYPES:
            log.warning(
                "Unknown TR event type %r — falling back to cash handler", event_type
            )
            self._notifier.unknown_event_type(event_type)

    @staticmethod
    def _parse_existing_ids(raw: str | None) -> list[str]:
        """Split a comma-joined wallet-record-id string into a list."""
        return [wid for wid in raw.split(",") if wid] if raw else []

    def _resync_put_single(
        self,
        wid: str,
        record: dict,
        event: dict[str, Any],
        wallet_client: WalletClient,
    ) -> tuple[bool, str]:
        """PUT one record; return ``(failed, new_id)``."""
        try:
            resp = wallet_client.put_record(wid, record)
            return False, resp.get("id") or wid
        except Exception:
            log.exception(
                "PUT failed for event %s record %s", dedup_event_id(event), wid
            )
            return True, ""

    def _resync_post_extra(
        self,
        record: dict,
        event: dict[str, Any],
        wallet_client: WalletClient,
    ) -> tuple[bool, str]:
        """POST a single extra record; return ``(failed, new_id)``."""
        try:
            results = wallet_client.post_records([record])
            if not results:
                log.error(
                    "POST returned no results for extra record of event %s",
                    dedup_event_id(event),
                )
                return True, ""
            item = results[0]
            if item.get("error"):
                log.error(
                    "POST error for extra record of event %s: %s",
                    dedup_event_id(event),
                    item["error"],
                )
                return True, ""
            return False, item.get("id") or ""
        except Exception:
            log.exception(
                "POST failed for extra record of event %s", dedup_event_id(event)
            )
            return True, ""

    def _resync_put_records(
        self,
        event: dict[str, Any],
        recs: list[dict],
        existing_ids: list[str],
        wallet_client: WalletClient,
    ) -> tuple[bool, list[str]]:
        """PUT existing sub-records; POST any extras beyond the stored IDs.

        Returns:
            ``(failed, new_ids)`` — *failed* is ``True`` if any call raised.
        """
        new_ids: list[str] = []
        for i, record in enumerate(recs):
            wid = existing_ids[i] if i < len(existing_ids) else None
            failed, new_id = (
                self._resync_put_single(wid, record, event, wallet_client)
                if wid
                else self._resync_post_extra(record, event, wallet_client)
            )
            if failed:
                return True, []
            if new_id:
                new_ids.append(new_id)
        return False, new_ids

    def _resync_post_records(
        self,
        event: dict[str, Any],
        recs: list[dict],
        wallet_client: WalletClient,
    ) -> tuple[bool, str | None]:
        """POST all records for an event that has no prior wallet ID.

        Returns:
            ``(failed, wallet_record_id)`` — *failed* is ``True`` on error.
        """
        try:
            results = wallet_client.post_records(recs)
            results_by_index = {
                r.get("inputIndex", i): r for i, r in enumerate(results)
            }
            missing = [i for i in range(len(recs)) if i not in results_by_index]
            if missing:
                log.error(
                    "POST returned no result for record index(es) %s of event %s",
                    missing,
                    dedup_event_id(event),
                )
                return True, None
            failures = [
                results_by_index[i]
                for i in range(len(recs))
                if results_by_index[i].get("error")
            ]
            if failures:
                eid = dedup_event_id(event)
                for f in failures:
                    log.error(
                        "POST error for event %s record %d: %s",
                        eid,
                        f.get("inputIndex"),
                        f.get("error"),
                    )
                return True, None
            wallet_ids = [
                results_by_index[i]["id"]
                for i in range(len(recs))
                if results_by_index[i].get("id")
            ]
            return False, ",".join(wallet_ids) if wallet_ids else None
        except Exception:
            log.exception("POST failed for event %s", dedup_event_id(event))
            return True, None

    def _resync_single_event(
        self,
        event: dict[str, Any],
        repo: EventRepository,
        wallet_client: WalletClient,
    ) -> tuple[int, int, int]:
        """Process one event during a resync.

        Returns:
            ``(synced, excluded, failed)`` increment tuple.
        """
        self._warn_unknown_event_type(event)

        recs = build_records_for_event(
            event,
            cash_account_id=self._cfg.wallet_cash_account_id,
            portfolio_account_id=self._cfg.wallet_portfolio_account_id,
            label_ids=self._cfg.label_ids,
        )
        if not recs:
            repo.mark_processed_force(event, wallet_record_id=None)
            log.info("Resync: excluded zero-amount event %s", dedup_event_id(event))
            return 0, 1, 0

        existing_ids = self._parse_existing_ids(repo.get_wallet_record_id(event))
        if existing_ids:
            failed, new_ids = self._resync_put_records(
                event, recs, existing_ids, wallet_client
            )
            wallet_record_id: str | None = ",".join(new_ids) if new_ids else None
        else:
            failed, wallet_record_id = self._resync_post_records(
                event, recs, wallet_client
            )

        if failed:
            return 0, 0, 1
        repo.mark_processed_force(event, wallet_record_id=wallet_record_id)
        return 1, 0, 0

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
        d = date.fromisoformat(date_str)
        since = datetime(d.year, d.month, d.day, tzinfo=UTC)
        day_events = filter_by_lookback(
            self.fetch_events(since), since, until=since + timedelta(days=1)
        )
        log.info(
            "Resync %s: %d events fetched (dedup bypassed)", date_str, len(day_events)
        )

        synced = excluded = failed = 0
        for event in day_events:
            s, e, f = self._resync_single_event(event, repo, wallet_client)
            synced += s
            excluded += e
            failed += f

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
