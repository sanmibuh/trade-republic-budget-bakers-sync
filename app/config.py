from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Default data directory.
_DEFAULT_DATA_DIR = "/app/data"

# Path to the instances YAML config file (mounted via Docker volume).
# Can be overridden by the INSTANCES_CONFIG env var for local development.
INSTANCES_CONFIG_PATH = Path(os.getenv("INSTANCES_CONFIG", "/app/config/instances.yml"))

# Event types that support optional label assignment (configured in instances.yml).
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

_VALID_CATEGORY_STRATEGIES: frozenset[str] = frozenset({"none", "history"})


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

    @property
    def shared_db_path(self) -> Path:
        """Path to the shared ``sync.db`` at the root data directory level.

        ``data_dir`` is ``{root}/tr_session_{instance}``; the shared database
        lives at ``{root}/sync.db`` — one level above the session directory.
        """
        return self.data_dir.parent / "sync.db"

    @property
    def twofa_code_file(self) -> Path:
        """Path to the instance-specific 2FA code file at the root data level.

        Format: ``{root}/.tr_2fa_code_{instance}``
        """
        return self.data_dir.parent / f".tr_2fa_code_{self.instance}"

    @property
    def twofa_pending_file(self) -> Path:
        """Path to the instance-specific 2FA pending marker at the root data level.

        Format: ``{root}/.tr_2fa_pending_{instance}``
        """
        return self.data_dir.parent / f".tr_2fa_pending_{self.instance}"


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
    def from_instances_yaml(
        cls,
        instances_yaml: InstancesConfig,
    ) -> BackupConfig | None:
        """Build a :class:`BackupConfig` from an :class:`InstancesConfig`.

        Telegram credentials, ``data_dir``, and ``allow_insecure_ssl`` are taken
        from *instances_yaml*. ``wallet_api_key`` is taken from the first instance.

        Returns ``None`` when *instances_yaml* has no instances.
        """
        if not instances_yaml.instances:
            return None
        first = instances_yaml.instances[0]
        return cls(
            owner_name="Backup",
            wallet_api_key=first.wallet_api_key,
            telegram_bot_token=instances_yaml.telegram_bot_token,
            telegram_chat_id=instances_yaml.telegram_chat_id,
            data_dir=instances_yaml.data_dir,
            allow_insecure_ssl=instances_yaml.allow_insecure_ssl,
        )


# ---------------------------------------------------------------------------
# Multi-instance config (YAML file)
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


def _validate_instance_name(raw_inst: dict[str, Any], idx: int) -> str:
    """Extract, coerce, strip, and validate the instance name from a raw dict.

    Allowed characters: ASCII alphanumerics, hyphens, underscores, and dots
    ``[A-Za-z0-9._-]``.  This allowlist ensures names are safe for:
    - filesystem paths (no separators or control chars)
    - shell arguments in cron job lines (no metacharacters)
    """
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


def _parse_positive_int(name: str, field_name: str, raw: object, default: int) -> int:
    """Parse *raw* as a positive integer, raising a descriptive ``ValueError`` on failure."""
    try:
        value = int(raw if raw is not None else default)
    except (ValueError, TypeError) as err:
        raise ValueError(
            f"instance '{name}': {field_name} must be an integer, got: {raw!r}"
        ) from err
    if value <= 0:
        raise ValueError(f"instance '{name}': {field_name} must be a positive integer")
    return value


def _parse_instance_numerics(name: str, raw_inst: dict[str, Any]) -> tuple[int, int]:
    """Parse and validate lookback_days and dedup_ttl_days for an instance."""
    lookback_days = _parse_positive_int(
        name, "lookback_days", raw_inst.get("lookback_days"), default=7
    )
    dedup_ttl_days = _parse_positive_int(
        name, "dedup_ttl_days", raw_inst.get("dedup_ttl_days"), default=60
    )
    return lookback_days, dedup_ttl_days


def _parse_instance_labels(name: str, raw_inst: dict[str, Any]) -> dict[str, str]:
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


