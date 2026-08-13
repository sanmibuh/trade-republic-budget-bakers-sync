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


# Default owner name used when OWNER_NAME env var is not set.
# The backup service intentionally omits OWNER_NAME; sync services set it explicitly.
_DEFAULT_OWNER_NAME = "Backup"

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


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise ValueError(f"{name} must be a boolean (true/false/1/0/yes/no), got: {raw!r}")


def _read_notifier_env() -> dict[str, object]:
    """Read env vars shared by Config and BackupConfig."""
    return {
        "owner_name": os.getenv("OWNER_NAME", _DEFAULT_OWNER_NAME),
        "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "data_dir": Path(os.getenv("DATA_DIR", "/app/data")),
        "allow_insecure_ssl": _bool_env("ALLOW_INSECURE_SSL", default=False),
    }


def read_data_dir() -> Path:
    """Return the data directory path from the DATA_DIR environment variable."""
    return Path(os.getenv("DATA_DIR", "/app/data"))


@dataclass(frozen=True)
class Config:
    """Full config for the sync command. Requires Trade Republic and Wallet credentials."""

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
    instance: str = ""
    allow_insecure_ssl: bool = False
    label_ids: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Config:
        notifier_env = _read_notifier_env()
        instance = (
            os.getenv("INSTANCE", "").strip() or str(notifier_env["owner_name"]).lower()
        )
        return cls(
            **notifier_env,
            phone_number=_required_env("PHONE_NUMBER"),
            pin=_required_env("PIN"),
            wallet_api_key=_required_env("WALLET_API_KEY"),
            wallet_cash_account_id=_required_env("WALLET_CASH_ACCOUNT_ID"),
            wallet_portfolio_account_id=_required_env("WALLET_PORTFOLIO_ACCOUNT_ID"),
            lookback_days=_positive_int_env("LOOKBACK_DAYS", default=7),
            instance=instance,
            label_ids=_read_label_ids(),
        )


@dataclass(frozen=True)
class BackupConfig:
    """Config for the backup command. Only requires WALLET_API_KEY — no TR credentials."""

    owner_name: str
    wallet_api_key: str
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    data_dir: Path
    allow_insecure_ssl: bool = False

    @classmethod
    def from_env(cls) -> BackupConfig:
        return cls(
            **_read_notifier_env(),
            wallet_api_key=_required_env("WALLET_API_KEY"),
        )


@dataclass(frozen=True)
class BotEnv:
    """Raw environment values needed by the Telegram bot."""

    bot_token: str
    chat_id: str
    instances_raw: str
    container_prefix: str
    backup_service: str
    telegram_verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> BotEnv:
        return cls(
            bot_token=_required_env("TELEGRAM_BOT_TOKEN"),
            chat_id=_required_env("TELEGRAM_CHAT_ID"),
            instances_raw=_required_env("INSTANCES"),
            container_prefix=_required_env("CONTAINER_PREFIX"),
            backup_service=os.getenv("BACKUP_SERVICE", "backup").strip(),
            telegram_verify_ssl=_bool_env("TELEGRAM_VERIFY_SSL", default=True),
        )
