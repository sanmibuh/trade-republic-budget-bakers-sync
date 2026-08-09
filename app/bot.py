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
    /backup_monthly [YYYY-MM]          Force monthly backup (runs on the backup service).
    /backup_yearly  [YYYY]             Force yearly backup  (runs on the backup service).
    /status                            Show configured instances and backup service availability.
    /help                              Show available commands.

Interaction flow for /sync:
    1. User sends /sync.
    2. Bot replies with inline keyboard buttons, one per configured instance.
    3. User taps an instance button.
    4. Bot sends an ACK ("▶️ Executing sync for David...") and launches docker exec in background.
    5. The container's own Notifier sends the final Telegram result notification when done.

Interaction flow for /backup_monthly / /backup_yearly:
    1. User sends the command (with optional period param).
    2. Bot sends an ACK and launches docker exec on the backup service directly.
    3. The backup container's Notifier sends the result notification.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

import requests
import urllib3

from app.config import BotEnv
from app.notifier import _escape_markdown as _esc

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"
_EXEC_TIMEOUT = 600  # seconds — sync can take a while

# Set TELEGRAM_VERIFY_SSL=false to disable SSL verification (e.g. behind a corporate proxy).
_SSL_VERIFY: bool = os.environ.get("TELEGRAM_VERIFY_SSL", "true").strip().lower() != "false"

# Separator used inside callback_data to encode command + param + instance.
# Must not appear in instance names or period params (YYYY-MM / YYYY contain only digits and hyphens).
_CB_SEP = ":"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InstanceConfig:
    name: str            # human-readable, used in commands (e.g. "david")
    container_name: str  # Docker container name (e.g. "myproject-sync-david-1")


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    chat_id: str
    instances: dict[str, InstanceConfig] = field(default_factory=dict)
    backup_container: str | None = None  # None means backup commands are disabled

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

        backup_container = f"{env.container_prefix}-{env.backup_service}-1" if env.backup_service else None

        return cls(
            bot_token=env.bot_token,
            chat_id=env.chat_id,
            instances=instances,
            backup_container=backup_container,
        )


# ---------------------------------------------------------------------------
# Markdown helpers (MarkdownV2)
# ---------------------------------------------------------------------------
# _esc is imported from app.notifier._escape_markdown — single source of truth.

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------