def _resolve_category_strategy(
    name: str,
    raw_inst: dict[str, Any],
    global_category_strategy: str | None,
) -> str:
    """Resolve ``category_strategy`` for an instance with global inheritance and validation.

    Priority: per-instance → global default → ``"none"``.
    Raises ``ValueError`` when the resolved value is not in :data:`_VALID_CATEGORY_STRATEGIES`.
    """
    raw_cat = raw_inst.get("category_strategy")
    if raw_cat is None and global_category_strategy is not None:
        raw_cat = global_category_strategy
    strategy = str(raw_cat if raw_cat is not None else "none").strip().lower()
    if strategy not in _VALID_CATEGORY_STRATEGIES:
        raise ValueError(
            f"instance '{name}': category_strategy must be one of "
            f"{sorted(_VALID_CATEGORY_STRATEGIES)}, got: {strategy!r}"
        )
    return strategy


def _resolve_instance_wallet_key(
    name: str,
    raw_inst: dict[str, Any],
    global_wallet_api_key: str | None,
) -> str:
    """Resolve ``wallet_api_key`` for a single instance.

    Priority: per-instance → global → error.
    A present-but-blank or null per-instance key is rejected immediately so it
    cannot silently inherit the global key.
    """
    if "wallet_api_key" in raw_inst:
        raw = raw_inst["wallet_api_key"]
        key = str(raw).strip() if raw is not None else ""
        if not key:
            raise ValueError(
                f"instance '{name}' has a blank 'wallet_api_key' — "
                f"provide a valid key or remove the field to inherit the global key"
            )
        return key
    if not global_wallet_api_key:
        raise ValueError(
            f"instance '{name}' is missing required field 'wallet_api_key' "
            f"(set it per-instance or under sync.wallet_api_key)"
        )
    return global_wallet_api_key


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

    wallet_api_key = _resolve_instance_wallet_key(name, raw_inst, global_wallet_api_key)

    # lookback_days / dedup_ttl_days: merge global default into raw dict before parsing.
    effective_raw = dict(raw_inst)
    if effective_raw.get("lookback_days") is None and global_lookback_days is not None:
        effective_raw["lookback_days"] = global_lookback_days
    lookback_days, dedup_ttl_days = _parse_instance_numerics(name, effective_raw)

    label_ids = _parse_instance_labels(name, raw_inst)

    category_strategy = _resolve_category_strategy(
        name, raw_inst, global_category_strategy
    )

    # schedule: per-instance > global sync.schedule > None.
    # A present-but-blank per-instance value is rejected — omit the field to inherit global.
    raw_schedule = raw_inst.get("schedule")
    if raw_schedule is not None:
        effective_schedule: str | None = str(raw_schedule).strip() or None
        if effective_schedule is None:
            raise ValueError(
                f"instance '{name}' has a blank 'schedule' — "
                f"provide a valid cron expression or remove the field to inherit the global schedule"
            )
        _validate_cron_schedule(f"instance '{name}' schedule", effective_schedule)
    else:
        effective_schedule = global_schedule

    return InstanceConfig(
        name=name,
        phone=str(raw_inst["phone"]),
        pin=str(raw_inst["pin"]),
        wallet_api_key=wallet_api_key,
        wallet_cash_account_id=str(raw_inst["wallet_cash_account_id"]),
        wallet_portfolio_account_id=str(raw_inst["wallet_portfolio_account_id"]),
        owner_name=raw_inst.get("owner_name") or None,
        lookback_days=lookback_days,
        dedup_ttl_days=dedup_ttl_days,
        label_ids=label_ids,
        category_strategy=category_strategy,
        schedule=effective_schedule or None,
    )


def _parse_global_wallet_key(raw_sync: dict[str, Any]) -> str | None:
    """Return ``sync.wallet_api_key`` stripped, or ``None`` if the field is absent.

    Raises ``ValueError`` when the field is present but blank or null.
    """
    if "wallet_api_key" not in raw_sync:
        return None
    raw = raw_sync["wallet_api_key"]
    key = str(raw).strip() if raw is not None else ""
    if not key:
        raise ValueError(
            "sync.wallet_api_key is present but blank — "
            "provide a valid key or remove the field"
        )
    return key


def _parse_global_lookback(raw_sync: dict[str, Any]) -> int | None:
    """Return ``sync.lookback_days`` as a positive integer, or ``None`` if absent.

    Raises ``ValueError`` for non-integer or non-positive values.
    """
    raw_gl = raw_sync.get("lookback_days")
    if raw_gl is None:
        return None
    try:
        value = int(raw_gl)
    except (ValueError, TypeError) as err:
        raise ValueError(
            f"sync.lookback_days must be an integer, got: {raw_gl!r}"
        ) from err
    if value <= 0:
        raise ValueError(f"sync.lookback_days must be a positive integer, got: {value}")
    return value


