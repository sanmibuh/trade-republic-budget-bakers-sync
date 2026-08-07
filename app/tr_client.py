from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def connect_trade_republic(phone_number: str, data_dir: Path) -> Any:
    from pytr.api import TradeRepublicApi

    # pytr stores and reuses session files from this path.
    client = TradeRepublicApi(phone_no=phone_number, save_cookies=True, cookie_path=str(data_dir))

    for method_name in ("resume_websession", "resume_session", "login"):
        method = getattr(client, method_name, None)
        if method is None:
            continue
        try:
            method()
            break
        except TypeError:
            continue

    return client


def fetch_timeline_events(client: Any, since: datetime) -> list[dict[str, Any]]:
    providers = [
        ("timeline", (since,)),
        ("timeline", tuple()),
        ("get_timeline", (since,)),
        ("get_timeline", tuple()),
        ("timeline_transactions", (since,)),
        ("timeline_transactions", tuple()),
    ]

    last_error: Exception | None = None
    for method_name, args in providers:
        method = getattr(client, method_name, None)
        if method is None:
            continue
        try:
            result = method(*args)
            if result is None:
                return []
            if isinstance(result, list):
                return [item for item in result if isinstance(item, dict)]
            if isinstance(result, dict):
                items = result.get("items") or result.get("data") or []
                return [item for item in items if isinstance(item, dict)]
            return []
        except TypeError as exc:
            last_error = exc
            continue

    if last_error:
        raise RuntimeError("Unable to fetch timeline events with known pytr methods") from last_error
    raise RuntimeError("No supported timeline method found on pytr client")
