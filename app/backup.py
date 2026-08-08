from __future__ import annotations

"""Wallet backup logic.

Entry point: python -m app.backup <mode> [param]

Modes:
  auto              Smart daily mode. Backs up current + previous month.
                    In February, also generates yearly for the previous year
                    (idempotent: skipped if the file already exists).
  monthly [YYYY-MM] Explicit monthly backup. Default: previous calendar month.
                    Always executes regardless of existing files.
  yearly  [YYYY]    Explicit yearly backup. Default: previous calendar year.
                    Always executes; removes covered monthly files afterwards.
"""

import calendar
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import Config
from app.notifier import Notifier
from app.wallet_client import WalletClient

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _month_range(year: int, month: int) -> tuple[str, str]:
    """Return (YYYY-MM-DD, YYYY-MM-DD) for the first and last day of a month."""
    last_day = calendar.monthrange(year, month)[1]
    return (
        date(year, month, 1).isoformat(),
        date(year, month, last_day).isoformat(),
    )


def _year_range(year: int) -> tuple[str, str]:
    return date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()


def _previous_month(today: date) -> tuple[int, int]:
    """Return (year, month) of the month before today."""
    if today.month == 1:
        return today.year - 1, 12
    return today.year, today.month - 1


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------

def _monthly_path(data_dir: Path, year: int, month: int) -> Path:
    return data_dir / "backups" / "monthly" / f"wallet-monthly-{year:04d}-{month:02d}.json"


def _yearly_path(data_dir: Path, year: int) -> Path:
    return data_dir / "backups" / "yearly" / f"wallet-yearly-{year:04d}.json"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    log.info("Written %s", path)


def _payload_counts(payload: dict) -> dict[str, int]:
    """Extract resource counts from a snapshot payload."""
    return {
        "records": len(payload["records"]),
        "accounts": len(payload["accounts"]),
        "categories": len(payload["categories"]),
        "budgets": len(payload["budgets"]),
        "labels": len(payload["labels"]),
    }


# ---------------------------------------------------------------------------
# Core fetch
# ---------------------------------------------------------------------------

def _fetch_snapshot(
    client: WalletClient,
    date_from: str,
    date_to: str,
    mode: str,
) -> dict:
    """Fetch all resources for the given period and return the backup payload."""
    log.info("Fetching snapshot mode=%s %s → %s", mode, date_from, date_to)
    accounts = client.get_accounts()
    categories = client.get_categories()
    budgets = client.get_budgets()
    labels = client.get_labels()
    records = client.get_records(date_from, date_to)

    return {
        "mode": mode,
        "date_from": date_from,
        "date_to": date_to,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "accounts": accounts,
        "categories": categories,
        "budgets": budgets,
        "labels": labels,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Individual backup operations
# ---------------------------------------------------------------------------

def run_monthly(
    client: WalletClient,
    notifier: Notifier,
    data_dir: Path,
    year: int,
    month: int,
) -> dict:
    """Generate (or overwrite) a monthly backup. Returns counts dict."""
    date_from, date_to = _month_range(year, month)
    payload = _fetch_snapshot(client, date_from, date_to, "monthly")
    _write_json(_monthly_path(data_dir, year, month), payload)

    counts = _payload_counts(payload)
    notifier.backup_complete(
        mode="monthly",
        period=f"{year:04d}-{month:02d}",
        date_from=date_from,
        date_to=date_to,
        counts=counts,
    )
    return counts


def run_yearly(
    client: WalletClient,
    notifier: Notifier,
    data_dir: Path,
    year: int,
) -> dict:
    """Generate (or overwrite) a yearly backup and clean up covered monthly files."""
    date_from, date_to = _year_range(year)
    payload = _fetch_snapshot(client, date_from, date_to, "yearly")
    _write_json(_yearly_path(data_dir, year), payload)

    # Remove monthly files covered by this year
    removed = 0
    for month in range(1, 13):
        p = _monthly_path(data_dir, year, month)
        if p.exists():
            p.unlink()
            log.info("Removed covered monthly backup %s", p)
            removed += 1

    counts = _payload_counts(payload)
    counts["monthly_removed"] = removed
    notifier.backup_complete(
        mode="yearly",
        period=str(year),
        date_from=date_from,
        date_to=date_to,
        counts=counts,
    )
    return counts


# ---------------------------------------------------------------------------
# Auto mode
# ---------------------------------------------------------------------------

def run_auto(
    client: WalletClient,
    notifier: Notifier,
    data_dir: Path,
    today: date | None = None,
) -> None:
    """Smart daily backup.

    Always:
      - Overwrites backup of current month (partial, fresh data)
      - Overwrites backup of previous month

    Additionally, when current month is February (previous month = January,
    i.e. last month of the previous year):
      - Generates yearly backup for the previous year if it does not yet exist
      - Cleans up the covered monthly files
    """
    if today is None:
        today = datetime.now(timezone.utc).date()

    cur_year, cur_month = today.year, today.month
    prev_year, prev_month = _previous_month(today)

    log.info("auto: backing up current month %04d-%02d", cur_year, cur_month)
    run_monthly(client, notifier, data_dir, cur_year, cur_month)

    log.info("auto: backing up previous month %04d-%02d", prev_year, prev_month)
    run_monthly(client, notifier, data_dir, prev_year, prev_month)

    # February trigger: previous month was January → last month of last year
    if cur_month == 2:
        yearly_year = cur_year - 1  # the year that just fully completed
        yearly_file = _yearly_path(data_dir, yearly_year)
        if yearly_file.exists():
            log.info("auto: yearly backup for %d already exists, skipping", yearly_year)
        else:
            log.info("auto: generating yearly backup for %d", yearly_year)
            run_yearly(client, notifier, data_dir, yearly_year)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _parse_monthly_param(argv: list[str]) -> tuple[int, int]:
    if len(argv) >= 2:
        try:
            parsed = datetime.strptime(argv[1], "%Y-%m")  # noqa: DTZ007 — only year/month needed, no tz
            return parsed.year, parsed.month
        except ValueError:
            print(f"Invalid YYYY-MM param: {argv[1]!r}", file=sys.stderr)
            sys.exit(1)
    return _previous_month(datetime.now(timezone.utc).date())


def _parse_yearly_param(argv: list[str]) -> int:
    if len(argv) >= 2:
        try:
            return int(argv[1])
        except ValueError:
            print(f"Invalid YYYY param: {argv[1]!r}", file=sys.stderr)
            sys.exit(1)
    return datetime.now(timezone.utc).year - 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        print("Usage: python -m app.backup <mode> [param]", file=sys.stderr)
        print("  mode: auto | monthly [YYYY-MM] | yearly [YYYY]", file=sys.stderr)
        sys.exit(1)

    mode = argv[0]

    from app.logging_setup import configure_logging  # avoid circular at module level
    configure_logging()

    cfg = Config.from_env()
    client = WalletClient(api_key=cfg.wallet_api_key)
    notifier = Notifier(
        bot_token=cfg.telegram_bot_token,
        chat_id=cfg.telegram_chat_id,
        owner_name=cfg.owner_name,
    )

    if mode == "auto":
        run_auto(client, notifier, cfg.data_dir)
    elif mode == "monthly":
        year, month = _parse_monthly_param(argv)
        run_monthly(client, notifier, cfg.data_dir, year, month)
    elif mode == "yearly":
        year = _parse_yearly_param(argv)
        run_yearly(client, notifier, cfg.data_dir, year)
    else:
        print(f"Unknown mode: {mode!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