def _parse_global_category(raw_sync: dict[str, Any]) -> str | None:
    """Return ``sync.category_strategy`` normalized to lowercase, or ``None`` if absent.

    Raises ``ValueError`` when the value is blank or not in
    :data:`_VALID_CATEGORY_STRATEGIES`.
    """
    raw_cat = raw_sync.get("category_strategy")
    if raw_cat is None:
        return None
    cat = str(raw_cat).strip().lower()
    if not cat:
        raise ValueError(
            "sync.category_strategy is present but blank — "
            f"use one of {sorted(_VALID_CATEGORY_STRATEGIES)} or remove the field"
        )
    if cat not in _VALID_CATEGORY_STRATEGIES:
        raise ValueError(
            f"sync.category_strategy must be one of "
            f"{sorted(_VALID_CATEGORY_STRATEGIES)}, got: {str(raw_cat).strip()!r}"
        )
    return cat


_CRON_FIELD_RE = (
    r"(?:\*|[0-9]+(?:-[0-9]+)?)(?:/[0-9]+)?(?:,(?:\*|[0-9]+(?:-[0-9]+)?)(?:/[0-9]+)?)*"
)
_CRON_SCHEDULE_RE = re.compile(r"^" + r" ".join([_CRON_FIELD_RE] * 5) + r"$")

# (min, max) inclusive for each cron field position
_CRON_FIELD_RANGES: tuple[tuple[int, int], ...] = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),  # day of week (0 and 7 both mean Sunday)
)


def _validate_cron_term(
    field_name: str,
    value: str,
    position: int,
    term: str,
    low: int,
    high: int,
) -> None:
    """Validate a single cron term (e.g. ``*/5``, ``8-18``, ``3``) for one field.

    *position* is 1-based and used only in error messages.
    Raises ``ValueError`` for an invalid step (<1) or an out-of-range base value.
    """
    parts = term.split("/")
    base = parts[0]
    if len(parts) == 2:
        step = int(parts[1])
        if step < 1:
            raise ValueError(
                f"{field_name} has an invalid step value in field {position} "
                f"(step must be >= 1, got {step!r}) — got: {value!r}"
            )
    if base == "*":
        return
    # base is either "N" or "N-M"
    for part in base.split("-"):
        num = int(part)
        if not (low <= num <= high):
            raise ValueError(
                f"{field_name} has an out-of-range value in field {position} "
                f"({num!r} is outside {low}–{high}) — got: {value!r}"
            )


def _validate_cron_field_ranges(field_name: str, value: str) -> None:
    """Raise ``ValueError`` if any base numeric value in a cron expression is out of range.

    Base values are checked against their field-specific range.  Step values
    (``/S``) are not range-checked, but must be a positive integer (>= 1) —
    a step of 0 is invalid and would be silently ignored by the cron daemon.

    Must be called after syntax validation (``_CRON_SCHEDULE_RE``).
    """
    fields = value.split()
    for position, (cron_field, (low, high)) in enumerate(
        zip(fields, _CRON_FIELD_RANGES, strict=True)
    ):
        # Each field is a comma-separated list of terms; each term is [*|N|N-M][/S].
        for term in cron_field.split(","):
            _validate_cron_term(field_name, value, position + 1, term, low, high)


def _validate_cron_schedule(field_name: str, value: str) -> None:
    """Raise ``ValueError`` if *value* is not a valid five-field cron expression.

    Only the numeric cron syntax is accepted: each of the five fields may
    contain digits, ``*``, ``-``, ``/``, and ``,``.  Letters, spaces within a
    field, shell metacharacters, and extra fields are all rejected.

    This prevents cron-line injection: a schedule is written verbatim as the
    first five columns of a ``/etc/cron.d`` entry followed by a fixed
    ``root`` user column.  An attacker-controlled extra word (e.g.
    ``* * * * * root touch /tmp/pwned #``) would shift the ``root`` column
    and inject an arbitrary command.

    After syntax validation, numeric values are checked against the allowed
    range for each field so that out-of-range expressions (e.g. ``0 25 * * *``)
    are rejected before they silently fail inside the cron daemon.
    """
    if not _CRON_SCHEDULE_RE.match(value):
        raise ValueError(
            f"{field_name} must be a valid five-field cron expression "
            f"(e.g. '0 8 * * *') — got: {value!r}"
        )
    _validate_cron_field_ranges(field_name, value)


