from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from requests import HTTPError

from app.config import Config
from app.logging_setup import setup_logging
from app.notifier import (
    notify_authentication_required,
    notify_error,
    notify_fetch_summary,
    notify_login_failed,
    notify_login_required,
    notify_login_success,
    notify_sync_complete,
)
from app.persistence import backup_csv, dedup_event_id, filter_unprocessed, init_db, mark_processed
from app.tr_client import LoginFailedError, connect_trade_republic, fetch_timeline_events
from app.tr_mapper import build_records_for_event
from app.wallet_client import WalletClient

try:
    from pytr.exceptions import AuthenticationError
except Exception:  # pragma: no cover
    AuthenticationError = Exception


def _filter_by_lookback(events: list[dict], since: datetime) -> list[dict]:
    from app.tr_mapper import normalize_event_time
    filtered = []
    for event in events:
        event_time = normalize_event_time(event)
        parsed = None
        try:
            parsed = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed is None or parsed >= since:
            filtered.append(event)
    return filtered


def run() -> int:
    cfg = Config.from_env()

    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    log = setup_logging(cfg.data_dir)
    log.info("Starting sync for owner: %s", cfg.owner_name)

    conn = init_db(cfg.data_dir / "processed_events.db")
    try:
        wallet_client = WalletClient(api_key=cfg.wallet_api_key)
        since = datetime.now(timezone.utc) - timedelta(days=cfg.lookback_days)
        log.info("Fetching events since %s", since.isoformat())

        try:
            tr_client = connect_trade_republic(
                phone_number=cfg.phone_number,
                pin=cfg.pin,
                data_dir=cfg.data_dir,
                on_login_required=lambda: notify_login_required(
                    bot_token=cfg.telegram_bot_token,
                    chat_id=cfg.telegram_chat_id,
                    owner_name=cfg.owner_name,
                ),
                on_login_success=lambda: notify_login_success(
                    bot_token=cfg.telegram_bot_token,
                    chat_id=cfg.telegram_chat_id,
                    owner_name=cfg.owner_name,
                ),
            )
            log.info("Trade Republic session established")
            events = fetch_timeline_events(tr_client, since=since)
            log.info("Fetched %d timeline events", len(events))
        except LoginFailedError:
            log.exception("Login failed")
            notify_login_failed(
                bot_token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                owner_name=cfg.owner_name,
            )
            return 1
        except AuthenticationError:
            log.exception("Authentication error")
            notify_authentication_required(
                bot_token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                owner_name=cfg.owner_name,
            )
            return 1
        except HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            log.exception("HTTP error (status=%s)", status)
            if status == 401:
                notify_authentication_required(
                    bot_token=cfg.telegram_bot_token,
                    chat_id=cfg.telegram_chat_id,
                    owner_name=cfg.owner_name,
                )
                return 1
            notify_error(bot_token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id, owner_name=cfg.owner_name, error=exc)
            raise
        except Exception as exc:
            log.exception("Unexpected error during TR connection/fetch")
            notify_error(bot_token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id, owner_name=cfg.owner_name, error=exc)
            raise

        recent_events = _filter_by_lookback(events, since)
        new_events = filter_unprocessed(recent_events, conn)
        skipped_count = len(recent_events) - len(new_events)
        log.info("%d new events to sync (after dedup)", len(new_events))

        notify_fetch_summary(
            bot_token=cfg.telegram_bot_token,
            chat_id=cfg.telegram_chat_id,
            owner_name=cfg.owner_name,
            since=since.strftime("%Y-%m-%d"),
            until=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            fetched=len(recent_events),
            new=len(new_events),
            skipped=skipped_count,
        )

        synced_count = 0
        excluded_count = 0
        try:
            all_records: list[dict] = []
            event_record_indices: list[list[int]] = [[] for _ in new_events]

            for event_idx, event in enumerate(new_events):
                recs = build_records_for_event(
                    event,
                    cash_account_id=cfg.wallet_cash_account_id,
                    portfolio_account_id=cfg.wallet_portfolio_account_id,
                )
                if not recs:
                    mark_processed(conn, event)
                    excluded_count += 1
                    log.info("Excluded zero-amount event %s", dedup_event_id(event))
                    continue
                for r in recs:
                    event_record_indices[event_idx].append(len(all_records))
                    all_records.append(r)

            if all_records:
                results = wallet_client.post_records(all_records)
                log.debug("API results: %s", results)
                failed_by_index = {
                    r.get("inputIndex", i): r
                    for i, r in enumerate(results)
                    if r.get("error")
                }

                for event_idx, event in enumerate(new_events):
                    record_indices = event_record_indices[event_idx]
                    if not record_indices:
                        continue
                    failures = [failed_by_index[i] for i in record_indices if i in failed_by_index]
                    if not failures:
                        mark_processed(conn, event)
                        synced_count += 1
                    else:
                        eid = dedup_event_id(event)
                        for f in failures:
                            log.error(
                                "Event %s record %d failed: %s",
                                eid,
                                f.get("inputIndex"),
                                f.get("error"),
                            )
                conn.commit()

            backup_csv(output_dir=cfg.output_dir, owner_name=cfg.owner_name, events=new_events)
            log.info("Sync complete. %d/%d events synced. %d excluded.", synced_count, len(new_events), excluded_count)
        except Exception as exc:
            log.exception("Error syncing events to wallet")
            notify_error(bot_token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id, owner_name=cfg.owner_name, error=exc)
            raise
        finally:
            failed_count = len(new_events) - synced_count - excluded_count
            sent = notify_sync_complete(
                bot_token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                owner_name=cfg.owner_name,
                synced=synced_count,
                failed=failed_count,
                skipped=skipped_count,
                excluded=excluded_count,
            )
            if not sent:
                log.warning("notify_sync_complete: Telegram message not sent (no credentials or request failed)")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
