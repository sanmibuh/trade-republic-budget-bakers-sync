from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as err:
        raise ValueError(f"{name} must be an integer, got: {raw!r}") from err
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer, got: {value}")
    return value


# Event types that support optional label assignment via LABEL_<EVENT_TYPE> env vars.
LABELABLE_EVENT_TYPES: tuple[str, ...] = (
    "BANK_TRANSACTION_INCOMING",
    "BANK_TRANSACTION_OUTGOING",
    "CARD_TRANSACTION",
    "INTEREST_PAYOUT",
    "INTEREST_PAYMENT",
    "BUY_ORDER",
    "SELL_ORDER",
    "SAVINGS_PLAN",
    "TRADING_SAVINGSPLAN_EXECUTED",
    "SAVEBACK_AGGREGATE",
    "SPARE_CHANGE_AGGREGATE",
    "SAVEBACK",
    "PAYMENT_INBOUND",
)


def _read_label_ids() -> dict[str, str]:
    """Read LABEL_<EVENT_TYPE> env vars and return a mapping of event_type → label_id."""
    return {
        event_type: label_id
        for event_type in LABELABLE_EVENT_TYPES
        if (label_id := os.getenv(f"LABEL_{event_type}", "").strip())
    }


@dataclass(frozen=True)
class Config:
    owner_name: str
    phone_number: str
    pin: str
    wallet_api_key: str
    wallet_cash_account_id: str
    wallet_portfolio_account_id: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    lookback_days: int
    data_dir: Path
    label_ids: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            owner_name=_required_env("OWNER_NAME"),
            phone_number=_required_env("PHONE_NUMBER"),
            pin=_required_env("PIN"),
            wallet_api_key=_required_env("WALLET_API_KEY"),
            wallet_cash_account_id=_required_env("WALLET_CASH_ACCOUNT_ID"),
            wallet_portfolio_account_id=_required_env("WALLET_PORTFOLIO_ACCOUNT_ID"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            lookback_days=_positive_int_env("LOOKBACK_DAYS", default=7),
            data_dir=Path(os.getenv("DATA_DIR", "/app/data")),
            label_ids=_read_label_ids(),
        )
