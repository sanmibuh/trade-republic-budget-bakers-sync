from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from requests import HTTPError

from app.categorizer import HistoryCategorizer
from app.config import Config
from app.notifier import Notifier
from app.persistence import EventRepository, dedup_event_id
from app.tr_client import LoginFailedError, SessionExpiredError, TRClient
from app.tr_mapper import (
    KNOWN_EVENT_TYPES,
    build_cancellation_records,
    build_records_for_event,
    extract_event_type,
    filter_by_lookback,
    is_canceled_event,
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

        def __init__(self, message: str = "") -> None:  # pragma: no cover
            super().__init__(message)


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
    records: list[dict[str, Any]]
    event_record_indices: list[list[int]]
    excluded_count: int
    categorizer: HistoryCategorizer | None = None
    cancellation_events: list[dict[str, Any]] = field(default_factory=list)
    cancellation_record_indices: list[list[int]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 2FA code provider selector
# ---------------------------------------------------------------------------


def _build_code_provider(
    cfg: Config, notifier: Notifier
) -> TerminalCodeProvider | TelegramCodeProvider | None:
    """Choose how the authenticator 2FA code is obtained for this environment."""
    telegram_configured = bool(cfg.telegram_bot_token and cfg.telegram_chat_id)
    return select_code_provider(
        code_file=cfg.twofa_code_file,
        pending_file=cfg.twofa_pending_file,
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
            with EventRepository(
                self._cfg.shared_db_path, instance=self._cfg.instance
            ) as repo:
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
            with EventRepository(
                self._cfg.shared_db_path, instance=self._cfg.instance
            ) as repo:
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
        cancellation_events: list[dict[str, Any]] | None = None,
    ) -> _Batch:
        """Convert new events into API records, marking zero-amount and CANCELED
        ones as excluded.

        When ``cfg.category_strategy == "history"`` and a *wallet_client* is
        provided, a :class:`~app.categorizer.HistoryCategorizer` is used to
        look up a category for each record based on its ``note``.

        When *cancellation_events* is provided, reversal records are appended to
        the batch for each event that previously had a ``wallet_record_id``.
        """
        categorizer: HistoryCategorizer | None = None
        if self._cfg.category_strategy == "history" and wallet_client is not None:
            categorizer = HistoryCategorizer(wallet_client)

        all_records: list[dict[str, Any]] = []
        event_record_indices, excluded_count = self._append_new_event_records(
            new_events, repo, all_records, categorizer
        )
        c_events, c_record_indices, c_excluded = self._append_cancellation_records(
            cancellation_events or [], all_records, repo
        )
        if categorizer is not None:
            for indices in c_record_indices:
                self._apply_category([all_records[i] for i in indices], categorizer)

        return _Batch(
            all_records,
            event_record_indices,
            excluded_count + c_excluded,
            categorizer,
            c_events,
            c_record_indices,
        )

    def _append_new_event_records(
        self,
        new_events: list[dict[str, Any]],
        repo: EventRepository,
        all_records: list[dict[str, Any]],
        categorizer: HistoryCategorizer | None,
    ) -> tuple[list[list[int]], int]:
        """Append wallet records for *new_events* into *all_records* in-place.

        Returns:
            ``(event_record_indices, excluded_count)``
        """
        event_record_indices: list[list[int]] = [[] for _ in new_events]
        excluded_count = 0

        for event_idx, event in enumerate(new_events):
            self._warn_unknown_event_type(event)
            recs = build_records_for_event(
                event,
                cash_account_id=self._cfg.wallet_cash_account_id,
                portfolio_account_id=self._cfg.wallet_portfolio_account_id,
                label_ids=self._cfg.label_ids,
            )
            if not recs:
                repo.mark_processed(event)
                excluded_count += 1
                reason = "CANCELED" if is_canceled_event(event) else "zero amount"
                log.info("Excluded event %s (%s)", dedup_event_id(event), reason)
                continue
            if categorizer is not None:
                self._apply_category(recs, categorizer)
            for r in recs:
                event_record_indices[event_idx].append(len(all_records))
                all_records.append(r)

        return event_record_indices, excluded_count

    def _append_cancellation_records(
        self,
        cancellation_events: list[dict[str, Any]],
        all_records: list[dict[str, Any]],
        repo: EventRepository,
    ) -> tuple[list[dict[str, Any]], list[list[int]], int]:
        """Append reversal records for *cancellation_events* into *all_records* in-place.

        Zero-amount cancellation events produce no reversal record; their
        ``wallet_record_id`` is cleared immediately so that
        ``filter_cancellation_pending`` does not return them on subsequent runs.

        Returns:
            ``(c_events, c_record_indices, excluded_count)`` — parallel lists of events
            that produced at least one reversal record, their record index ranges,
            and the count of events that were excluded (already-reversed or zero-amount).
        """
        c_events: list[dict[str, Any]] = []
        c_record_indices: list[list[int]] = []
        excluded_count = 0
        for c_event in cancellation_events:
            # Skip if the wallet_record_id was already cleared by a previous reversal —
            # this makes build_batch idempotent when called again with the same event.
            if repo.get_wallet_record_id(c_event) is None:
                excluded_count += 1
                log.info(
                    "Excluded already-reversed cancellation event %s",
                    dedup_event_id(c_event),
                )
                continue
            recs = build_cancellation_records(
                c_event,
                cash_account_id=self._cfg.wallet_cash_account_id,
                portfolio_account_id=self._cfg.wallet_portfolio_account_id,
                label_ids=self._cfg.label_ids,
            )
            if not recs:
                repo.mark_processed_force(c_event, wallet_record_id=None)
                excluded_count += 1
                log.info(
                    "Excluded zero-amount cancellation event %s",
                    dedup_event_id(c_event),
                )
                continue
            indices: list[int] = []
            for r in recs:
                indices.append(len(all_records))
                all_records.append(r)
            c_record_indices.append(indices)
            c_events.append(c_event)
        return c_events, c_record_indices, excluded_count

    @staticmethod
    def _apply_category(
        recs: list[dict[str, Any]], categorizer: HistoryCategorizer
    ) -> None:
        """Look up a category for *recs* via *categorizer* and stamp it on each record.

        Uses the ``note`` of the first record as the lookup key — all sub-records
        of a single event share the same note.  No-ops when no category is found.
        """
        note = recs[0].get("note", "")
        category_id = categorizer.get_category_id(note)
        if category_id:
            for r in recs:
                r["categoryId"] = category_id

    def _retry_category_failures(
        self,
        records: list[dict[str, Any]],
        results: list[dict[str, Any]],
        wallet_client: WalletClient,
        *,
        categorizer: HistoryCategorizer | None,
    ) -> list[dict[str, Any]]:
        """Retry records that failed due to an invalid ``categoryId``, once.

        When a categorized record returns an API error, the category cache is
        invalidated (the category may have been deleted since the cache loaded)
        and the record is retried without ``categoryId``.  Records without a
        ``categoryId`` or with no failure are returned unchanged.

        Args:
            records:     The original flat records list submitted to the API.
            results:     Per-item results returned by :meth:`WalletClient.post_records`.
            wallet_client: Client used for the retry POST.
            categorizer: Active :class:`~app.categorizer.HistoryCategorizer`
                         whose cache should be invalidated on failure, or ``None``
                         to skip the retry entirely.

        Returns:
            The results list with retry outcomes merged in (same order / indices).
        """
        if categorizer is None:
            return results

        results_by_index: dict[int, dict[str, Any]] = {
            r.get("inputIndex", i): r for i, r in enumerate(results)
        }

        retry_indices = [
            idx
            for idx, record in enumerate(records)
            if results_by_index.get(idx, {}).get("error") and record.get("categoryId")
        ]
        if not retry_indices:
            return results

        categorizer.invalidate_cache()
        log.warning(
            "Retrying %d record(s) without categoryId after API error (cache invalidated)",
            len(retry_indices),
        )

        retry_records = [
            {k: v for k, v in records[idx].items() if k != "categoryId"}
            for idx in retry_indices
        ]
        retry_results = wallet_client.post_records(retry_records)

        for pos, orig_idx in enumerate(retry_indices):
            if pos < len(retry_results):
                merged: dict[str, Any] = dict(retry_results[pos])
                merged["inputIndex"] = orig_idx
                results_by_index[orig_idx] = merged

        return [results_by_index[k] for k in sorted(results_by_index)]

    def _process_event_result(
        self,
        event: dict[str, Any],
        record_indices: list[int],
        results_by_index: dict[int, dict[str, Any]],
        repo: EventRepository,
        *,
        force: bool = False,
    ) -> tuple[int, int]:
        """Process the API result for one event.

        When *force* is ``True``, the event is persisted via
        :meth:`~app.persistence.EventRepository.mark_processed_force`
        (``INSERT OR REPLACE``) instead of the default ``INSERT OR IGNORE``.
        Use this for the resync path where an existing record must be updated.

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
        if force:
            repo.mark_processed_force(event, wallet_record_id=wallet_record_id)
        else:
            repo.mark_processed(event, wallet_record_id=wallet_record_id)
        return 1, 0

    def _persist_sync_run(self, repo: EventRepository, counts: _SyncCounts) -> None:
        """Write a ``sync_run`` row reflecting *counts*.

        Best-effort: failures are logged as warnings and never interrupt the caller.
        """
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

    def process_results(
        self,
        results: list[dict[str, Any]],
        new_events: list[dict[str, Any]],
        event_record_indices: list[list[int]],
        repo: EventRepository,
        excluded_count: int = 0,
        *,
        force: bool = False,
    ) -> _SyncCounts:
        """Interpret API results, mark successful events as processed, log failures.

        When *force* is ``True``, successful events are persisted via
        ``INSERT OR REPLACE`` (upsert) so that an existing ``wallet_record_id``
        is updated.  Use this for the resync path.
        """
        results_by_index = {r.get("inputIndex", i): r for i, r in enumerate(results)}

        synced = 0
        failed = 0
        for event_idx, event in enumerate(new_events):
            s, f = self._process_event_result(
                event,
                event_record_indices[event_idx],
                results_by_index,
                repo,
                force=force,
            )
            synced += s
            failed += f

        repo.commit()
        counts = _SyncCounts(synced=synced, excluded=excluded_count, failed=failed)
        self._persist_sync_run(repo, counts)
        return counts

    # ------------------------------------------------------------------
    # run() orchestration helpers
    # ------------------------------------------------------------------

    def notify_fetch_summary(
        self,
        since: datetime,
        recent_events: list[dict[str, Any]],
        new_events: list[dict[str, Any]],
    ) -> int:
        """Log and notify about the fetch results.

        Args:
            since:         Start of the lookback window.
            recent_events: All events within the lookback window.
            new_events:    Subset of *recent_events* not yet processed (after dedup).

        Returns:
            The number of skipped (already-processed) events.
        """
        skipped_count = len(recent_events) - len(new_events)
        log.info("%d new events to sync (after dedup)", len(new_events))
        self._notifier.fetch_summary(
            since=since.strftime("%Y-%m-%d"),
            until=datetime.now(UTC).strftime("%Y-%m-%d"),
            fetched=len(recent_events),
            new=len(new_events),
            skipped=skipped_count,
        )
        return skipped_count

    def submit_batch(
        self,
        batch: _Batch,
        wallet_client: WalletClient,
        repo: EventRepository,
        *,
        new_events: list[dict[str, Any]],
        force: bool = False,
    ) -> _SyncCounts:
        """Submit *batch* to the Wallet API and persist the results.

        When *batch* has no records (all events were excluded), the repository
        is committed immediately and the excluded count is returned.

        Args:
            batch:         Pre-built batch from :meth:`build_batch`.
            wallet_client: Authenticated wallet API client.
            repo:          Open repository for marking events as processed.
            new_events:    The same list of new events passed to :meth:`build_batch`.
            force:         When ``True``, persist results via ``INSERT OR REPLACE``
                           so that an existing ``wallet_record_id`` is updated.
                           Use this for the resync path.

        Returns:
            A :class:`_SyncCounts` with synced / excluded / failed tallies.
        """
        if not batch.records:
            repo.commit()
            return _SyncCounts(excluded=batch.excluded_count)

        results = wallet_client.post_records(batch.records)
        results = self._retry_category_failures(
            batch.records,
            results,
            wallet_client,
            categorizer=batch.categorizer,
        )
        log.debug("API results: %s", results)

        # Clear wallet_record_id for cancellation events so they are not reversed again.
        # A result is considered successful when it has no 'error' key — consistent with
        # how process_results / _process_event_result treat API responses.
        results_by_index = {r.get("inputIndex", i): r for i, r in enumerate(results)}
        failed_indices = {idx for idx, r in results_by_index.items() if r.get("error")}
        c_synced = 0
        c_failed = 0
        for indices, c_event in zip(
            batch.cancellation_record_indices, batch.cancellation_events, strict=True
        ):
            s, f = self._process_cancellation_result(
                c_event, indices, results_by_index, failed_indices, repo
            )
            c_synced += s
            c_failed += f

        counts = self.process_results(
            results,
            new_events,
            batch.event_record_indices,
            repo,
            excluded_count=batch.excluded_count,
            force=force,
        )
        counts.synced += c_synced
        counts.failed += c_failed
        # Re-persist sync_run with the final counts that include cancellation reversals.
        # process_results already wrote a preliminary row; this overwrites it via upsert.
        if c_synced or c_failed:
            self._persist_sync_run(repo, counts)
        return counts

    # resync_day helpers
    # ------------------------------------------------------------------

    def _process_cancellation_result(
        self,
        c_event: dict[str, Any],
        indices: list[int],
        results_by_index: dict[int, dict[str, Any]],
        failed_indices: set[int],
        repo: EventRepository,
    ) -> tuple[int, int]:
        """Process the API result for one cancellation reversal.

        Clears ``wallet_record_id`` on success (so a subsequent sync does not
        post a second reversal) and logs/notifies on failure.

        Returns:
            ``(synced_delta, failed_delta)`` — exactly one of the two is 1.
        """
        all_present = all(i in results_by_index for i in indices)
        any_failed = any(i in failed_indices for i in indices)
        if all_present and not any_failed:
            repo.mark_processed_force(c_event, wallet_record_id=None)
            log.info(
                "Cleared wallet_record_id for canceled event %s",
                dedup_event_id(c_event),
            )
            return 1, 0

        eid = dedup_event_id(c_event)
        missing = [i for i in indices if i not in results_by_index]
        if missing:
            log.error(
                "Cancellation reversal for event %s has no API result"
                " for record index(es): %s",
                eid,
                missing,
            )
            self._notifier.missing_api_result(eid, missing)
        else:
            for i in indices:
                r = results_by_index[i]
                if r.get("error"):
                    log.error(
                        "Cancellation reversal for event %s record %d failed: %s",
                        eid,
                        r.get("inputIndex"),
                        r.get("error"),
                    )
        return 0, 1

    def _warn_unknown_event_type(self, event: dict[str, Any]) -> None:
        """Log a warning and notify if *event* carries an unrecognised type."""
        event_type = extract_event_type(event)
        if event_type and event_type not in KNOWN_EVENT_TYPES:
            log.warning(
                "Unknown TR event type %r — falling back to cash handler", event_type
            )
            self._notifier.unknown_event_type(event_type)

    def resync_day(
        self,
        date_str: str,
        repo: EventRepository,
        wallet_client: WalletClient,
    ) -> _SyncCounts:
        """Re-sync all TR events for a specific day, bypassing dedup.

        Uses the same pipeline as the regular sync (:meth:`build_batch` +
        :meth:`submit_batch`) but skips the ``filter_unprocessed`` dedup step
        so that all events for the day are re-processed regardless of whether
        they were seen before.

        CANCELED events are handled via :meth:`~app.persistence.EventRepository\
.filter_cancellation_pending`: if a wallet record already exists, a reversal is
        posted; otherwise the event is excluded.

        All results are persisted via ``INSERT OR REPLACE`` (upsert) so that
        existing ``wallet_record_id`` values are updated in the local DB.

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

        cancellation_events = repo.filter_cancellation_pending(day_events)
        batch = self.build_batch(
            day_events,
            repo,
            wallet_client=wallet_client,
            cancellation_events=cancellation_events,
        )
        return self.submit_batch(
            batch, wallet_client, repo, new_events=day_events, force=True
        )
