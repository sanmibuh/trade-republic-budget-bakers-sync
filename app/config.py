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


# Default data directory when DATA_DIR env var is not set.
_DEFAULT_DATA_DIR = "/app/data"

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
    "TRADING_SAVINGSPLAN_EXECUTION_PENDING",
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


_VALID_CATEGORY_STRATEGIES: frozenset[str] = frozenset({"none", "history"})


def _category_strategy_env() -> str:
    raw = os.getenv("CATEGORY_STRATEGY", "none").strip().lower()
    if raw not in _VALID_CATEGORY_STRATEGIES:
        raise ValueError(
            f"CATEGORY_STRATEGY must be one of {sorted(_VALID_CATEGORY_STRATEGIES)}, got: {raw!r}"
        )
    return raw


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
        "data_dir": Path(os.getenv("DATA_DIR", _DEFAULT_DATA_DIR)),
        "allow_insecure_ssl": _bool_env("ALLOW_INSECURE_SSL", default=False),
    }


def read_instances_config_path() -> Path:
    """Return the path to the instances YAML config file from ``INSTANCES_CONFIG`` env var.

    Raises ``ValueError`` when the variable is unset or blank.  All env var reads
    for ``INSTANCES_CONFIG`` must go through this helper — never call ``os.getenv``
    for this variable outside ``config.py``.
    """
    raw = os.getenv("INSTANCES_CONFIG", "").strip()
    if not raw:
        raise ValueError("Missing required environment variable: INSTANCES_CONFIG")
    return Path(raw)


def read_optional_wallet_api_key() -> str | None:
    """Return ``WALLET_API_KEY`` from env, or ``None`` if absent or blank.

    Used by the bot to let an explicit env override take precedence over the
    key stored in the instances YAML.  All ``os.getenv`` calls must stay in
    ``config.py``; callers must use this helper instead of calling
    ``os.getenv`` directly.
    """
    return os.getenv("WALLET_API_KEY", "").strip() or None


def read_data_dir() -> Path:
    """Return the data directory path from the DATA_DIR environment variable."""
    return Path(os.getenv("DATA_DIR", _DEFAULT_DATA_DIR))


def read_telegram_verify_ssl() -> bool:
    """Return the TELEGRAM_VERIFY_SSL setting (default True)."""
    return _bool_env("TELEGRAM_VERIFY_SSL", default=True)


def read_instance() -> str:
    """Return the logical instance name for this container.

    Reads ``INSTANCE`` env var; falls back to ``OWNER_NAME`` lowercased
    (matching the logic in :meth:`Config.from_env`).
    """
    instance = os.getenv("INSTANCE", "").strip()
    if instance:
        return instance
    return os.getenv("OWNER_NAME", _DEFAULT_OWNER_NAME).lower()


def has_valid_session(data_dir: Path) -> bool:
    """Return True if cookies.txt exists and contains at least one non-expired cookie.

    pytr persists the Trade Republic session as a Netscape cookie jar (cookies.txt).
    A file with only expired cookies means the session has ended and the user must
    log in again.  We use stdlib's MozillaCookieJar so the parsing logic is the
    same as pytr's own cookie loading — no custom TSV parsing needed.
    """
    import time
    from http.cookiejar import MozillaCookieJar

    cookies_file = data_dir / "cookies.txt"
    if not cookies_file.exists():
        return False

    jar = MozillaCookieJar(str(cookies_file))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except OSError:
        return False

    now = time.time()
    return any(c.expires is None or c.expires > now for c in jar)


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
    dedup_ttl_days: int
    data_dir: Path
    instance: str = ""
    allow_insecure_ssl: bool = False
    label_ids: dict[str, str] = field(default_factory=dict)
    category_strategy: str = "none"

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
            dedup_ttl_days=_positive_int_env("DEDUP_TTL_DAYS", default=60),
            instance=instance,
            label_ids=_read_label_ids(),
            category_strategy=_category_strategy_env(),
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

    @classmethod
    def from_instances_yaml(
        cls,
        instances_yaml: InstancesConfig,
        wallet_api_key: str | None = None,
    ) -> BackupConfig | None:
        """Build a :class:`BackupConfig` from an :class:`InstancesConfig`.

        Telegram credentials, ``data_dir``, and ``allow_insecure_ssl`` are always
        taken from *instances_yaml* so that the backup service is consistent with
        the sync instances in the single-container deployment.

        ``wallet_api_key`` overrides the key from the first instance (use this to
        propagate ``WALLET_API_KEY`` from the environment when it is set).

        Returns ``None`` when *instances_yaml* has no instances.
        """
        if not instances_yaml.instances:
            return None
        first = instances_yaml.instances[0]
        return cls(
            owner_name="Backup",
            wallet_api_key=wallet_api_key or first.wallet_api_key,
            telegram_bot_token=instances_yaml.telegram_bot_token,
            telegram_chat_id=instances_yaml.telegram_chat_id,
            data_dir=instances_yaml.data_dir / "backup",
            allow_insecure_ssl=instances_yaml.allow_insecure_ssl,
        )


