from __future__ import annotations

import csv
import hashlib
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from requests import HTTPError

from app.logging_setup import setup_logging
from app.notifier import notify_authentication_required, notify_error, notify_fetch_summary, notify_login_failed, notify_login_required, notify_login_success, notify_sync_complete
from app.tr_client import connect_trade_republic, fetch_timeline_events, LoginFailedError
from app.wallet_client import WalletClient, normalize_event_time, build_records_for_event

try:
    from pytr.exceptions import AuthenticationError
except Exception:  # pragma: no cover
    AuthenticationError = Exception


DATA_DIR = Path("/app/data")
OUTPUT_DIR = Path("/app/output")
DB_PATH = DATA_DIR / "processed_events.db"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS processed_events (event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL)"
    )
    conn.commit()
    return conn


def _event_id(event: dict[str, Any]) -> str:
    for key in ("id", "eventId", "event_id"):
        value = event.get(key)
        if value:
            return str(value)
    return ""


def _dedup_event_id(event: dict[str, Any]) -> str:
    event_id = _event_id(event)
    if event_id:
        return event_id

    seed = "|".join(
        [
            str(event.get("eventType") or event.get("type") or event.get("event_type") or ""),
            normalize_event_time(event),
            str(event.get("amount") or event.get("value") or ""),
            str(event.get("title") or event.get("name") or event.get("description") or ""),
        ]
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"hash:{digest}"


def _filter_unprocessed_events(events: list[dict[str, Any]], conn: sqlite3.Connection) -> list[dict[str, Any]]:
    events_with_ids = [(event, _dedup_event_id(event)) for event in events]
    ids = [dedup_id for _, dedup_id in events_with_ids]
    if not ids:
        return []

    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT event_id FROM processed_events WHERE event_id IN ({placeholders})", ids
    ).fetchall()
    processed = {row[0] for row in rows}

    return [event for event, dedup_id in events_with_ids if dedup_id not in processed]


