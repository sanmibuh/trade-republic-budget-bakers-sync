"""Inline keyboard builder functions for the Telegram bot.

Each function returns a ``list[list[dict]]`` suitable for Telegram's
``inline_keyboard`` reply markup field.  Functions are stateless and accept
only the data they need — no dependency on ``TelegramBot`` or Docker.
"""

from __future__ import annotations

import datetime

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_YEAR_BUTTON_COUNT = 3  # number of recent years in the yearly backup keyboard
_MONTH_BUTTON_COUNT = 4  # number of recent months in the monthly backup keyboard
_RESYNC_DAY_COUNT = 7  # number of recent days in the resync date picker

# Separator used inside callback_data.
# Must not appear in instance names or period params (YYYY-MM / YYYY are digits/hyphens).
_CB_SEP = ":"

# Icons used in backup ACK messages — kept in sync with backup_type_buttons().
BACKUP_ICONS: dict[str, str] = {
    "monthly": "📅",
    "yearly": "📆",
}


# ---------------------------------------------------------------------------
# Backup keyboards
# ---------------------------------------------------------------------------


def backup_type_buttons() -> list[list[dict]]:
    """Inline keyboard with Monthly and Yearly type-selection buttons."""
    return [
        [
            {"text": "📅 Monthly", "callback_data": f"backup_type{_CB_SEP}monthly"},
            {"text": "📆 Yearly", "callback_data": f"backup_type{_CB_SEP}yearly"},
        ]
    ]


def year_buttons(count: int = _YEAR_BUTTON_COUNT) -> list[list[dict]]:
    """Inline keyboard with the most recent years (previous year first)."""
    current_year = datetime.datetime.now(tz=datetime.UTC).year
    years = [current_year - i for i in range(1, count + 1)]
    buttons = [
        {"text": str(y), "callback_data": f"backup_yearly{_CB_SEP}{y}"} for y in years
    ]
    return [buttons]


def month_buttons(count: int = _MONTH_BUTTON_COUNT) -> list[list[dict]]:
    """Inline keyboard with the most recent months (previous month first)."""
    today = datetime.datetime.now(tz=datetime.UTC).date()
    months: list[str] = []
    year, month = today.year, today.month
    for _ in range(count):
        month -= 1
        if month == 0:
            month = 12
            year -= 1
        months.append(f"{year}-{month:02d}")
    buttons = [
        {"text": m, "callback_data": f"backup_monthly{_CB_SEP}{m}"} for m in months
    ]
    return [buttons]


# ---------------------------------------------------------------------------
# Instance pickers
# ---------------------------------------------------------------------------


def instance_buttons(cmd: str, names: list[str]) -> list[list[dict]]:
    """Build an inline keyboard with one button per instance name.

    Args:
        cmd:   The callback command prefix (e.g. ``"sync"``, ``"login"``).
        names: Ordered list of instance display names (case-preserved).

    Returns:
        Rows of at most 3 buttons each.
    """
    buttons = [
        {"text": name, "callback_data": f"{cmd}{_CB_SEP}{name.lower()}"}
        for name in names
    ]
    if not buttons:
        return [[]]
    return [buttons[i : i + 3] for i in range(0, len(buttons), 3)]


def instance_buttons_for_resync(date_str: str, names: list[str]) -> list[list[dict]]:
    """Build an instance-picker keyboard that encodes *date_str* in the callback.

    Args:
        date_str: ISO date string (``YYYY-MM-DD``).
        names:    Ordered list of instance display names.

    Returns:
        Rows of at most 3 buttons each.
    """
    buttons = [
        {
            "text": name,
            "callback_data": f"resync{_CB_SEP}{date_str}{_CB_SEP}{name.lower()}",
        }
        for name in names
    ]
    if not buttons:
        return [[]]
    return [buttons[i : i + 3] for i in range(0, len(buttons), 3)]


def resync_date_buttons(
    instance_key: str, count: int = _RESYNC_DAY_COUNT
) -> list[list[dict]]:
    """Build a date-picker keyboard with the most recent days (yesterday first).

    Args:
        instance_key: Lower-case instance name encoded in the callback data.
        count:        Number of days to offer (default: ``_RESYNC_DAY_COUNT``).

    Returns:
        Rows of at most 3 buttons each.
    """
    today = datetime.datetime.now(tz=datetime.UTC).date()
    days = [str(today - datetime.timedelta(days=i)) for i in range(1, count + 1)]
    buttons = [
        {
            "text": d,
            "callback_data": f"resync{_CB_SEP}{d}{_CB_SEP}{instance_key}",
        }
        for d in days
    ]
    return [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