@dataclass(frozen=True)
class BotEnv:
    """Raw environment values needed by the Telegram bot."""

    bot_token: str
    chat_id: str
    telegram_verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> BotEnv:
        return cls(
            bot_token=_required_env("TELEGRAM_BOT_TOKEN"),
            chat_id=_required_env("TELEGRAM_CHAT_ID"),
            telegram_verify_ssl=_bool_env("TELEGRAM_VERIFY_SSL", default=True),
        )


# ---------------------------------------------------------------------------
# Multi-instance config (YAML file — Phase 1 of single-container migration)
# ---------------------------------------------------------------------------

_REQUIRED_INSTANCE_FIELDS: tuple[str, ...] = (
    "phone",
    "pin",
    "wallet_cash_account_id",
    "wallet_portfolio_account_id",
)


@dataclass(frozen=True)
class InstanceConfig:
    """Per-instance configuration loaded from the instances YAML file."""

    name: str
    phone: str
    pin: str
    wallet_api_key: str
    wallet_cash_account_id: str
    wallet_portfolio_account_id: str
    owner_name: str | None = None
    lookback_days: int = 7
    dedup_ttl_days: int = 60
    label_ids: dict[str, str] = field(default_factory=dict)
    category_strategy: str = "none"
    schedule: str | None = None


def _parse_yaml_bool(field_name: str, value: object) -> bool:
    """Parse a YAML boolean field that may arrive as a native bool or a string.

    YAML natively parses unquoted ``true``/``false`` as booleans.  Quoted values
    (e.g. ``"false"``) arrive as strings.  Both forms are accepted; anything else
    raises ``ValueError``.
    """
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    raise ValueError(
        f"{field_name} must be a boolean (true/false/1/0/yes/no), got: {value!r}"
    )


def _validate_instance_name(raw_inst: dict, idx: int) -> str:
    """Extract, coerce, strip, and validate the instance name from a raw dict.

    Allowed characters: ASCII alphanumerics, hyphens, underscores, and dots
    ``[A-Za-z0-9._-]``.  This allowlist ensures names are safe for:
    - filesystem paths (no separators or control chars)
    - shell arguments in cron job lines (no metacharacters)
    """
    import re

    raw_name = raw_inst.get("name")
    name = str(raw_name).strip() if raw_name is not None else ""
    if not name:
        raise ValueError(f"instance at index {idx} is missing 'name'")
    if name in (".", ".."):
        raise ValueError(f"instance name '{name}' must not be '.' or '..'")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise ValueError(
            f"instance name '{name}' contains invalid characters — only ASCII "
            f"alphanumerics, hyphens, underscores, and dots are allowed"
        )
    return name


def _parse_positive_int(name: str, field: str, raw: object, default: int) -> int:
    """Parse *raw* as a positive integer, raising a descriptive ``ValueError`` on failure."""
    try:
        value = int(raw if raw is not None else default)
    except (ValueError, TypeError) as err:
        raise ValueError(
            f"instance '{name}': {field} must be an integer, got: {raw!r}"
        ) from err
    if value <= 0:
        raise ValueError(f"instance '{name}': {field} must be a positive integer")
    return value


def _parse_instance_numerics(name: str, raw_inst: dict) -> tuple[int, int]:
    """Parse and validate lookback_days and dedup_ttl_days for an instance."""
    lookback_days = _parse_positive_int(
        name, "lookback_days", raw_inst.get("lookback_days"), default=7
    )
    dedup_ttl_days = _parse_positive_int(
        name, "dedup_ttl_days", raw_inst.get("dedup_ttl_days"), default=60
    )
    return lookback_days, dedup_ttl_days


