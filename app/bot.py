"""Telegram bot for remote command execution across multi-tenant instances.

Usage (via CLI):
    python -m app bot

All configuration is read from ``/app/config/instances.yml``.
Required fields: ``telegram_bot_token``, ``telegram_chat_id``, at least one
instance under ``sync.instances``.

Commands (registered via setMyCommands for Telegram autocomplete):
    /sync                              Force a Trade Republic sync — choose instance via inline buttons.
    /backup [monthly|yearly] [period]  Force a Wallet backup — guided by inline buttons if no args given.
    /status                            Show configured instances and backup availability.

Interaction flow for /sync:
    1. User sends /sync.
    2. Bot replies with inline keyboard buttons, one per configured instance.
    3. User taps an instance button.
    4. Bot sends an ACK ("▶️ Executing sync for David...") and calls main.run() in a background thread.
    5. The Notifier inside run() sends the final Telegram result notification when done.

Interaction flow for /backup (no args):
    1. User sends /backup.
    2. Bot asks "Monthly or Yearly?" via inline keyboard.
    3. User taps a type button.
    4. Bot shows the period selection keyboard (months or years).
    5. User taps a period button → bot executes backup directly in a background thread.
"""

from __future__ import annotations

import collections
import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

from app import backup as backup_module, http_client
from app.bot_keyboards import (
    _CB_SEP,
    BACKUP_ICONS as _BACKUP_ICONS,
    backup_type_buttons as _backup_type_buttons_fn,
    instance_buttons as _instance_buttons_fn,
    instance_buttons_for_resync as _instance_buttons_for_resync_fn,
    month_buttons as _month_buttons_fn,
    resync_date_buttons as _resync_date_buttons_fn,
    year_buttons as _year_buttons_fn,
)
from app.config import (
    INSTANCES_CONFIG_PATH,
    BackupConfig,
    Config,
    InstancesConfig,
    has_valid_session,
)
from app.http_client import build_session as _build_session
from app.main import (
    run as _main_run,
    run_resync as _main_run_resync,
)
from app.notifier import Notifier, escape_code as _esc_code, escape_markdown as _esc
from app.wallet_client import WalletClient

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"
_MAX_LOG_CHARS = 3800  # safe limit below Telegram's 4096-char message cap

__all__ = [
    "_BACKUP_ICONS",
    "_CB_SEP",
    "_MAX_LOG_CHARS",
    "BotConfig",
    "InstanceConfig",
    "TelegramBot",
    "_auth_icon",
    "_check_session_direct",
    "_format_sync_timestamp",
    "_last_sync_summary_direct",
    "run",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstanceConfig:
    name: str  # human-readable, used in commands (e.g. "david")
    config: Config  # full sync config for direct method calls


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    chat_id: str
    instances: dict[str, InstanceConfig] = field(default_factory=dict)
    backup_cfg: BackupConfig | None = None  # None means backup commands are disabled
    log_dir: Path = field(default_factory=lambda: Path("/app/data"))
    allow_insecure_ssl: bool = False

    @classmethod
    def from_env(cls, instances_yaml: InstancesConfig | None = None) -> BotConfig:
        if instances_yaml is None:
            instances_yaml = InstancesConfig.load(INSTANCES_CONFIG_PATH)

        bot_token = str(instances_yaml.telegram_bot_token or "").strip() or None
        chat_id = str(instances_yaml.telegram_chat_id or "").strip() or None
        if not bot_token:
            raise ValueError(
                "Missing required credential TELEGRAM_BOT_TOKEN "
                "(set telegram_bot_token in the instances config file)"
            )
        if not chat_id:
            raise ValueError(
                "Missing required credential TELEGRAM_CHAT_ID "
                "(set telegram_chat_id in the instances config file)"
            )

        instances: dict[str, InstanceConfig] = {}
        for inst in instances_yaml.instances:
            full_cfg = instances_yaml.to_config(inst.name)
            instances[inst.name.lower()] = InstanceConfig(
                name=inst.name,
                config=full_cfg,
            )

        backup_cfg = BackupConfig.from_instances_yaml(instances_yaml)

        return cls(
            bot_token=bot_token,
            chat_id=chat_id,
            instances=instances,
            backup_cfg=backup_cfg,
            log_dir=instances_yaml.data_dir,
            allow_insecure_ssl=instances_yaml.allow_insecure_ssl,
        )


# ---------------------------------------------------------------------------
# Markdown helpers (MarkdownV2)
# ---------------------------------------------------------------------------
# _esc is imported from app.notifier.escape_markdown — for bold/plain text contexts.
# _esc_code is imported from app.notifier.escape_code — for inline-code (backtick) spans.


def _auth_icon(auth: bool | None) -> str:
    """Return a status icon for a Trade Republic session auth check result."""
    if auth is True:
        return "✅"
    if auth is False:
        return "⚠️"
    return "❓"


def _format_sync_timestamp(raw: str) -> str:
    """Format log/DB timestamps as ``YYYY/MM/DD HH:MM UTC`` for Telegram output."""
    try:
        if "T" in raw:
            parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        else:
            parsed = datetime.datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=datetime.UTC
            )
        return parsed.astimezone(datetime.UTC).strftime("%Y/%m/%d %H:%M UTC")
    except ValueError:
        return raw


