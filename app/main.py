from __future__ import annotations

import csv
import hashlib
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from requests import HTTPError

from app.notifier import notify_authentication_required, notify_error, notify_login_failed, notify_login_required, notify_login_success
from app.tr_client import connect_trade_republic, fetch_timeline_events, LoginFailedError
from app.wallet_client import WalletClient, normalize_event_time, sync_event_to_wallet

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

    conn = _init_db(DB_PATH)
    try:
        wallet_client = WalletClient(api_key=wallet_api_key)

        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

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
            events = fetch_timeline_events(tr_client, since=since)
        except LoginFailedError:
            notify_login_failed(
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                owner_name=owner_name,
            )
            return 1
        except AuthenticationError:
            notify_authentication_required(
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                owner_name=owner_name,
            )
            return 1
        except HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
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
            notify_error(bot_token=telegram_bot_token, chat_id=telegram_chat_id, owner_name=owner_name, error=exc)
            raise

        recent_events = _filter_by_lookback(events, since)
        new_events = _filter_unprocessed_events(recent_events, conn)

        try:
            for event in new_events:
                sync_event_to_wallet(
                    wallet_client,
                    event,
                    cash_account_id=wallet_cash_account_id,
                    portfolio_account_id=wallet_portfolio_account_id,
                )
                _mark_processed(conn, event)
                conn.commit()

            _backup_csv(owner_name=owner_name, events=new_events)
        except Exception as exc:
            notify_error(bot_token=telegram_bot_token, chat_id=telegram_chat_id, owner_name=owner_name, error=exc)
            raise

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(run())