def _parse_instance_labels(name: str, raw_inst: dict) -> dict[str, str]:
    """Parse and validate the labels mapping for an instance.

    Keys and values are coerced to ``str``; null or blank values are rejected.
    """
    raw_labels = raw_inst.get("labels")
    if raw_labels is not None and not isinstance(raw_labels, dict):
        raise ValueError(f"instance '{name}': labels must be a mapping")
    if not raw_labels:
        return {}
    result: dict[str, str] = {}
    for k, v in raw_labels.items():
        if v is None:
            raise ValueError(
                f"instance '{name}': label '{k}' has a null value — provide a non-blank string ID"
            )
        str_v = str(v).strip()
        if not str_v:
            raise ValueError(
                f"instance '{name}': label '{k}' has a blank value — provide a non-blank string ID"
            )
        result[str(k)] = str_v
    return result


def _parse_instance(
    raw_inst: object,
    idx: int,
    *,
    global_wallet_api_key: str | None = None,
    global_lookback_days: int | None = None,
    global_category_strategy: str | None = None,
    global_schedule: str | None = None,
) -> InstanceConfig:
    """Parse and validate a single instance entry from the YAML file."""
    if not isinstance(raw_inst, dict):
        raise ValueError(
            f"instance at index {idx} must be a mapping, got {type(raw_inst).__name__}"
        )
    name = _validate_instance_name(raw_inst, idx)
    for required in _REQUIRED_INSTANCE_FIELDS:
        if not raw_inst.get(required):
            raise ValueError(
                f"instance '{name}' is missing required field '{required}'"
            )

    # wallet_api_key: per-instance takes precedence, then global, then error.
    # Both values are stripped and blank-checked to match env var handling.
    raw_inst_key = str(raw_inst.get("wallet_api_key") or "").strip() or None
    raw_wallet_key = raw_inst_key or global_wallet_api_key
    if not raw_wallet_key:
        raise ValueError(
            f"instance '{name}' is missing required field 'wallet_api_key' "
            f"(set it per-instance or under sync.wallet_api_key)"
        )

    # lookback_days / dedup_ttl_days: merge global default into raw dict before parsing.
    effective_raw = dict(raw_inst)
    if "lookback_days" not in effective_raw and global_lookback_days is not None:
        effective_raw["lookback_days"] = global_lookback_days
    lookback_days, dedup_ttl_days = _parse_instance_numerics(name, effective_raw)

    label_ids = _parse_instance_labels(name, raw_inst)

    # category_strategy: per-instance > global > default "none".
    raw_cat = raw_inst.get("category_strategy")
    if raw_cat is None and global_category_strategy is not None:
        raw_cat = global_category_strategy
    category_strategy = str(raw_cat if raw_cat is not None else "none").strip().lower()
    if category_strategy not in _VALID_CATEGORY_STRATEGIES:
        raise ValueError(
            f"instance '{name}': category_strategy must be one of "
            f"{sorted(_VALID_CATEGORY_STRATEGIES)}, got: {category_strategy!r}"
        )

    # schedule: per-instance > global sync.schedule > None.
    raw_schedule = raw_inst.get("schedule")
    effective_schedule: str | None = (
        str(raw_schedule).strip() if raw_schedule is not None else global_schedule
    )

    return InstanceConfig(
        name=name,
        phone=str(raw_inst["phone"]),
        pin=str(raw_inst["pin"]),
        wallet_api_key=str(raw_wallet_key),
        wallet_cash_account_id=str(raw_inst["wallet_cash_account_id"]),
        wallet_portfolio_account_id=str(raw_inst["wallet_portfolio_account_id"]),
        owner_name=raw_inst.get("owner_name") or None,
        lookback_days=lookback_days,
        dedup_ttl_days=dedup_ttl_days,
        label_ids=label_ids,
        category_strategy=category_strategy,
        schedule=effective_schedule or None,
    )


def _parse_sync_section(
    raw_sync: dict,
) -> tuple[list, str | None, int | None, str | None, str | None]:
    """Parse the ``sync:`` mapping and return its five components.

    Returns a tuple of:
    ``(raw_instances_list, global_wallet_key, global_lookback, global_cat, sync_schedule)``
    """
    raw_instances_list = raw_sync.get("instances") or []
    global_wallet_key: str | None = (
        str(raw_sync.get("wallet_api_key") or "").strip() or None
    )
    global_lookback: int | None = None
    raw_gl = raw_sync.get("lookback_days")
    if raw_gl is not None:
        try:
            global_lookback = int(raw_gl)
        except (ValueError, TypeError) as err:
            raise ValueError(
                f"sync.lookback_days must be an integer, got: {raw_gl!r}"
            ) from err
    global_cat: str | None = raw_sync.get("category_strategy") or None
    raw_sched = raw_sync.get("schedule")
    sync_schedule: str | None = (
        str(raw_sched).strip() if raw_sched is not None else None
    ) or None
    return (
        raw_instances_list,
        global_wallet_key,
        global_lookback,
        global_cat,
        sync_schedule,
    )