@dataclass
class InstanceStatus:
    """Combined auth and last-sync status for a single instance."""

    auth: bool | None
    last_sync: str | None


def _instance_status_direct(
    data_dir: Path, shared_db_path: Path, instance: str
) -> InstanceStatus:
    """Read auth state and last sync run from a single DB connection.

    Opens ``EventRepository`` at most once, fetching both ``auth_state`` and
    ``sync_runs`` in a single connection.  This is the preferred call site for
    :meth:`TelegramBot._instance_status_line`; it halves the number of SQLite
    opens compared with calling ``_check_session_direct`` and
    ``_last_sync_summary_direct`` separately.

    The last sync info is always read from the DB regardless of the current
    session/cookie state, so ``/status`` keeps showing the last sync summary
    even when the user is logged out.

    Returns:
        An :class:`InstanceStatus` with ``auth`` set to ``True``/``False``/``None``
        and ``last_sync`` set to a human-readable summary string or ``None``.
    """
    from app.persistence import EventRepository

    session_ok = has_valid_session(data_dir)

    if not shared_db_path.exists():
        return InstanceStatus(
            auth=bool(session_ok) if session_ok else False, last_sync=None
        )

    try:
        with EventRepository(shared_db_path, instance=instance) as repo:
            auth_status = repo.get_auth_state(instance)
            run_info = repo.get_sync_run(instance)
    except Exception:
        return InstanceStatus(auth=False if not session_ok else None, last_sync=None)

    if not session_ok:
        auth: bool | None = False
    elif auth_status in ("failed", "expired"):
        auth = False
    else:
        auth = True

    last_sync = _build_sync_summary(run_info)
    return InstanceStatus(auth=auth, last_sync=last_sync)


def _build_sync_summary(run_info: dict | None) -> str | None:
    """Convert a raw sync-run dict to a human-readable summary string."""
    if run_info is None:
        return None

    status = run_info.get("status")
    ran_at = run_info.get("ran_at")

    if status not in {"success", "partial", "failed"}:
        return None

    icon: dict[str, str] = {"success": "✅", "partial": "⚠️", "failed": "❌"}
    parts = [f"{icon[status]} {status}"]
    if ran_at:
        parts[0] = f"{parts[0]} at {_format_sync_timestamp(ran_at)}"
    saved = run_info.get("saved")
    failed = run_info.get("failed")
    excluded = run_info.get("excluded")
    if saved is not None:
        parts.append(f"saved {saved}")
    if failed is not None:
        parts.append(f"failed {failed}")
    if excluded is not None:
        parts.append(f"excluded {excluded}")
    return " · ".join(parts)


