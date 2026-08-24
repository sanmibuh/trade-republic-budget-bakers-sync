"""app CLI entry point.

Usage:
    python -m app                                    # shows help
    python -m app sync --instance user1             # one-shot TR → Wallet sync
    python -m app resync --instance user1 DATE      # force re-sync for a date
    python -m app submit-code --instance user1 CODE # deliver 2FA code
    python -m app check-pending --instance user1    # check if 2FA is waiting
    python -m app backup auto                       # smart daily backup
    python -m app backup monthly                    # backup previous month
    python -m app backup monthly 2026-07            # backup specific month
    python -m app backup yearly                     # backup previous year
    python -m app backup yearly 2025                # backup specific year
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

from app.logging_setup import setup_logging

if TYPE_CHECKING:
    from pathlib import Path

    from app.config import BackupConfig, Config


def _resolve_instance_cfg(instance: str) -> tuple[Config, Path]:
    """Load ``InstancesConfig`` from the hardcoded path and return the
    ``Config`` for *instance* together with the root data directory.

    Any ``ValueError`` or ``OSError`` (including ``FileNotFoundError`` and
    ``PermissionError``) is re-raised as a :class:`click.UsageError` so the
    user sees a clean error message instead of a traceback.

    Raises :class:`click.UsageError` immediately when *instance* is blank.
    """
    from app.config import INSTANCES_CONFIG_PATH, InstancesConfig

    if not instance.strip():
        raise click.UsageError("--instance value must not be blank")
    instance = instance.strip()
    try:
        instances_yaml = InstancesConfig.load(INSTANCES_CONFIG_PATH)
        return instances_yaml.to_config(instance), instances_yaml.data_dir
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc


@click.group()
def cli() -> None:
    """Trade Republic → BudgetBakers Wallet sync and backup tool."""


@cli.command()
@click.option(
    "--instance",
    required=True,
    metavar="NAME",
    help="Instance name from the instances YAML config file.",
)
def sync(instance: str) -> None:
    """Run a one-shot Trade Republic → Wallet sync."""
    from app.http_client import configure
    from app.main import run
    from app.persistence import init_db

    cfg, log_dir = _resolve_instance_cfg(instance)
    setup_logging(log_dir)
    configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    init_db(cfg.shared_db_path)
    sys.exit(run(cfg=cfg))


@cli.command(name="submit-code")
@click.option(
    "--instance",
    required=True,
    metavar="NAME",
    help="Instance name from the instances YAML config file.",
)
@click.argument("code")
def submit_code(instance: str, code: str) -> None:
    """Write an authenticator CODE for a waiting login process to pick up.

    Used by the Telegram bot (the /code command) to deliver the 2FA code into
    the sync container, where the blocked login process is polling for it.

    Exits with an error if no login is currently waiting (the pending marker
    file is absent), so stale /code submissions are rejected cleanly.
    """
    cfg, _ = _resolve_instance_cfg(instance)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    pending_file = cfg.twofa_pending_file
    if not pending_file.exists():
        click.echo("No active login request for this instance.")
        sys.exit(1)
    cfg.twofa_code_file.write_text(code.strip())


@cli.command(name="check-pending")
@click.option(
    "--instance",
    required=True,
    metavar="NAME",
    help="Instance name from the instances YAML config file.",
)
def check_pending(instance: str) -> None:
    """Exit 0 if a 2FA login is currently waiting for a code, 1 otherwise.

    Used by the Telegram bot to detect which containers are blocked on
    authenticator input, so plain-digit replies can be routed automatically
    when a sync-triggered 2FA flow is waiting for a code.

    Exit codes:
        0 — the pending-login marker file is present (container awaiting code).
        1 — no active 2FA request for this instance.
    """
    cfg, _ = _resolve_instance_cfg(instance)
    pending_file = cfg.twofa_pending_file
    sys.exit(0 if pending_file.exists() else 1)


def _resolve_backup_cfg() -> BackupConfig:
    """Load ``InstancesConfig`` and return the derived :class:`BackupConfig`.

    Raises :class:`click.UsageError` for any config or I/O error so the user
    sees a clean message instead of a traceback.
    """
    from app.config import (
        INSTANCES_CONFIG_PATH,
        BackupConfig,
        InstancesConfig,
    )

    try:
        instances_yaml = InstancesConfig.load(INSTANCES_CONFIG_PATH)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc
    cfg = BackupConfig.from_instances_yaml(instances_yaml)
    if cfg is None:
        raise click.UsageError(
            "instances config has no instances — cannot build backup config"
        )
    return cfg


def _run_backup_mode(
    client: object,
    notifier: object,
    data_dir: object,
    mode: str,
    param: str | None,
) -> None:
    """Dispatch to the correct backup runner based on *mode*."""
    from app.backup import (
        parse_monthly_param,
        parse_yearly_param,
        run_auto,
        run_monthly,
        run_yearly,
    )

    if mode == "auto":
        run_auto(client, notifier, data_dir)
    elif mode == "monthly":
        try:
            year, month = parse_monthly_param(param)
        except ValueError:
            raise click.BadParameter(
                f"Expected YYYY-MM, got {param!r}", param_hint="PARAM"
            ) from None
        run_monthly(client, notifier, data_dir, year, month)
    elif mode == "yearly":
        try:
            year = parse_yearly_param(param)
        except ValueError:
            raise click.BadParameter(
                f"Expected YYYY, got {param!r}", param_hint="PARAM"
            ) from None
        run_yearly(client, notifier, data_dir, year)


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
    from app.http_client import configure
    from app.notifier import Notifier
    from app.wallet_client import WalletClient

    cfg = _resolve_backup_cfg()
    setup_logging(cfg.data_dir)
    configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    client = WalletClient(api_key=cfg.wallet_api_key)
    notifier = Notifier(
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
        owner_name=cfg.owner_name,
    )
    _run_backup_mode(client, notifier, cfg.data_dir, mode, param)


@cli.command()
@click.option(
    "--instance",
    required=True,
    metavar="NAME",
    help="Instance name from the instances YAML config file.",
)
@click.argument("date")
def resync(instance: str, date: str) -> None:
    """Force a re-sync of all TR events for DATE (YYYY-MM-DD), bypassing dedup.

    Already-synced events are updated in BudgetBakers via PUT; new or previously
    excluded events are inserted via POST.  Use this to correct a specific day
    without touching the rest of the history.

    \b
    Example:
      python -m app resync --instance user1 2026-07-15
    """
    from app.http_client import configure
    from app.main import run_resync
    from app.persistence import init_db

    cfg, log_dir = _resolve_instance_cfg(instance)
    setup_logging(log_dir)
    configure(allow_insecure_ssl=cfg.allow_insecure_ssl)
    init_db(cfg.shared_db_path)
    sys.exit(run_resync(date, cfg=cfg))


@cli.command(name="list-instances")
def list_instances() -> None:
    """List all instance names from the instances YAML config file, one per line.

    Used by entrypoint.sh to register one cron job per instance.
    Exits with an error when the file cannot be loaded.
    """
    from app.config import INSTANCES_CONFIG_PATH, InstancesConfig

    try:
        cfg = InstancesConfig.load(INSTANCES_CONFIG_PATH)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc

    for inst in cfg.instances:
        click.echo(inst.name)


@cli.command(name="list-schedules")
def list_schedules() -> None:
    """List per-instance sync schedules as 'name<TAB>schedule', one per line.

    Only instances that have a schedule defined (via sync.schedule or a per-instance
    schedule override) are emitted.  Instances with no schedule are omitted.

    Used by entrypoint.sh to register one cron job per instance with its own
    schedule.
    """
    from app.config import INSTANCES_CONFIG_PATH, InstancesConfig

    try:
        cfg = InstancesConfig.load(INSTANCES_CONFIG_PATH)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc

    for inst in cfg.instances:
        if inst.schedule:
            click.echo(f"{inst.name}\t{inst.schedule}")


@cli.command(name="get-backup-schedule")
def get_backup_schedule() -> None:
    """Print the backup_schedule from the instances YAML config file, or nothing.

    Exits 0 in both cases (schedule present or absent).  Used by entrypoint.sh
    to conditionally register the backup cron job.
    """
    from app.config import INSTANCES_CONFIG_PATH, InstancesConfig

    try:
        cfg = InstancesConfig.load(INSTANCES_CONFIG_PATH)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc

    if cfg.backup_schedule:
        click.echo(cfg.backup_schedule)


@cli.command()
def bot() -> None:
    """Start the Telegram bot for remote command execution.

    Listens for commands from the authorized Telegram chat and dispatches
    them directly as in-process Python calls (no Docker SDK required).

    \b
    Required fields in instances.yml:
      telegram_bot_token    Bot token from BotFather.
      telegram_chat_id      Authorized chat ID — only this chat can issue commands.

    \b
    Supported Telegram commands:
      /status
      /sync              (shows an inline keyboard to pick the instance)
      /resync [YYYY-MM-DD]
      /logs              (shows today's shared sync log)
      /code <instance> <code>  (or send the 6-digit code as a plain message)
      /backup [monthly|yearly] [period]
    """
    from app.bot import run
    from app.config import INSTANCES_CONFIG_PATH, InstancesConfig

    try:
        instances_yaml = InstancesConfig.load(INSTANCES_CONFIG_PATH)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc
    setup_logging(instances_yaml.data_dir)
    try:
        run(instances_yaml=instances_yaml)
    except (ValueError, OSError) as exc:
        raise click.UsageError(str(exc)) from exc


if __name__ == "__main__":  # pragma: no cover
    cli()
