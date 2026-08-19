"""app CLI entry point.

Usage:
    python -m app                          # shows help
    python -m app sync                     # one-shot TR → Wallet sync (env vars)
    python -m app sync --instance user1    # one-shot sync for a named instance (YAML config)
    python -m app backup auto              # smart daily backup
    python -m app backup monthly           # backup previous month
    python -m app backup monthly 2026-07   # backup specific month
    python -m app backup yearly            # backup previous year
    python -m app backup yearly 2025       # backup specific year
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from app.logging_setup import setup_logging

if TYPE_CHECKING:
    from app.config import Config


def _resolve_instance_cfg(instance: str) -> Config:
    """Load ``InstancesConfig`` via ``INSTANCES_CONFIG`` env var and return the
    ``Config`` for *instance*.

    ``INSTANCES_CONFIG`` is read through :func:`app.config.read_instances_config_path`
    so that all env var access stays in ``config.py``.  Any ``ValueError`` or
    ``FileNotFoundError`` is re-raised as a :class:`click.UsageError` so the user
    sees a clean error message instead of a traceback.

    Raises :class:`click.UsageError` immediately when *instance* is blank, so
    passing ``--instance ""`` never silently falls back to env-var mode.
    """
    from app.config import InstancesConfig, read_instances_config_path

    if not instance.strip():
        raise click.UsageError("--instance value must not be blank")
    instance = instance.strip()
    try:
        path = read_instances_config_path()
        return InstancesConfig.load(path).to_config(instance)
    except (ValueError, FileNotFoundError) as exc:
        raise click.UsageError(str(exc)) from exc


@click.group()
def cli() -> None:
    """Trade Republic → BudgetBakers Wallet sync and backup tool."""


@cli.command()
@click.option(
    "--instance",
    default=None,
    metavar="NAME",
    help="Instance name from the INSTANCES_CONFIG YAML file. "
    "When set, credentials are loaded from the file instead of env vars.",
)
def sync(instance: str | None) -> None:
    """Run a one-shot Trade Republic → Wallet sync."""
    from app.config import Config
    from app.http_client import configure
    from app.main import run

    if instance is not None:
        cfg = _resolve_instance_cfg(instance)
        log_dir = cfg.data_dir.parent / "logs"
    else:
        cfg = Config.from_env()
        log_dir = cfg.data_dir / "logs"
    setup_logging(log_dir)
    configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    sys.exit(run(cfg=cfg))


@cli.command()
@click.option(
    "--instance",
    default=None,
    metavar="NAME",
    help="Instance name from the INSTANCES_CONFIG YAML file.",
)
def login(instance: str | None) -> None:
    """Re-authenticate with Trade Republic on demand (renew the 2FA session).

    Resumes the saved session if still valid; otherwise runs the full login.
    For authenticator accounts the code is requested via Telegram (reply with
    /code <instance> <code>); for push accounts, approve the request in the app.
    """
    from app.config import Config
    from app.http_client import configure
    from app.main import run_login

    if instance is not None:
        cfg = _resolve_instance_cfg(instance)
        log_dir = cfg.data_dir.parent / "logs"
    else:
        cfg = Config.from_env()
        log_dir = cfg.data_dir / "logs"
    setup_logging(log_dir)
    configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    sys.exit(run_login(cfg=cfg))


@cli.command(name="submit-code")
@click.argument("code")
def submit_code(code: str) -> None:
    """Write an authenticator CODE for a waiting login process to pick up.

    Used by the Telegram bot (the /code command) to deliver the 2FA code into
    the sync container, where the blocked login process is polling for it.

    Exits with an error if no login is currently waiting (the pending marker
    file is absent), so stale /code submissions are rejected cleanly.
    """
    from app.config import Config
    from app.twofa import CODE_FILENAME, PENDING_FILENAME

    cfg = Config.from_env()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    pending_file = cfg.data_dir / PENDING_FILENAME
    if not pending_file.exists():
        click.echo("No active login request for this instance.")
        sys.exit(1)
    (cfg.data_dir / CODE_FILENAME).write_text(code.strip())


@cli.command(name="check-pending")
def check_pending() -> None:
    """Exit 0 if a 2FA login is currently waiting for a code, 1 otherwise.

    Used by the Telegram bot to detect which containers are blocked on
    authenticator input, so plain-digit replies can be routed automatically
    even when the login was triggered by a cron sync rather than the /login
    command.

    Exit codes:
        0 — the pending-login marker file is present (container awaiting code).
        1 — no active 2FA request for this instance.
    """
    from app.config import Config
    from app.twofa import PENDING_FILENAME

    cfg = Config.from_env()
    pending_file = cfg.data_dir / PENDING_FILENAME
    sys.exit(0 if pending_file.exists() else 1)


@cli.command(name="check-session")
def check_session() -> None:
    """Exit 0 if a saved Trade Republic session exists, 1 if login is required.

    Used by the Telegram bot to report per-instance authentication state in
    /status without making any network calls.

    pytr persists the session as cookies.txt (written by save_websession()).
    The check reads the cookie expiry timestamps so that a file with only
    expired cookies is correctly reported as needing re-authentication.

    Additionally, if ``sync.db`` contains an ``auth_state`` row for this
    instance with status ``"failed"`` or ``"expired"``, the command exits 1
    even when a cookies file is present — this catches the case where a failed
    login left the old session file in place.

    Exit codes:
        0 — session valid and auth state is ``ok`` (or no state recorded yet).
        1 — session missing, expired, or auth state is ``failed``/``expired``.
        2 — DB could not be read (corrupted/locked); the bot treats this as
            an unknown/unreachable state rather than a hard auth failure.
    """
    from app.config import has_valid_session, read_data_dir, read_instance
    from app.persistence import EventRepository

    data_dir = read_data_dir()
    if not has_valid_session(data_dir):
        sys.exit(1)

    db_path = data_dir / "sync.db"
    if db_path.exists():
        try:
            with EventRepository(db_path) as repo:
                auth_status = repo.get_auth_state(read_instance())
        except Exception:
            sys.exit(2)
        if auth_status in ("failed", "expired"):
            sys.exit(1)

    sys.exit(0)


@cli.command()
@click.argument("mode", type=click.Choice(["auto", "monthly", "yearly"]))
@click.argument("param", required=False, default=None)
def backup(mode: str, param: str | None) -> None:
    """Run a Wallet backup.

    \b
    MODE choices:
      auto              Smart daily backup (intended for scheduled cron).
                        Always backs up current and previous month.
                        In February, also generates the yearly backup for the
                        previous year (skipped if it already exists).
      monthly [PARAM]   Backup a specific month (YYYY-MM). Default: previous month.
      yearly  [PARAM]   Backup a specific year  (YYYY).   Default: previous year.
                        Also removes the covered monthly files.

    \b
    Examples:
      python -m app backup auto
      python -m app backup monthly
      python -m app backup monthly 2026-07
      python -m app backup yearly
      python -m app backup yearly 2025
    """

    import click

    from app.backup import (
        _parse_monthly_param,
        _parse_yearly_param,
        run_auto,
        run_monthly,
        run_yearly,
    )
    from app.config import BackupConfig
    from app.http_client import configure
    from app.notifier import Notifier
    from app.wallet_client import WalletClient

    cfg = BackupConfig.from_env()
    setup_logging(cfg.data_dir / "logs")
    configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    client = WalletClient(api_key=cfg.wallet_api_key)
    notifier = Notifier(
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
        owner_name=cfg.owner_name,
    )

    if mode == "auto":
        run_auto(client, notifier, cfg.data_dir)
    elif mode == "monthly":
        try:
            year, month = _parse_monthly_param(param)
        except ValueError:
            raise click.BadParameter(
                f"Expected YYYY-MM, got {param!r}", param_hint="PARAM"
            ) from None
        run_monthly(client, notifier, cfg.data_dir, year, month)
    elif mode == "yearly":
        try:
            year = _parse_yearly_param(param)
        except ValueError:
            raise click.BadParameter(
                f"Expected YYYY, got {param!r}", param_hint="PARAM"
            ) from None
        run_yearly(client, notifier, cfg.data_dir, year)


@cli.command()
@click.argument("date")
def resync(date: str) -> None:
    """Force a re-sync of all TR events for DATE (YYYY-MM-DD), bypassing dedup.

    Already-synced events are updated in BudgetBakers via PUT; new or previously
    excluded events are inserted via POST.  Use this to correct a specific day
    without touching the rest of the history.

    \b
    Example:
      python -m app resync 2026-07-15
    """
    from app.config import Config
    from app.http_client import configure
    from app.main import run_resync

    cfg = Config.from_env()
    setup_logging(cfg.data_dir / "logs")
    configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    sys.exit(run_resync(date, cfg=cfg))


@cli.command(name="list-instances")
def list_instances() -> None:
    """List all instance names from the INSTANCES_CONFIG YAML file, one per line.

    Used by entrypoint.sh to register one cron job per instance in
    multi-instance mode.  Exits with an error when INSTANCES_CONFIG is not
    set or the file cannot be loaded.
    """
    from app.config import InstancesConfig, read_instances_config_path

    try:
        path = read_instances_config_path()
        cfg = InstancesConfig.load(path)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc

    for inst in cfg.instances:
        click.echo(inst.name)


@cli.command()
def bot() -> None:
    """Start the Telegram bot for remote command execution.

    Listens for commands from the authorized Telegram chat and dispatches
    them directly as in-process Python calls (no Docker SDK required).

    \b
    Required environment variables:
      TELEGRAM_BOT_TOKEN    Bot token from BotFather.
      TELEGRAM_CHAT_ID      Authorized chat ID — only this chat can issue commands.
      INSTANCES_CONFIG      Path to the instances YAML config file.

    \b
    Supported Telegram commands:
      /help
      /status
      /sync              (shows an inline keyboard to pick the instance)
      /login             (shows an inline keyboard to pick the instance)
      /resync [YYYY-MM-DD]
      /logs              (shows today's shared sync log)
      /code <instance> <code>  (or send the 6-digit code as a plain message)
      /backup [monthly|yearly] [period]
    """
    from app.bot import run
    from app.config import InstancesConfig, read_instances_config_path

    try:
        instances_yaml = InstancesConfig.load(read_instances_config_path())
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc
    setup_logging(instances_yaml.data_dir / "logs")
    try:
        run()
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc


if __name__ == "__main__":  # pragma: no cover
    cli()
