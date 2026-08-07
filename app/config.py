from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        raise ValueError(f"Missing required environment variable: {name}")
    return value


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

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            owner_name=_required_env("OWNER_NAME"),
            phone_number=_required_env("PHONE_NUMBER"),
            pin=_required_env("PIN"),
            wallet_api_key=_required_env("WALLET_API_KEY"),
            wallet_cash_account_id=_required_env("WALLET_CASH_ACCOUNT_ID"),
            wallet_portfolio_account_id=_required_env("WALLET_PORTFOLIO_ACCOUNT_ID"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
            lookback_days=int(os.getenv("LOOKBACK_DAYS", "7")),
            data_dir=Path(os.getenv("DATA_DIR", "/app/data")),
        )
