"""app CLI entry point.

Usage:
    python -m app                          # shows help
    python -m app sync                     # one-shot TR → Wallet sync
    python -m app backup auto              # smart daily backup
    python -m app backup monthly           # backup previous month
    python -m app backup monthly 2026-07   # backup specific month
    python -m app backup yearly            # backup previous year
    python -m app backup yearly 2025       # backup specific year
"""
from __future__ import annotations

import sys

import click

from app.logging_setup import configure_logging


@click.group()
def cli() -> None:
    """Trade Republic → BudgetBakers Wallet sync and backup tool."""


@cli.command()
def sync() -> None:
    """Run a one-shot Trade Republic → Wallet sync."""
    from app.main import run

    configure_logging()
    sys.exit(run())


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
    from app.notifier import Notifier
    from app.wallet_client import WalletClient

    configure_logging()
    cfg = BackupConfig.from_env()
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
            raise click.BadParameter(f"Expected YYYY-MM, got {param!r}", param_hint="PARAM") from None
        run_monthly(client, notifier, cfg.data_dir, year, month)
    elif mode == "yearly":
        try:
            year = _parse_yearly_param(param)
        except ValueError:
            raise click.BadParameter(f"Expected YYYY, got {param!r}", param_hint="PARAM") from None
        run_yearly(client, notifier, cfg.data_dir, year)


@cli.command()
def bot() -> None:
    """Start the Telegram bot for remote command execution.

    Listens for commands from the authorized Telegram chat and dispatches
    them to the configured Docker containers via the Docker SDK.

    \b
    Required environment variables:
      TELEGRAM_BOT_TOKEN    Bot token from BotFather.
      TELEGRAM_CHAT_ID      Authorized chat ID — only this chat can issue commands.
      INSTANCES             Comma-separated list of sync instance names (e.g. "david,eli").
      CONTAINER_PREFIX      Docker project name (set via `name:` in docker-compose.yml).
      BACKUP_SERVICE        Optional. Backup service name (default: "backup"). Set to empty to disable.

    \b
    Container naming convention:
      Sync:   {CONTAINER_PREFIX}-sync-{instance}-1   e.g. tr-sync-sync-david-1
      Backup: {CONTAINER_PREFIX}-{BACKUP_SERVICE}-1  e.g. tr-sync-backup-1

    \b
    Supported Telegram commands:
      /help
      /status
      /sync              (shows an inline keyboard to pick the instance)
      /backup_monthly    [YYYY-MM]
      /backup_yearly     [YYYY]
    """
    from app.bot import run
    from app.logging_setup import configure_logging

    configure_logging()
    run()


if __name__ == "__main__":  # pragma: no cover
    cli()
