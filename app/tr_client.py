from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def connect_trade_republic(
    phone_number: str,
    pin: str,
    data_dir: Path,
    on_login_required: Callable[[], None] | None = None,
    on_login_success: Callable[[], None] | None = None,
) -> Any:
    from pytr.api import TradeRepublicApi

    client = TradeRepublicApi(
        phone_no=phone_number,
        pin=pin,
        save_cookies=True,
        credentials_file=str(data_dir / "credentials.json"),
        cookies_file=str(data_dir / "cookies.txt"),
        use_v2_login=True,
    )

    # Try to reuse an existing session first.
    if client.resume_websession():
        return client

    # No saved session — notify and run the interactive login flow.
    if on_login_required:
        on_login_required()

    try:
        client.initiate_weblogin()
        verify_code = input("Enter the 2FA code sent by Trade Republic: ").strip()
        client.complete_weblogin(verify_code=verify_code)
    except Exception as exc:
        raise LoginFailedError("2FA login failed") from exc

    if on_login_success:
        on_login_success()

    return client


class LoginFailedError(Exception):
    """Raised when the interactive Trade Republic 2FA login fails."""


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
            coroutine_or_result = method(*args)
            # pytr methods are async — use run_blocking if available
            run_blocking = getattr(client, "run_blocking", None)
            if run_blocking is not None and hasattr(coroutine_or_result, "__await__"):
                result = run_blocking(coroutine_or_result)
            else:
                result = coroutine_or_result
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
