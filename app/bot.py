"""Telegram bot for remote command execution across multi-tenant instances.

Usage (via CLI):
    python -m app bot

Environment variables:
    TELEGRAM_BOT_TOKEN  Required. Bot token from BotFather.
    TELEGRAM_CHAT_ID    Required. Authorized chat ID (only this chat can issue commands).
    INSTANCES           Required. Comma-separated list of sync instance names (e.g. "david,eli").
    CONTAINER_PREFIX    Required. Docker container name prefix (e.g. "trade-republic-budget-bakers-sync").
    BACKUP_SERVICE      Optional. Name of the backup service (default: "backup").
                        Set to empty string to disable backup commands.

Container naming convention:
    Sync instances:  {CONTAINER_PREFIX}-sync-{instance}-1   (e.g. "myproject-sync-david-1")
    Backup service:  {CONTAINER_PREFIX}-{BACKUP_SERVICE}-1  (e.g. "myproject-backup-1")

Commands (registered via setMyCommands for Telegram autocomplete):
    /sync                              Force a Trade Republic sync — choose instance via inline buttons.
    /backup [monthly|yearly] [period]  Force a Wallet backup — guided by inline buttons if no args given.
    /status                            Show configured instances and backup service availability.
    /help                              Show available commands.

Interaction flow for /sync:
    1. User sends /sync.
    2. Bot replies with inline keyboard buttons, one per configured instance.
    3. User taps an instance button.
    4. Bot sends an ACK ("▶️ Executing sync for David...") and launches docker exec in background.
    5. The container's own Notifier sends the final Telegram result notification when done.

Interaction flow for /backup (no args):
    1. User sends /backup.
    2. Bot asks "Monthly or Yearly?" via inline keyboard.
    3. User taps a type button.
    4. Bot shows the period selection keyboard (months or years).
    5. User taps a period button → bot executes backup and the backup container sends the result.

Interaction flow for /backup with args:
    /backup monthly          → skips step 2, shows month keyboard directly.
    /backup monthly YYYY-MM  → executes immediately, no keyboards shown.
    /backup yearly           → skips step 2, shows year keyboard directly.
    /backup yearly YYYY      → executes immediately.
"""

from __future__ import annotations

import datetime
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests
import urllib3

import docker
from app.bot_docker import (
    _docker_check_awaiting_code,
    _docker_check_session,
    _docker_client_ctx,
    _docker_container_status,
    _docker_exec_silent,
    _docker_last_sync_summary,
    _docker_logs_today,
    _format_sync_timestamp,
)
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
from app.config import BotEnv
from app.notifier import _escape_markdown as _esc

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"
_MAX_LOG_CHARS = 3800  # safe limit below Telegram's 4096-char message cap

# Re-export docker helpers so existing imports from app.bot keep working.
__all__ = [
    "_BACKUP_ICONS",
    "_CB_SEP",
    "_MAX_LOG_CHARS",
    "BotConfig",
    "InstanceConfig",
    "TelegramBot",
    "_auth_icon",
    "_docker_check_session",
    "_docker_client_ctx",
    "_docker_container_status",
    "_docker_exec_silent",
    "_docker_last_sync_summary",
    "_docker_logs_today",
    "_format_sync_timestamp",
    "run",
]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstanceConfig:
    name: str  # human-readable, used in commands (e.g. "david")
    container_name: str  # Docker container name (e.g. "myproject-sync-david-1")


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    chat_id: str
    instances: dict[str, InstanceConfig] = field(default_factory=dict)
    backup_container: str | None = None  # None means backup commands are disabled
    telegram_verify_ssl: bool = True

    @classmethod
    def from_env(cls) -> BotConfig:
        env = BotEnv.from_env()

        instances: dict[str, InstanceConfig] = {}
        for name in [n.strip() for n in env.instances_raw.split(",") if n.strip()]:
            container_name = f"{env.container_prefix}-sync-{name.lower()}-1"
            instances[name.lower()] = InstanceConfig(
                name=name,
                container_name=container_name,
            )

        backup_container = (
            f"{env.container_prefix}-{env.backup_service}-1"
            if env.backup_service
            else None
        )

        return cls(
            bot_token=env.bot_token,
            chat_id=env.chat_id,
            instances=instances,
            backup_container=backup_container,
            telegram_verify_ssl=env.telegram_verify_ssl,
        )


# ---------------------------------------------------------------------------
# Markdown helpers (MarkdownV2)
# ---------------------------------------------------------------------------
# _esc is imported from app.notifier._escape_markdown — single source of truth.