class TelegramBot:
    """Long-polling Telegram bot that dispatches commands to Docker containers."""

    def __init__(self, cfg: BotConfig) -> None:
        self._cfg = cfg
        self._api = _TELEGRAM_API.format(token=cfg.bot_token)
        self._offset = 0

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self) -> None:
        log.info("Telegram bot started — polling for updates (authorized chat: %s)", self._cfg.chat_id)
        self._register_commands()
        self._send_message("🤖 *Bot started and ready\\.* Use /help to see available commands\\.")
        while True:
            try:
                self._poll_once()
            except KeyboardInterrupt:
                log.info("Bot stopped by keyboard interrupt")
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("Polling error: %s", exc)
                time.sleep(5)

    # ------------------------------------------------------------------
    # Command registration (Telegram autocomplete menu)
    # ------------------------------------------------------------------

    def _register_commands(self) -> None:
        commands = [
            {"command": "sync",           "description": "Force Trade Republic sync (choose instance)"},
            {"command": "status",         "description": "Show instances and backup service availability"},
            {"command": "help",           "description": "Show available commands"},
        ]
        if self._cfg.backup_container:
            commands[1:1] = [
                {"command": "backup_monthly", "description": "Force monthly backup [YYYY-MM]"},
                {"command": "backup_yearly",  "description": "Force yearly backup [YYYY]"},
            ]
        try:
            resp = requests.post(
                f"{self._api}/setMyCommands",
                json={"commands": commands},
                timeout=10,
                verify=_SSL_VERIFY,
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
            verify=_SSL_VERIFY,
        )
        resp.raise_for_status()
        for update in resp.json().get("result", []):
            self._offset = update["update_id"] + 1
            try:
                self._handle_update(update)
            except Exception as exc:  # noqa: BLE001
                log.warning("Error handling update %s: %s", update.get("update_id"), exc)

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
        if not text.startswith("/"):
            return

        parts = text.split()
        raw_cmd = parts[0].lstrip("/").split("@")[0].lower()
        args = parts[1:]

        dispatch = {
            "help":           self._cmd_help,
            "status":         self._cmd_status,
            "sync":           self._cmd_sync,
            "backup_monthly": self._cmd_backup_monthly,
            "backup_yearly":  self._cmd_backup_yearly,
        }
        handler = dispatch.get(raw_cmd)
        if handler is None:
            self._send_message(f"❓ Unknown command: `{_esc(raw_cmd)}`\\. Use /help\\.")
            return
        handler(args)

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
        # Encoded format: "<cmd>:<instance>" or "<cmd>:<param>:<instance>"
        if len(parts) < 2:
            log.warning("Malformed callback_data: %r", data)
            return

        cmd = parts[0]
        instance_key = parts[-1].lower()

        inst = self._cfg.instances.get(instance_key)
        if inst is None:
            self._send_message(f"❓ Unknown instance: `{_esc(instance_key)}`")
            return

        if cmd == "sync":
            self._launch_sync(inst)
        else:
            log.warning("Unknown callback cmd: %r", cmd)

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    def _cmd_help(self, _args: list[str]) -> None:
        lines = [
            "🤖 *Available commands*\n",
            "/sync — Force Trade Republic sync \\(choose instance\\)",
        ]
        if self._cfg.backup_container:
            lines += [
                "/backup\\_monthly `[YYYY\\-MM]` — Monthly backup \\(default: previous month\\)",
                "/backup\\_yearly `[YYYY]` — Yearly backup \\(default: previous year\\)",
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
        for inst in self._cfg.instances.values():
            lines.append(
                f"• *{_esc(inst.name)}* — `{_esc(inst.container_name)}`"
            )

        if self._cfg.backup_container:
            lines.append(f"\n💾 *Backup service*: `{_esc(self._cfg.backup_container)}`")
        else:
            lines.append("\n💾 *Backup service*: not configured")

        self._send_message("\n".join(lines))

    def _cmd_sync(self, _args: list[str]) -> None:
        buttons = self._instance_buttons("sync")
        self._send_message("🔄 *Sync* — Choose instance:", keyboard=buttons)

    def _cmd_backup_monthly(self, args: list[str]) -> None:
        if not self._cfg.backup_container:
            self._send_message("🚫 Backup service is not configured\\.")
            return
        param = args[0] if args else None
        label = _esc(f"Monthly backup ({param or 'previous month'})")
        self._send_message(f"📦 *{label}*\\.\\.\\.")
        app_args = ["backup", "monthly"] + ([param] if param else [])
        threading.Thread(
            target=_docker_exec_silent,
            args=(self._cfg.backup_container, app_args),
            daemon=True,
        ).start()

    def _cmd_backup_yearly(self, args: list[str]) -> None:
        if not self._cfg.backup_container:
            self._send_message("🚫 Backup service is not configured\\.")
            return
        param = args[0] if args else None
        label = _esc(f"Yearly backup ({param or 'previous year'})")
        self._send_message(f"📆 *{label}*\\.\\.\\.")
        app_args = ["backup", "yearly"] + ([param] if param else [])
        threading.Thread(
            target=_docker_exec_silent,
            args=(self._cfg.backup_container, app_args),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _launch_sync(self, inst: InstanceConfig) -> None:
        self._send_message(f"▶️ Executing *sync* for *{_esc(inst.name)}*\\.\\.\\.")
        threading.Thread(
            target=_docker_exec_silent,
            args=(inst.container_name, ["sync"]),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Keyboard builder
    # ------------------------------------------------------------------

    def _instance_buttons(self, cmd: str) -> list[list[dict]]:
        """Build an inline keyboard row with one button per sync instance."""
        buttons = [
            {"text": inst.name, "callback_data": f"{cmd}{_CB_SEP}{inst.name.lower()}"}
            for inst in self._cfg.instances.values()
        ]
        return [buttons[i:i + 3] for i in range(0, len(buttons), 3)]

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    def _send_message(self, text: str, keyboard: list[list[dict]] | None = None) -> None:
        payload: dict = {
            "chat_id": self._cfg.chat_id,
            "text": text,
            "parse_mode": "MarkdownV2",
            "disable_web_page_preview": True,
        }
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            resp = requests.post(
                f"{self._api}/sendMessage",
                json=payload,
                timeout=20,
                verify=_SSL_VERIFY,
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
                verify=_SSL_VERIFY,
            )
        except requests.RequestException as exc:
            log.warning("Failed to answer callback query: %s", exc)


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------

def _docker_exec_silent(container_name: str, app_args: list[str]) -> None:
    """Run `docker exec <container> python -m app <app_args>` and log the result.

    Does NOT send any Telegram message — the container's own Notifier handles that.
    """
    cmd = ["docker", "exec", container_name, "python", "-m", "app"] + app_args
    log.info("Executing: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_EXEC_TIMEOUT,
            check=False,
        )
        if result.returncode == 0:
            log.info("docker exec finished successfully for container %s", container_name)
        else:
            log.warning(
                "docker exec exited with code %s for container %s:\n%s",
                result.returncode,
                container_name,
                (result.stdout + result.stderr).strip(),
            )
    except subprocess.TimeoutExpired:
        log.warning("docker exec timed out after %ss for container %s", _EXEC_TIMEOUT, container_name)
    except Exception as exc:  # noqa: BLE001
        log.warning("docker exec failed for container %s: %s", container_name, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Load config from environment and start the bot."""
    cfg = BotConfig.from_env()
    bot = TelegramBot(cfg)
    bot.run()