def _filter_by_lookback(events: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for event in events:
        event_time = normalize_event_time(event)
        parsed: datetime | None = None
        try:
            parsed = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed is None or parsed >= since:
            filtered.append(event)
    return filtered


def _mark_processed(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    event_id = _dedup_event_id(event)
    event_time = normalize_event_time(event)
    conn.execute(
        "INSERT OR IGNORE INTO processed_events (event_id, timestamp) VALUES (?, ?)",
        (event_id, event_time),
    )


def _backup_csv(owner_name: str, events: list[dict[str, Any]]) -> Path:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    backup_file = OUTPUT_DIR / f"TradeRepublic_{owner_name}_{month}.csv"
    headers = ["event_id", "event_type", "timestamp", "raw"]

    file_has_data = backup_file.exists() and backup_file.stat().st_size > 0
    with backup_file.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        if not file_has_data:
            writer.writeheader()

        for event in events:
            writer.writerow(
                {
                    "event_id": _dedup_event_id(event),
                    "event_type": event.get("eventType") or event.get("type") or event.get("event_type") or "",
                    "timestamp": normalize_event_time(event),
                    "raw": str(event),
                }
            )
    return backup_file


def run() -> int:
    owner_name = _required_env("OWNER_NAME")
    phone_number = _required_env("PHONE_NUMBER")
    pin = _required_env("PIN")
    wallet_api_key = _required_env("WALLET_API_KEY")
    wallet_cash_account_id = _required_env("WALLET_CASH_ACCOUNT_ID")
    wallet_portfolio_account_id = _required_env("WALLET_PORTFOLIO_ACCOUNT_ID")

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
    lookback_days = int(os.getenv("LOOKBACK_DAYS", "7"))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log = setup_logging(DATA_DIR)
    log.info("Starting sync for owner: %s", owner_name)

    conn = _init_db(DB_PATH)
    try:
        wallet_client = WalletClient(api_key=wallet_api_key)

        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        log.info("Fetching events since %s", since.isoformat())

        try:
            tr_client = connect_trade_republic(
                phone_number=phone_number,
                pin=pin,
                data_dir=DATA_DIR,
                on_login_required=lambda: notify_login_required(
                    bot_token=telegram_bot_token,
                    chat_id=telegram_chat_id,
                    owner_name=owner_name,
                ),
                on_login_success=lambda: notify_login_success(
                    bot_token=telegram_bot_token,
                    chat_id=telegram_chat_id,
                    owner_name=owner_name,
                ),
            )
            log.info("Trade Republic session established")
            events = fetch_timeline_events(tr_client, since=since)
            log.info("Fetched %d timeline events", len(events))
        except LoginFailedError:
            log.exception("Login failed")
            notify_login_failed(
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                owner_name=owner_name,
            )
            return 1
        except AuthenticationError:
            log.exception("Authentication error")
            notify_authentication_required(
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                owner_name=owner_name,
            )
            return 1
        except HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            log.exception("HTTP error (status=%s)", status)
            if status == 401:
                notify_authentication_required(
                    bot_token=telegram_bot_token,
                    chat_id=telegram_chat_id,
                    owner_name=owner_name,
                )
                return 1
            notify_error(bot_token=telegram_bot_token, chat_id=telegram_chat_id, owner_name=owner_name, error=exc)
            raise
        except Exception as exc:
            log.exception("Unexpected error during TR connection/fetch")
            notify_error(bot_token=telegram_bot_token, chat_id=telegram_chat_id, owner_name=owner_name, error=exc)
            raise

        recent_events = _filter_by_lookback(events, since)
        new_events = _filter_unprocessed_events(recent_events, conn)
        skipped_count = len(recent_events) - len(new_events)
        log.info("%d new events to sync (after dedup)", len(new_events))

        notify_fetch_summary(
            bot_token=telegram_bot_token,
            chat_id=telegram_chat_id,
            owner_name=owner_name,
            since=since.strftime("%Y-%m-%d"),
            until=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            fetched=len(recent_events),
            new=len(new_events),
            skipped=skipped_count,
        )

        try:
            # Build all record payloads in one pass, tracking which event each belongs to.
            all_records: list[dict] = []
            event_record_indices: list[list[int]] = [[] for _ in new_events]

            for event_idx, event in enumerate(new_events):
                recs = build_records_for_event(
                    event,
                    cash_account_id=wallet_cash_account_id,
                    portfolio_account_id=wallet_portfolio_account_id,
                )
                for r in recs:
                    event_record_indices[event_idx].append(len(all_records))
                    all_records.append(r)

            synced_count = 0
            if all_records:
                results = wallet_client.post_records(all_records)
                result_by_index = {r.get("inputIndex", i): r for i, r in enumerate(results)}

                for event_idx, event in enumerate(new_events):
                    record_indices = event_record_indices[event_idx]
                    failures = [
                        result_by_index.get(i, {})
                        for i in record_indices
                        if not result_by_index.get(i, {}).get("success")
                    ]
                    if not failures:
                        _mark_processed(conn, event)
                        synced_count += 1
                    else:
                        event_id = _dedup_event_id(event)
                        for f in failures:
                            log.error(
                                "Event %s record %d failed: %s",
                                event_id,
                                f.get("inputIndex"),
                                f.get("error"),
                            )
                conn.commit()

            _backup_csv(owner_name=owner_name, events=new_events)
            log.info("Sync complete. %d/%d events synced.", synced_count, len(new_events))

            failed_count = len(new_events) - synced_count
            notify_sync_complete(
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                owner_name=owner_name,
                synced=synced_count,
                failed=failed_count,
                skipped=skipped_count,
            )
        except Exception as exc:
            log.exception("Error syncing events to wallet")
            notify_error(bot_token=telegram_bot_token, chat_id=telegram_chat_id, owner_name=owner_name, error=exc)
            raise

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
