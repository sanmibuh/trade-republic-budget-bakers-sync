"""Telegram bot for remote command execution across multi-tenant instances.

Usage (via CLI):
    python -m app bot

Environment variables:
    TELEGRAM_BOT_TOKEN  Required. Bot token from BotFather.
    TELEGRAM_CHAT_ID    Required. Authorized chat ID (only this chat can issue commands).
    INSTANCES           Required. Comma-separated list of instance names (e.g. "david,eli").
    CONTAINER_PREFIX    Required. Docker container name prefix (e.g. "trade-republic-budget-bakers-sync").

Backup availability is determined automatically by inspecting whether the target container has
BACKUP_SCHEDULE defined in its environment (same variable used by entrypoint.sh to register the
cron job). No extra configuration needed in the bot service.

Commands (registered via setMyCommands for Telegram autocomplete):
    /sync                              Force a Trade Republic sync — choose instance via inline buttons.
    /backup_monthly [YYYY-MM]          Force monthly backup — choose instance via inline buttons.
    /backup_yearly  [YYYY]             Force yearly backup  — choose instance via inline buttons.
    /status                            Show configured instances and their capabilities.
    /help                              Show available commands.

Interaction flow:
    1. User sends a command (e.g. /sync or /backup_monthly 2026-07).
    2. Bot replies with inline keyboard buttons, one per configured instance.
    3. User taps an instance button.
    4. Bot sends an ACK ("▶️ Executing ...") and launches docker exec in a background thread.
    5. The container's own Notifier sends the final Telegram result notification when done.
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

# Human-readable period unit per backup mode (used in ACK messages).
_MODE_UNIT: dict[str, str] = {"monthly": "month", "yearly": "year"}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InstanceConfig:
    name: str            # human-readable, used in commands (e.g. "david")
    container_name: str  # Docker container name (e.g. "trade-republic-budget-bakers-sync-david-1")


@dataclass(frozen=True)
class BotConfig:
    bot_token: str
    chat_id: str
    instances: dict[str, InstanceConfig] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> BotConfig:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if not bot_token:
            raise ValueError("Missing required environment variable: TELEGRAM_BOT_TOKEN")
        if not chat_id:
            raise ValueError("Missing required environment variable: TELEGRAM_CHAT_ID")

        raw_instances = os.environ.get("INSTANCES", "").strip()
        if not raw_instances:
            raise ValueError("Missing required environment variable: INSTANCES")

        prefix = os.environ.get("CONTAINER_PREFIX", "").strip()
        if not prefix:
            raise ValueError("Missing required environment variable: CONTAINER_PREFIX")

        instances: dict[str, InstanceConfig] = {}
        for name in [n.strip() for n in raw_instances.split(",") if n.strip()]:
            container_name = f"{prefix}-{name.lower()}-1"
            instances[name.lower()] = InstanceConfig(
                name=name,
                container_name=container_name,
            )

        return cls(bot_token=bot_token, chat_id=chat_id, instances=instances)


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
            {"command": "backup_monthly", "description": "Force monthly backup [YYYY-MM] (choose instance)"},
            {"command": "backup_yearly",  "description": "Force yearly backup [YYYY] (choose instance)"},
            {"command": "status",         "description": "Show instances and backup availability"},
            {"command": "help",           "description": "Show available commands"},
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
        param = parts[1] if len(parts) == 3 else None  # present only for backup commands with a param

        inst = self._cfg.instances.get(instance_key)
        if inst is None:
            self._send_message(f"❓ Unknown instance: `{_esc(instance_key)}`")
            return

        if cmd == "sync":
            self._launch_sync(inst)
        elif cmd == "backup_monthly":
            self._launch_backup(inst, "monthly", param)
        elif cmd == "backup_yearly":
            self._launch_backup(inst, "yearly", param)
        else:
            log.warning("Unknown callback cmd: %r", cmd)

    # ------------------------------------------------------------------
    # Command handlers — show instance picker keyboard
    # ------------------------------------------------------------------

    def _cmd_help(self, _args: list[str]) -> None:
        lines = [
            "🤖 *Available commands*\n",
            "/sync — Force Trade Republic sync",
            "/backup\\_monthly `[YYYY\\-MM]` — Monthly backup \\(default: previous month\\)",
            "/backup\\_yearly `[YYYY]` — Yearly backup \\(default: previous year\\)",
            "/status — Show instances and backup availability",
            "/help — This message",
        ]
        self._send_message("\n".join(lines))

    def _cmd_status(self, _args: list[str]) -> None:
        if not self._cfg.instances:
            self._send_message("⚠️ No instances configured\\.")
            return

        lines = ["📋 *Instance status*\n"]
        for inst in self._cfg.instances.values():
            backup_ok = _container_has_backup_schedule(inst.container_name)
            backup_icon = "✅" if backup_ok else "❌"
            lines.append(
                f"• *{_esc(inst.name)}*\n"
                f"  Container: `{_esc(inst.container_name)}`\n"
                f"  Backup available: {backup_icon}"
            )
        self._send_message("\n".join(lines))

    def _cmd_sync(self, _args: list[str]) -> None:
        buttons = self._instance_buttons("sync", param=None)
        self._send_message("🔄 *Sync* — Choose instance:", keyboard=buttons)

    def _cmd_backup_monthly(self, args: list[str]) -> None:
        param = args[0] if args else None
        label = f"Monthly backup ({param or 'previous month'})"
        buttons = self._instance_buttons("backup_monthly", param=param, check_backup=True)
        self._send_message(f"📦 *{_esc(label)}* — Choose instance:", keyboard=buttons)

    def _cmd_backup_yearly(self, args: list[str]) -> None:
        param = args[0] if args else None
        label = f"Yearly backup ({param or 'previous year'})"
        buttons = self._instance_buttons("backup_yearly", param=param, check_backup=True)
        self._send_message(f"📆 *{_esc(label)}* — Choose instance:", keyboard=buttons)

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

    def _launch_backup(self, inst: InstanceConfig, mode: str, param: str | None) -> None:
        if not _container_has_backup_schedule(inst.container_name):
            self._send_message(
                f"🚫 *{_esc(inst.name)}* has no `BACKUP_SCHEDULE` configured — "
                f"backup cron is not registered\\."
            )
            return
        unit = _MODE_UNIT.get(mode, mode)
        period_label = _esc(param or f"previous {unit}")
        self._send_message(
            f"▶️ Executing *backup {_esc(mode)}* \\(`{period_label}`\\) for *{_esc(inst.name)}*\\.\\.\\."
        )
        app_args = ["backup", mode] + ([param] if param else [])
        threading.Thread(
            target=_docker_exec_silent,
            args=(inst.container_name, app_args),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Keyboard builder
    # ------------------------------------------------------------------

    def _instance_buttons(
        self,
        cmd: str,
        param: str | None,
        check_backup: bool = False,
    ) -> list[list[dict]]:
        """Build an inline keyboard row with one button per instance.

        When check_backup=True, instances without BACKUP_SCHEDULE are shown with a 🚫 prefix
        and their callback_data is set to a no-op token so tapping them does nothing harmful.
        The docker inspect calls run concurrently to avoid blocking the polling thread.
        """
        # Resolve backup availability in parallel when needed.
        if check_backup:
            results: dict[str, bool] = {}

            def _check(name: str, container: str) -> None:
                results[name] = _container_has_backup_schedule(container)

            threads = [
                threading.Thread(target=_check, args=(key, inst.container_name), daemon=True)
                for key, inst in self._cfg.instances.items()
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=12)
            availability = results
        else:
            availability = dict.fromkeys(self._cfg.instances, True)

        buttons = []
        for key, inst in self._cfg.instances.items():
            has_backup = availability.get(key, False)
            if check_backup and not has_backup:
                # Show the instance as unavailable; noop callback so the tap is acknowledged
                # but produces no action (handled gracefully in _handle_callback_query).
                buttons.append({"text": f"🚫 {inst.name}", "callback_data": "noop"})
            else:
                data_parts = [cmd, param, inst.name.lower()] if param else [cmd, inst.name.lower()]
                buttons.append({"text": inst.name, "callback_data": _CB_SEP.join(data_parts)})

        # All instances in a single row; split into rows of 3 if many instances.
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

def _container_has_backup_schedule(container_name: str) -> bool:
    """Return True if the container has BACKUP_SCHEDULE set (non-empty) in its environment.

    Uses `docker inspect` via subprocess — requires the Docker socket to be mounted.
    Returns False if the container is not found or the inspect call fails.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{range .Config.Env}}{{.}}\n{{end}}", container_name],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            log.warning("docker inspect failed for %s: %s", container_name, result.stderr.strip())
            return False
        for line in result.stdout.splitlines():
            if line.startswith("BACKUP_SCHEDULE=") and line[len("BACKUP_SCHEDULE="):].strip():
                return True
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not inspect container %s: %s", container_name, exc)
        return False


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