def _auth_icon(auth: bool | None) -> str:
    """Return a status icon for a Trade Republic session auth check result."""
    if auth is True:
        return "✅"
    if auth is False:
        return "⚠️"
    return "❓"


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class TelegramBot:
    """Long-polling Telegram bot that dispatches commands to Docker containers."""

    def __init__(self, cfg: BotConfig) -> None:
        self._cfg = cfg
        self._api = _TELEGRAM_API.format(token=cfg.bot_token)
        self._offset = 0
        # Instances that have an active login flow waiting for a 2FA code.
        # Maps instance key (lower-case name) → InstanceConfig.
        self._pending_login: dict[str, InstanceConfig] = {}
        if not cfg.telegram_verify_ssl:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        log.info(
            "Telegram bot started — polling for updates (authorized chat: %s)",
            self._cfg.chat_id,
        )
        self._register_commands()
        self._send_message(
            "🤖 *Bot started and ready\\.* Use /help to see available commands\\."
        )
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
                "command": "resync",
                "description": "Force re-sync of a specific day, bypassing dedup",
            },
            {
                "command": "login",
                "description": "Renew Trade Republic 2FA session (choose instance)",
            },
            {"command": "logs", "description": "Show today's logs for an instance"},
            {
                "command": "status",
                "description": "Show instances and backup service availability",
            },
            {"command": "help", "description": "Show available commands"},
        ]
        if self._cfg.backup_container:
            commands[2:2] = [
                {
                    "command": "backup",
                    "description": "Force a Wallet backup (monthly or yearly)",
                },
            ]
        try:
            resp = requests.post(
                f"{self._api}/setMyCommands",
                json={"commands": commands},
                timeout=10,
                verify=self._cfg.telegram_verify_ssl,
            )
            resp.raise_for_status()
            log.info("Telegram commands registered successfully")
        except requests.RequestException as exc:
            log.warning("Failed to register Telegram commands: %s", exc)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _poll_once(self) -> None:
        resp = requests.get(
            f"{self._api}/getUpdates",
            params={
                "offset": self._offset,
                "timeout": 30,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=40,
            verify=self._cfg.telegram_verify_ssl,
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
            self._send_message(
                "⚠️ I only accept commands\\. Use /help to see what's available\\."
            )
            return

        parts = text.split()
        raw_cmd = parts[0].lstrip("/").split("@")[0].lower()
        args = parts[1:]

        dispatch = {
            "help": self._cmd_help,
            "status": self._cmd_status,
            "sync": self._cmd_sync,
            "resync": self._cmd_resync,
            "login": self._cmd_login,
            "logs": self._cmd_logs,
            "code": self._cmd_code,
            "backup": self._cmd_backup,
        }
        handler = dispatch.get(raw_cmd)
        if handler is None:
            self._send_message(f"❓ Unknown command: `{_esc(raw_cmd)}`\\. Use /help\\.")
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
            self._send_message(f"❓ Unknown instance: `{_esc(instance_key)}`")
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
            self._send_message(f"❓ Unknown instance: `{_esc(instance_key)}`")
            return
        self._launch_resync(inst, date_str)

    def _on_cb_instance_cmd(self, cmd: str, parts: list[str]) -> None:
        """Handle instance-routed callbacks: sync, login, logs."""
        instance_key = parts[-1].lower()
        inst = self._cfg.instances.get(instance_key)
        if inst is None:
            self._send_message(f"❓ Unknown instance: `{_esc(instance_key)}`")
            return

        if cmd == "sync":
            self._launch_sync(inst)
        elif cmd == "login":
            self._launch_login(inst)
        elif cmd == "logs":
            threading.Thread(
                target=self._fetch_and_send_logs,
                args=(inst,),
                daemon=True,
            ).start()
        else:
            log.warning("Unknown callback cmd: %r", cmd)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_help(self, _args: list[str]) -> None:
        lines = [
            "🤖 *Available commands*\n",
            "/sync — Force Trade Republic sync \\(choose instance\\)",
            "/resync `[YYYY\\-MM\\-DD]` — Force re\\-sync of a specific day, bypassing dedup",
            "/login — Renew Trade Republic 2FA session \\(choose instance\\)",
            "/logs — Show today's logs for an instance",
            "/code `<instance> <code>` — Submit an authenticator code",
        ]
        if self._cfg.backup_container:
            lines += [
                "/backup — Force a Wallet backup \\(guided by inline buttons\\)",
                "/backup `monthly [YYYY\\-MM]` — Monthly backup, optional period",
                "/backup `yearly [YYYY]` — Yearly backup, optional year",
            ]
        lines += [
            "/status — Show instances and backup service",
            "/help — This message",
        ]
        self._send_message("\n".join(lines))

    def _cmd_status(self, _args: list[str]) -> None:
        if not self._cfg.instances:
            self._send_message("⚠️ No instances configured\\.")
            return

        lines = ["📋 *Instance status*\n"]
        with _docker_client_ctx() as client:
            for inst in self._cfg.instances.values():
                lines.append(self._instance_status_line(inst, client))

        if self._cfg.backup_container:
            lines.append(f"\n💾 *Backup service*: `{_esc(self._cfg.backup_container)}`")
        else:
            lines.append("\n💾 *Backup service*: not configured")

        self._send_message("\n".join(lines))

    def _instance_status_line(
        self, inst: InstanceConfig, client: docker.DockerClient | None
    ) -> str:
        """Build a Telegram-formatted status line for a single sync instance."""
        running_state = (
            _docker_container_status(inst.container_name, client=client)
            if client
            else None
        )
        is_running = client and running_state == "running"
        auth = (
            _docker_check_session(inst.container_name, client=client)
            if is_running
            else None
        )
        last_sync = (
            _docker_last_sync_summary(inst.container_name, client=client)
            if is_running
            else None
        ) or "unavailable"
        auth_icon = _auth_icon(auth)
        return (
            f"• *{_esc(inst.name)}* — `{_esc(inst.container_name)}`"
            f"\n  state: *{_esc(running_state or 'unknown')}* · auth: {auth_icon}"
            f"\n  last: {_esc(last_sync)}"
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
                f"⚠️ Invalid date: `{_esc(date_str)}`\\. "
                "Expected format: `YYYY\\-MM\\-DD`\\."
            )
            return

        self._send_message(
            f"🔁 *Resync {_esc(date_str)}* — Choose instance:",
            keyboard=self._instance_buttons_for_resync(date_str),
        )

    def _cmd_login(self, _args: list[str]) -> None:
        self._pick_instance(
            "login", "🔐 *Login* — Choose instance to re\\-authenticate:"
        )

    def _cmd_logs(self, _args: list[str]) -> None:
        self._pick_instance("logs", "📋 *Logs* — Choose instance:")

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
            self._send_message(f"❓ Unknown instance: `{_esc(args[0])}`")
            return
        if not code.isdigit():
            self._send_message("⚠️ The code must contain digits only\\.")
            return

        self._send_message(f"🔑 Sending code to *{_esc(inst.name)}*\\.\\.\\.")
        self._exec_in_thread(
            inst.container_name, ["submit-code", code], on_error=self._send_message
        )

    def _cmd_backup(self, args: list[str]) -> None:
        if not self._cfg.backup_container:
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
                f"⚠️ Unknown backup type: `{_esc(type_arg)}`\\. Use `monthly` or `yearly`\\."
            )

    def _launch_backup(self, mode: str, period: str) -> None:
        if not self._cfg.backup_container:
            self._send_message("🚫 Backup service is not configured\\.")
            return
        icon = _BACKUP_ICONS.get(mode, "📦")
        label = _esc(f"{mode.capitalize()} backup ({period})")
        self._send_message(f"{icon} *{label}*\\.\\.\\.")
        self._exec_in_thread(
            self._cfg.backup_container,
            ["backup", mode, period],
            on_error=self._send_message,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _launch_sync(self, inst: InstanceConfig) -> None:
        self._send_message(f"▶️ Executing *sync* for *{_esc(inst.name)}*\\.\\.\\.")
        self._exec_in_thread(inst.container_name, ["sync"], on_error=self._send_message)

    def _launch_resync(self, inst: InstanceConfig, date_str: str) -> None:
        self._send_message(
            f"🔁 Executing *resync {_esc(date_str)}* for *{_esc(inst.name)}*\\.\\.\\."
        )
        self._exec_in_thread(
            inst.container_name,
            ["resync", date_str],
            on_error=self._send_message,
        )

    def _launch_login(self, inst: InstanceConfig) -> None:
        self._send_message(f"🔐 Re\\-authenticating *{_esc(inst.name)}*\\.\\.\\.")
        self._pending_login[inst.name.lower()] = inst
        self._exec_in_thread(
            inst.container_name,
            ["login"],
            on_error=lambda msg: self._on_login_error(inst, msg),
            on_success=lambda: self._on_login_success(inst),
        )

    def _on_login_success(self, inst: InstanceConfig) -> None:
        """Called when login completes successfully: notify user then auto-sync."""
        self._pending_login.pop(inst.name.lower(), None)
        self._send_message(f"✅ *{_esc(inst.name)}* session is ready\\.")
        self._launch_sync(inst)

    def _on_login_error(self, inst: InstanceConfig, msg: str) -> None:
        """Called when login fails: clear pending state and forward the error message."""
        self._pending_login.pop(inst.name.lower(), None)
        self._send_message(msg)

    def _maybe_submit_pending_code(self, code: str) -> bool:
        """Submit *code* to the single pending login instance, or prompt if ambiguous.

        When ``_pending_login`` is empty (login was triggered by a cron sync rather
        than the ``/login`` Telegram command), falls back to querying each container
        via ``check-pending`` to discover which ones are actively waiting for a code.
        """
        pending = dict(self._pending_login)  # snapshot before any iteration
        if not pending:
            instances = self._cfg.instances
            if len(instances) == 1:
                inst = next(iter(instances.values()))
                self._exec_in_thread(
                    inst.container_name,
                    ["submit-code", code],
                    on_error=self._send_message,
                )
                return True
            # Multi-instance: probe Docker to find which containers are awaiting a code.
            with _docker_client_ctx() as docker_client:
                docker_pending = {
                    name: inst
                    for name, inst in instances.items()
                    if _docker_check_awaiting_code(inst.container_name, docker_client)
                    is True
                }
            if len(docker_pending) == 1:
                inst = next(iter(docker_pending.values()))
                self._exec_in_thread(
                    inst.container_name,
                    ["submit-code", code],
                    on_error=self._send_message,
                )
                return True
            if len(docker_pending) > 1:
                names = ", ".join(f"*{_esc(k)}*" for k in sorted(docker_pending))
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
        if len(pending) == 1:
            inst = next(iter(pending.values()))
            self._exec_in_thread(
                inst.container_name, ["submit-code", code], on_error=self._send_message
            )
            return True
        names = ", ".join(f"*{_esc(k)}*" for k in sorted(pending))
        self._send_message(
            f"⚠️ Multiple logins pending: {names}\\. "
            "Use `/code <instance> <code>` to specify which one\\."
        )
        return False

    def _fetch_and_send_logs(self, inst: InstanceConfig) -> None:
        """Fetch today's logs for *inst* and send them to Telegram."""
        today_start = datetime.datetime.now(tz=datetime.UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        try:
            text = _docker_logs_today(inst.container_name, since=today_start)
        except Exception as exc:
            self._send_message(
                f"❌ Could not fetch logs for `{_esc(inst.container_name)}`: {_esc(str(exc))}"
            )
            return

        header = f"📋 Logs for *{_esc(inst.name)}* \\({_esc(today_start.strftime('%Y-%m-%d'))} UTC\\)\n\n"
        if not text.strip():
            self._send_message(header + "_No logs today\\._")
            return

        if len(text) > _MAX_LOG_CHARS:
            text = "[... truncated ...]\n" + text[-_MAX_LOG_CHARS:]

        self._send_message(header + text, parse_mode=None)

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

    def _exec_in_thread(
        self,
        container_name: str,
        app_args: list[str],
        on_error: Callable[[str], None] | None = None,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        """Launch ``_docker_exec_silent`` for *container_name* in a daemon thread."""
        threading.Thread(
            target=_docker_exec_silent,
            args=(container_name, app_args),
            kwargs={"on_error": on_error, "on_success": on_success},
            daemon=True,
        ).start()

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
            resp = requests.post(
                f"{self._api}/sendMessage",
                json=payload,
                timeout=20,
                verify=self._cfg.telegram_verify_ssl,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Failed to send Telegram message: %s", exc)

    def _answer_callback_query(self, callback_query_id: str) -> None:
        """Acknowledge the callback query to remove the loading spinner on the button."""
        try:
            requests.post(
                f"{self._api}/answerCallbackQuery",
                json={"callback_query_id": callback_query_id},
                timeout=10,
                verify=self._cfg.telegram_verify_ssl,
            )
        except requests.RequestException as exc:
            log.warning("Failed to answer callback query: %s", exc)

    def _delete_message(self, message_id: int | None) -> None:
        """Delete a message from the chat (used to purge sensitive 2FA codes)."""
        if message_id is None:
            return
        try:
            requests.post(
                f"{self._api}/deleteMessage",
                json={"chat_id": self._cfg.chat_id, "message_id": message_id},
                timeout=10,
                verify=self._cfg.telegram_verify_ssl,
            )
        except requests.RequestException as exc:
            log.warning("Failed to delete message %s: %s", message_id, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Load config from environment and start the bot."""
    cfg = BotConfig.from_env()
    bot = TelegramBot(cfg)
    bot.run()