@dataclass(frozen=True)
class InstancesConfig:
    """Configuration for all sync instances, loaded from a YAML file.

    The file path is read from the ``INSTANCES_CONFIG`` environment variable.
    Each instance gets its own ``data_dir`` subdirectory:
    ``{root_data_dir}/{instance.name}/``.

    Required YAML layout::

        sync:
          schedule: "…"          # global default, overridable per instance (optional)
          wallet_api_key: "…"    # global default, overridable per instance
          lookback_days: 7       # optional
          category_strategy: history  # optional
          instances:
            - name: …
        backup_schedule: "…"     # optional — omit to disable scheduled backups
    """

    instances: list[InstanceConfig]
    data_dir: Path = field(default_factory=lambda: Path(_DEFAULT_DATA_DIR))
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    allow_insecure_ssl: bool = False
    sync_schedule: str | None = None
    backup_schedule: str | None = None

    @classmethod
    def load(cls, path: Path) -> InstancesConfig:
        """Load and validate an instances YAML config file."""
        import yaml  # deferred — only needed when INSTANCES_CONFIG is used

        raw = path.read_text()  # raises FileNotFoundError if absent
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"instances config must be a YAML mapping, got "
                f"{type(data).__name__ if data is not None else 'empty file'}"
            )

        raw_sync = data.get("sync")
        if raw_sync is None:
            raise ValueError(
                "instances config must have a 'sync' section — "
                "see instances.yml.example for the required format"
            )
        if not isinstance(raw_sync, dict):
            raise ValueError("'sync' must be a YAML mapping")

        (
            raw_instances_list,
            global_wallet_key,
            global_lookback,
            global_cat,
            sync_schedule,
        ) = _parse_sync_section(raw_sync)

        if not raw_instances_list:
            raise ValueError("instances config must define at least one instance")

        instances: list[InstanceConfig] = []
        seen_names: set[str] = set()
        for idx, raw_inst in enumerate(raw_instances_list):
            inst = _parse_instance(
                raw_inst,
                idx,
                global_wallet_api_key=global_wallet_key,
                global_lookback_days=global_lookback,
                global_category_strategy=global_cat,
                global_schedule=sync_schedule,
            )
            if inst.name in seen_names:
                raise ValueError(f"duplicate instance name: '{inst.name}'")
            seen_names.add(inst.name)
            instances.append(inst)

        allow_insecure_ssl = _parse_yaml_bool(
            "allow_insecure_ssl", data.get("allow_insecure_ssl", False)
        )

        raw_backup_sched = data.get("backup_schedule")
        backup_schedule: str | None = (
            str(raw_backup_sched).strip() if raw_backup_sched is not None else None
        ) or None

        return cls(
            instances=instances,
            data_dir=Path(data.get("data_dir", _DEFAULT_DATA_DIR)),
            telegram_bot_token=data.get("telegram_bot_token")
            or os.getenv("TELEGRAM_BOT_TOKEN")
            or None,
            telegram_chat_id=data.get("telegram_chat_id")
            or os.getenv("TELEGRAM_CHAT_ID")
            or None,
            allow_insecure_ssl=allow_insecure_ssl,
            sync_schedule=sync_schedule,
            backup_schedule=backup_schedule,
        )

    def get_instance(self, name: str) -> InstanceConfig:
        """Return the ``InstanceConfig`` for *name*, or raise ``ValueError``."""
        for inst in self.instances:
            if inst.name == name:
                return inst
        raise ValueError(f"instance '{name}' not found in instances config")

    def to_config(self, name: str) -> Config:
        """Build a full :class:`Config` for the named instance."""
        inst = self.get_instance(name)
        owner_name = inst.owner_name if inst.owner_name else inst.name.capitalize()
        return Config(
            owner_name=owner_name,
            phone_number=inst.phone,
            pin=inst.pin,
            wallet_api_key=inst.wallet_api_key,
            wallet_cash_account_id=inst.wallet_cash_account_id,
            wallet_portfolio_account_id=inst.wallet_portfolio_account_id,
            telegram_bot_token=self.telegram_bot_token,
            telegram_chat_id=self.telegram_chat_id,
            lookback_days=inst.lookback_days,
            dedup_ttl_days=inst.dedup_ttl_days,
            data_dir=self.data_dir / inst.name,
            instance=inst.name,
            allow_insecure_ssl=self.allow_insecure_ssl,
            label_ids=dict(inst.label_ids),
            category_strategy=inst.category_strategy,
        )