def _check_session_direct(
    data_dir: Path, shared_db_path: Path, instance: str
) -> bool | None:
    """Return True/False/None for the session state of *instance*.

    Reads cookie file expiry and ``auth_state`` from the shared ``sync.db``
    directly without any network calls.

    Returns:
        True  — session valid and ``auth_state`` is ``ok`` (or no state yet).
        False — session missing/expired or ``auth_state`` is ``failed``/``expired``.
        None  — DB could not be read (corrupted/locked).
    """
    from app.persistence import EventRepository

    if not has_valid_session(data_dir):
        return False

    if shared_db_path.exists():
        try:
            with EventRepository(shared_db_path, instance=instance) as repo:
                auth_status = repo.get_auth_state(instance)
        except Exception:
            return None
        if auth_status in ("failed", "expired"):
            return False
    return True


def _last_sync_summary_direct(shared_db_path: Path, instance: str) -> str | None:
    """Return a human-readable summary of the most recent sync run from the DB."""
    from app.persistence import EventRepository

    if not shared_db_path.exists():
        return None

    try:
        with EventRepository(shared_db_path, instance=instance) as repo:
            run_info = repo.get_sync_run(instance)
    except Exception:
        return None

    return _build_sync_summary(run_info)


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class TelegramBot:
    """Long-polling Telegram bot that dispatches commands to sync instances in-process."""

    def __init__(self, cfg: BotConfig) -> None:
        self._cfg = cfg
        self._api = _TELEGRAM_API.format(token=cfg.bot_token)
        self._offset = 0
        # Configure SSL once at startup — all in-process sync/resync/backup
        # calls share this policy without racing on a per-run configure() call.
        http_client.configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
        # Session for all Telegram API calls — routes through the SSL circuit-breaker
        # so allow_insecure_ssl applies to bot traffic too.
        self._session = _build_session()
        # Initialise the shared database schema on startup.
        if cfg.instances:
            from app.persistence import init_db

            first_inst = next(iter(cfg.instances.values()))
            init_db(first_inst.config.shared_db_path)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        log.info(
            "Telegram bot started — polling for updates (authorized chat: %s)",
            self._cfg.chat_id,
        )
        self._register_commands()
        self._send_message("🤖 Bot started and ready\\.")
        while True:
            try:
                self._poll_once()
            except KeyboardInterrupt:
                log.info("Bot stopped by keyboard interrupt")
                break
            except Exception as exc:
                log.warning("Polling error: %s", exc)
                time.sleep(5)

    # ------------------------------------------------------------------
    # Command registration (Telegram autocomplete menu)
    # ------------------------------------------------------------------

    def _register_commands(self) -> None:
        commands = [
            {
                "command": "sync",
                "description": "Force Trade Republic sync (choose instance)",
            },
            {
                "command": "status",
                "description": "Show instances and backup service availability",
            },
            {"command": "logs", "description": "Show today's shared sync log"},
            {
                "command": "resync",
                "description": "Force re-sync of a specific day, bypassing dedup",
            },
        ]
        if self._cfg.backup_cfg:
            commands[3:3] = [
                {
                    "command": "backup",
                    "description": "Force a Wallet backup (monthly or yearly)",
                },
            ]
        try:
            resp = self._session.post(
                f"{self._api}/setMyCommands",
                json={"commands": commands},
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Telegram commands registered successfully")
        except requests.RequestException as exc:
            log.warning("Failed to register Telegram commands: %s", exc)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_once(self) -> None:
        resp = self._session.get(
            f"{self._api}/getUpdates",
            params={
                "offset": self._offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=40,
        )
        resp.raise_for_status()
        for update in resp.json().get("result", []):
            self._offset = update["update_id"] + 1
            try:
                self._handle_update(update)
            except Exception as exc:
                log.warning(
                    "Error handling update %s: %s", update.get("update_id"), exc
                )

    # ------------------------------------------------------------------
    # Update routing
    # ------------------------------------------------------------------

    def _handle_update(self, update: dict) -> None:
        if "message" in update:
            self._handle_message(update["message"])
        elif "callback_query" in update:
            self._handle_callback_query(update["callback_query"])

    def _handle_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        if chat_id != self._cfg.chat_id:
            log.debug("Ignoring message from unauthorized chat %s", chat_id)
            return

        text = message.get("text", "").strip()

        # Intercept a plain digit-only reply as a 2FA code when a login is pending.
        if text.isdigit() and not text.startswith("/"):
            submitted = self._maybe_submit_pending_code(text)
            if submitted:
                self._delete_message(message.get("message_id"))
            return

        if not text.startswith("/"):
            self._send_message("⚠️ I only accept commands\\.")
            return

        parts = text.split()
        raw_cmd = parts[0].lstrip("/").split("@")[0].lower()
        args = parts[1:]

        dispatch = {
            "status": self._cmd_status,
            "sync": self._cmd_sync,
            "resync": self._cmd_resync,
            "logs": self._cmd_logs,
            "code": self._cmd_code,
            "backup": self._cmd_backup,
        }
        handler = dispatch.get(raw_cmd)
        if handler is None:
            self._send_message(f"❓ Unknown command: `{_esc_code(raw_cmd)}`\\.")
            return
        handler(args)

        # The /code message contains a sensitive 2FA code — remove it from the
        # chat history as soon as it has been dispatched.
        if raw_cmd == "code":
            self._delete_message(message.get("message_id"))

    def _handle_callback_query(self, cq: dict) -> None:
        """Handle inline keyboard button taps."""
        cq_id = cq.get("id", "")
        chat_id = str(cq.get("message", {}).get("chat", {}).get("id", ""))

        # Always acknowledge the callback to remove the loading spinner on the button.
        self._answer_callback_query(cq_id)

        if chat_id != self._cfg.chat_id:
            return

        data = cq.get("data", "")

        # Buttons for unavailable instances use "noop" — acknowledge and ignore.
        if data == "noop":
            return

        parts = data.split(_CB_SEP)
        if len(parts) < 2:
            log.warning("Malformed callback_data: %r", data)
            return

        self._dispatch_callback(parts, data)

    def _dispatch_callback(self, parts: list[str], data: str) -> None:
        """Route a parsed callback to the appropriate sub-handler."""
        cmd = parts[0]
        named: dict[str, Any] = {
            "backup_type": self._on_cb_backup_type,
            "backup_yearly": self._on_cb_backup_yearly,
            "backup_monthly": self._on_cb_backup_monthly,
            "resync_pick_date": self._on_cb_resync_pick_date,
            "resync": self._on_cb_resync,
        }
        handler = named.get(cmd)
        if handler is not None:
            handler(parts, data)
        else:
            self._on_cb_instance_cmd(cmd, parts)

    # Callback sub-handlers ---------------------------------------------------

    def _on_cb_backup_type(self, parts: list[str], _data: str) -> None:
        subtype = parts[1]
        if subtype == "monthly":
            self._send_message(
                "📅 *Monthly backup* — Choose month:",
                keyboard=self._month_buttons(),
            )
        elif subtype == "yearly":
            self._send_message(
                "📆 *Yearly backup* — Choose year:", keyboard=self._year_buttons()
            )

    def _on_cb_backup_yearly(self, parts: list[str], _data: str) -> None:
        self._launch_backup("yearly", parts[1])

    def _on_cb_backup_monthly(self, parts: list[str], _data: str) -> None:
        self._launch_backup("monthly", parts[1])

    def _on_cb_resync_pick_date(self, parts: list[str], _data: str) -> None:
        instance_key = parts[1].lower()
        inst = self._cfg.instances.get(instance_key)
        if inst is None:
            self._send_message(f"❓ Unknown instance: `{_esc_code(instance_key)}`")
            return
        self._send_message(
            f"🔁 *Resync* — Choose date for *{_esc(inst.name)}*:",
            keyboard=self._resync_date_buttons(instance_key),
        )

    def _on_cb_resync(self, parts: list[str], data: str) -> None:
        # Format: resync:<date>:<instance>
        if len(parts) < 3:
            log.warning("Malformed resync callback_data: %r", data)
            return
        date_str = parts[1]
        instance_key = parts[2].lower()
        inst = self._cfg.instances.get(instance_key)
        if inst is None:
            self._send_message(f"❓ Unknown instance: `{_esc_code(instance_key)}`")
            return
        self._launch_resync(inst, date_str)

    def _on_cb_instance_cmd(self, cmd: str, parts: list[str]) -> None:
        """Handle instance-routed callbacks: sync."""
        instance_key = parts[-1].lower()
        inst = self._cfg.instances.get(instance_key)
        if inst is None:
            self._send_message(f"❓ Unknown instance: `{_esc_code(instance_key)}`")
            return

        if cmd == "sync":
            self._launch_sync(inst)
        else:
            log.warning("Unknown callback cmd: %r", cmd)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_status(self, _args: list[str]) -> None:
        if not self._cfg.instances:
            self._send_message("⚠️ No instances configured\\.")
            return

        lines = ["📋 *Instance status*\n"]
        for inst in self._cfg.instances.values():
            lines.append(self._instance_status_line(inst))

        if self._cfg.backup_cfg:
            lines.append("\n💾 *Backup*: configured")
        else:
            lines.append("\n💾 *Backup*: not configured")

        self._send_message("\n".join(lines))

    def _instance_status_line(self, inst: InstanceConfig) -> str:
        """Build a Telegram-formatted status line for a single sync instance."""
        status = _instance_status_direct(
            inst.config.data_dir,
            inst.config.shared_db_path,
            inst.config.instance,
        )
        auth_icon = _auth_icon(status.auth)
        return (
            f"• *{_esc(inst.name)}*"
            f"\n  auth: {auth_icon}"
            f"\n  last: {_esc(status.last_sync or 'unavailable')}"
        )

    def _cmd_sync(self, _args: list[str]) -> None:
        self._pick_instance("sync", "🔄 *Sync* — Choose instance:")

    def _cmd_resync(self, args: list[str]) -> None:
        """Handle /resync [YYYY-MM-DD]."""
        if not args:
            self._pick_instance("resync_pick_date", "🔁 *Resync* — Choose instance:")
            return

        date_str = args[0]
        try:
            datetime.date.fromisoformat(date_str)
        except ValueError:
            self._send_message(
                f"⚠️ Invalid date: `{_esc_code(date_str)}`\\. "
                "Expected format: `YYYY\\-MM\\-DD`\\."
            )
            return

        self._send_message(
            f"🔁 *Resync {_esc(date_str)}* — Choose instance:",
            keyboard=self._instance_buttons_for_resync(date_str),
        )

    def _cmd_logs(self, _args: list[str]) -> None:
        threading.Thread(
            target=self._fetch_and_send_logs,
            daemon=True,
        ).start()

    def _cmd_code(self, args: list[str]) -> None:
        """Deliver an authenticator code to a waiting login process: /code <instance> <code>."""
        if len(args) != 2:
            self._send_message(
                "Usage: `/code <instance> <code>` — e\\.g\\. `/code david 123456`"
            )
            return

        instance_key, code = args[0].lower(), args[1]
        inst = self._cfg.instances.get(instance_key)
        if inst is None:
            self._send_message(f"❓ Unknown instance: `{_esc_code(args[0])}`")
            return
        if not code.isdigit():
            self._send_message("⚠️ The code must contain digits only\\.")
            return

        self._send_message(f"🔑 Sending code to *{_esc(inst.name)}*\\.\\.\\.")
        self._submit_code_to(inst, code)

    def _cmd_backup(self, args: list[str]) -> None:
        if not self._cfg.backup_cfg:
            self._send_message("🚫 Backup service is not configured\\.")
            return
        if not args:
            self._send_message(
                "📦 *Backup* — Choose type:", keyboard=self._backup_type_buttons()
            )
            return

        type_arg = args[0].lower()
        period_arg = args[1] if len(args) > 1 else None

        if type_arg == "monthly":
            if period_arg:
                self._launch_backup("monthly", period_arg)
            else:
                self._send_message(
                    "📅 *Monthly backup* — Choose month:",
                    keyboard=self._month_buttons(),
                )
        elif type_arg == "yearly":
            if period_arg:
                self._launch_backup("yearly", period_arg)
            else:
                self._send_message(
                    "📆 *Yearly backup* — Choose year:", keyboard=self._year_buttons()
                )
        else:
            self._send_message(
                f"⚠️ Unknown backup type: `{_esc_code(type_arg)}`\\. Use `monthly` or `yearly`\\."
            )

    def _launch_backup(self, mode: str, period: str) -> None:
        if not self._cfg.backup_cfg:
            self._send_message("🚫 Backup service is not configured\\.")
            return
        icon = _BACKUP_ICONS.get(mode, "📦")
        label = _esc(f"{mode.capitalize()} backup ({period})")
        self._send_message(f"{icon} *{label}*\\.\\.\\.")
        threading.Thread(
            target=self._run_backup,
            args=(mode, period),
            daemon=True,
        ).start()

    def _run_backup(self, mode: str, period: str) -> None:
        """Run a backup in-process. Called from a daemon thread."""
        cfg = self._cfg.backup_cfg
        if cfg is None:
            return
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        client = WalletClient(api_key=cfg.wallet_api_key)
        notifier = Notifier(
            cfg.telegram_bot_token, cfg.telegram_chat_id, cfg.owner_name
        )
        try:
            if mode == "auto":
                backup_module.run_auto(client, notifier, cfg.data_dir)
            elif mode == "monthly":
                year, month = backup_module._parse_monthly_param(period)
                backup_module.run_monthly(client, notifier, cfg.data_dir, year, month)
            elif mode == "yearly":
                year = backup_module._parse_yearly_param(period)
                backup_module.run_yearly(client, notifier, cfg.data_dir, year)
            else:
                log.warning("Unknown backup mode %r — ignoring", mode)
                self._send_message(
                    f"⚠️ Unknown backup mode: `{_esc_code(mode)}`\\. Expected `monthly` or `yearly`\\."
                )
        except Exception as exc:
            log.exception("Backup failed (mode=%s period=%s): %s", mode, period, exc)
            self._send_message(
                f"❌ Backup \\({_esc(mode)} {_esc(period)}\\) failed: {_esc(str(exc))}"
            )

    # ------------------------------------------------------------------
    # Execution — direct in-process calls
    # ------------------------------------------------------------------

    def _launch_sync(self, inst: InstanceConfig) -> None:
        self._send_message(f"▶️ Executing *sync* for *{_esc(inst.name)}*\\.\\.\\.")
        threading.Thread(
            target=self._run_sync_for_instance,
            args=(inst,),
            daemon=True,
        ).start()

    def _run_sync_for_instance(self, inst: InstanceConfig) -> None:
        """Run sync for *inst* in-process. Called from a daemon thread."""
        try:
            _main_run(cfg=inst.config)
        except Exception as exc:
            log.exception("Sync failed for instance %s", inst.name)
            self._send_message(
                f"❌ Sync error for *{_esc(inst.name)}*: {_esc(str(exc))}"
            )

    def _launch_resync(self, inst: InstanceConfig, date_str: str) -> None:
        self._send_message(
            f"🔁 Executing *resync {_esc(date_str)}* for *{_esc(inst.name)}*\\.\\.\\."
        )
        threading.Thread(
            target=self._run_resync_for_instance,
            args=(inst, date_str),
            daemon=True,
        ).start()

    def _run_resync_for_instance(self, inst: InstanceConfig, date_str: str) -> None:
        """Run resync for *inst* in-process. Called from a daemon thread."""
        try:
            _main_run_resync(date_str, cfg=inst.config)
        except Exception as exc:
            log.exception("Resync failed for instance %s date %s", inst.name, date_str)
            self._send_message(
                f"❌ Resync error for *{_esc(inst.name)}* `{_esc_code(date_str)}`: {_esc(str(exc))}"
            )

    def _maybe_submit_pending_code(self, code: str) -> bool:
        """Submit *code* to the instance awaiting 2FA during a sync.

        For single-instance setups, submits directly.  For multi-instance
        setups, probes each instance's ``twofa_pending_file`` at the data root
        to discover which one is awaiting a code.
        """
        return self._submit_code_no_pending(code)

    def _submit_code_to(self, inst: InstanceConfig, code: str) -> bool:
        """Write *code* to *inst*'s 2FA code file directly. Returns True on success."""
        pending_file = inst.config.twofa_pending_file
        if not pending_file.exists():
            self._send_message(f"⚠️ No active login request for *{_esc(inst.name)}*\\.")
            return False
        try:
            inst.config.twofa_code_file.write_text(code.strip())
            return True
        except Exception as exc:
            self._send_message(
                f"❌ Could not submit code for *{_esc(inst.name)}*: {_esc(str(exc))}"
            )
            return False

    def _submit_code_no_pending(self, code: str) -> bool:
        """Submit *code* to the instance currently awaiting a 2FA code.

        For a single-instance setup submits directly.  For multi-instance setups,
        probes each instance's ``twofa_pending_file`` at the data root to
        discover which one is awaiting a code, falling back to a generic
        disambiguation prompt when no instance is waiting.
        """
        instances = self._cfg.instances
        if len(instances) == 1:
            return self._submit_code_to(next(iter(instances.values())), code)
        file_pending = self._probe_pending(instances)
        if len(file_pending) == 1:
            return self._submit_code_to(next(iter(file_pending.values())), code)
        if len(file_pending) > 1:
            names = ", ".join(f"*{_esc(k)}*" for k in sorted(file_pending))
            self._send_message(
                f"⚠️ Multiple logins pending: {names}\\. "
                "Use `/code <instance> <code>` to specify which one\\."
            )
            return False
        names = ", ".join(f"*{_esc(k)}*" for k in sorted(instances))
        self._send_message(
            f"⚠️ Multiple instances configured: {names}\\. "
            "Use `/code <instance> <code>` to specify which one\\."
        )
        return False

    def _probe_pending(
        self, instances: dict[str, InstanceConfig]
    ) -> dict[str, InstanceConfig]:
        """Check each instance's 2FA pending marker file.

        Returns those instances whose ``twofa_pending_file`` marker is present.
        Short-circuits after finding two (result is already ambiguous).
        """
        pending: dict[str, InstanceConfig] = {}
        for name, inst in instances.items():
            if inst.config.twofa_pending_file.exists():
                pending[name] = inst
                if len(pending) > 1:
                    break
        return pending

    def _fetch_and_send_logs(self) -> None:
        """Fetch today's logs from the shared log file and send them to Telegram."""
        today_str = datetime.datetime.now(tz=datetime.UTC).strftime("%Y-%m-%d")
        log_file = self._cfg.log_dir / "sync.log"
        # MarkdownV2 header — used only when there are no logs (plain-text message not needed).
        header_md = f"📋 Logs \\({_esc(today_str)} UTC\\)\n\n"
        # Plain-text header — used when the log body is sent with parse_mode=None so that
        # MarkdownV2 escape characters are not displayed literally in Telegram.
        header_plain = f"📋 Logs ({today_str} UTC)\n\n"
        try:
            if not log_file.exists():
                text = ""
            else:
                lines: collections.deque[str] = collections.deque()
                total_chars = 0
                truncated = False
                with log_file.open(encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if not line.startswith(today_str):
                            continue
                        stripped = line.rstrip()
                        lines.append(stripped)
                        total_chars += len(stripped) + 1  # +1 for the joining newline
                        while total_chars > _MAX_LOG_CHARS and len(lines) > 1:
                            removed = lines.popleft()
                            total_chars -= len(removed) + 1
                            truncated = True
                text = "\n".join(lines)
                if truncated:
                    text = "[... truncated ...]\n" + text
        except Exception as exc:
            self._send_message(f"❌ Could not read logs: {_esc(str(exc))}")
            return

        if not text.strip():
            self._send_message(header_md + "_No logs today\\._")
            return

        self._send_message(header_plain + text, parse_mode=None)

    # ------------------------------------------------------------------
    # Keyboard builder wrappers — delegate to app.bot_keyboards
    # ------------------------------------------------------------------

    def _pick_instance(self, cmd: str, prompt: str) -> None:
        """Send *prompt* with an instance-picker inline keyboard for *cmd*."""
        self._send_message(prompt, keyboard=self._instance_buttons(cmd))

    def _instance_buttons(self, cmd: str) -> list[list[dict]]:
        names = [inst.name for inst in self._cfg.instances.values()]
        return _instance_buttons_fn(cmd, names)

    def _instance_buttons_for_resync(self, date_str: str) -> list[list[dict]]:
        names = [inst.name for inst in self._cfg.instances.values()]
        return _instance_buttons_for_resync_fn(date_str, names)

    def _resync_date_buttons(self, instance_key: str) -> list[list[dict]]:
        return _resync_date_buttons_fn(instance_key)

    def _backup_type_buttons(self) -> list[list[dict]]:
        return _backup_type_buttons_fn()

    def _year_buttons(self) -> list[list[dict]]:
        return _year_buttons_fn()

    def _month_buttons(self) -> list[list[dict]]:
        return _month_buttons_fn()

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    def _send_message(
        self,
        text: str,
        keyboard: list[list[dict]] | None = None,
        parse_mode: str | None = "MarkdownV2",
    ) -> None:
        payload: dict = {
            "chat_id": self._cfg.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            resp = self._session.post(
                f"{self._api}/sendMessage",
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Failed to send Telegram message: %s", exc)

    def _answer_callback_query(self, callback_query_id: str) -> None:
        """Acknowledge the callback query to remove the loading spinner on the button."""
        try:
            self._session.post(
                f"{self._api}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id},
                timeout=10,
            )
        except requests.RequestException as exc:
            log.warning("Failed to answer callback query: %s", exc)

    def _delete_message(self, message_id: int | None) -> None:
        """Delete a message from the chat (used to purge sensitive 2FA codes)."""
        if message_id is None:
            return
        try:
            self._session.post(
                f"{self._api}/deleteMessage",
                json={"chat_id": self._cfg.chat_id, "message_id": message_id},
                timeout=10,
            )
        except requests.RequestException as exc:
            log.warning("Failed to delete message %s: %s", message_id, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(instances_yaml: InstancesConfig | None = None) -> None:
    """Load config from environment and start the bot.

    *instances_yaml* — optional pre-loaded :class:`~app.config.InstancesConfig`.
    When provided, ``BotConfig.from_env`` skips loading it a second time, avoiding
    duplicate I/O when the caller (e.g. the ``bot`` CLI command) has already loaded
    it to derive the log directory.
    """
    cfg = BotConfig.from_env(instances_yaml=instances_yaml)
    bot = TelegramBot(cfg)
    bot.run()