def _parse_sync_section(
    raw_sync: dict[str, Any],
) -> tuple[list[Any], str | None, int | None, str | None, str | None]:
    """Parse the ``sync:`` mapping and return its five components.

    Returns a tuple of:
    ``(raw_instances_list, global_wallet_key, global_lookback, global_cat, sync_schedule)``
    """
    raw_instances_list = raw_sync.get("instances") or []
    if not isinstance(raw_instances_list, list):
        raise ValueError(
            f"sync.instances must be a list, got: {type(raw_instances_list).__name__!r}"
        )
    global_wallet_key = _parse_global_wallet_key(raw_sync)
    global_lookback = _parse_global_lookback(raw_sync)
    global_cat = _parse_global_category(raw_sync)
    raw_sched = raw_sync.get("schedule")
    sync_schedule: str | None = None
    if raw_sched is not None:
        sync_schedule = str(raw_sched).strip() or None
        if sync_schedule is None:
            raise ValueError(
                "sync.schedule is present but blank — "
                "provide a valid cron expression or remove the field"
            )
        _validate_cron_schedule("sync.schedule", sync_schedule)
    return (
        raw_instances_list,
        global_wallet_key,
        global_lookback,
        global_cat,
        sync_schedule,
    )


@dataclass(frozen=True)
class SyncConfig:
    """Global sync defaults and the resolved list of sync instances.

    All fields mirror the corresponding ``sync:`` keys in ``instances.yml``.
    Per-instance values take precedence over these globals when an instance
    overrides them; these are the *raw* global values before per-instance
    resolution.
    """

    instances: list[InstanceConfig]
    wallet_api_key: str | None = None
    lookback_days: int | None = None
    category_strategy: str | None = None
    schedule: str | None = None


@dataclass(frozen=True)
class InstancesConfig:
    """Configuration for all sync instances, loaded from ``/app/config/instances.yml``.

    Each instance gets its own session directory at the data root:
    ``{root_data_dir}/tr_session_{instance.name}/``.

    Required YAML layout::

        sync:
          schedule: "…"          # global default, overridable per instance (optional)
          wallet_api_key: "…"    # global default, overridable per instance
          lookback_days: 7       # optional
          category_strategy: history  # optional
          instances:
            - name: …

    Optional top-level keys::

        backup_schedule: "…"     # omit to disable scheduled backups
    """

    sync: SyncConfig
    data_dir: Path = field(default_factory=lambda: Path(_DEFAULT_DATA_DIR))
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    allow_insecure_ssl: bool = False
    backup_schedule: str | None = None

    @property
    def instances(self) -> list[InstanceConfig]:
        """Backward-compatible alias for :attr:`sync.instances`."""
        return self.sync.instances

    @property
    def sync_schedule(self) -> str | None:
        """Backward-compatible alias for :attr:`sync.schedule`."""
        return self.sync.schedule

    @classmethod
    def load(cls, path: Path) -> InstancesConfig:
        """Load and validate an instances YAML config file."""
        import yaml  # deferred — only needed when instances.yml is loaded

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
        if backup_schedule is not None:
            _validate_cron_schedule("backup_schedule", backup_schedule)

        return cls(
            sync=SyncConfig(
                instances=instances,
                wallet_api_key=global_wallet_key,
                lookback_days=global_lookback,
                category_strategy=global_cat,
                schedule=sync_schedule,
            ),
            data_dir=Path(data.get("data_dir", _DEFAULT_DATA_DIR)),
            telegram_bot_token=data.get("telegram_bot_token") or None,
            telegram_chat_id=data.get("telegram_chat_id") or None,
            allow_insecure_ssl=allow_insecure_ssl,
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
            data_dir=self.data_dir / f"tr_session_{inst.name}",
            instance=inst.name,
            allow_insecure_ssl=self.allow_insecure_ssl,
            label_ids=dict(inst.label_ids),
            category_strategy=inst.category_strategy,
        )
