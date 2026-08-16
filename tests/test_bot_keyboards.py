"""Tests for app.bot_keyboards — standalone inline keyboard builder functions."""

from __future__ import annotations

import datetime
from unittest.mock import patch

from app.bot_keyboards import (
    _CB_SEP,
    _MONTH_BUTTON_COUNT,
    _RESYNC_DAY_COUNT,
    _YEAR_BUTTON_COUNT,
    backup_type_buttons,
    instance_buttons,
    instance_buttons_for_resync,
    month_buttons,
    resync_date_buttons,
    year_buttons,
)

# ---------------------------------------------------------------------------
# backup_type_buttons
# ---------------------------------------------------------------------------


def test_backup_type_buttons_has_monthly_and_yearly():
    rows = backup_type_buttons()
    all_buttons = [b for row in rows for b in row]
    cb_data = [b["callback_data"] for b in all_buttons]
    assert any(f"backup_type{_CB_SEP}monthly" in d for d in cb_data)
    assert any(f"backup_type{_CB_SEP}yearly" in d for d in cb_data)


def test_backup_type_buttons_returns_nested_list():
    rows = backup_type_buttons()
    assert isinstance(rows, list)
    assert all(isinstance(row, list) for row in rows)


# ---------------------------------------------------------------------------
# year_buttons
# ---------------------------------------------------------------------------


def test_year_buttons_default_count():
    fixed = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = year_buttons()
    all_buttons = [b for row in rows for b in row]
    assert len(all_buttons) == _YEAR_BUTTON_COUNT


def test_year_buttons_excludes_current_year():
    """Year buttons should start from previous year (not include current)."""
    fixed = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = year_buttons()
    texts = [b["text"] for row in rows for b in row]
    assert "2026" not in texts
    assert "2025" in texts


def test_year_buttons_callback_data_prefix():
    fixed = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = year_buttons()
    for row in rows:
        for b in row:
            assert b["callback_data"].startswith(f"backup_yearly{_CB_SEP}")


def test_year_buttons_custom_count():
    fixed = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = year_buttons(count=5)
    all_buttons = [b for row in rows for b in row]
    assert len(all_buttons) == 5


# ---------------------------------------------------------------------------
# month_buttons
# ---------------------------------------------------------------------------


def test_month_buttons_default_count():
    fixed = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = month_buttons()
    all_buttons = [b for row in rows for b in row]
    assert len(all_buttons) == _MONTH_BUTTON_COUNT


def test_month_buttons_wraps_year_when_run_in_january():
    fixed = datetime.datetime(2026, 1, 15, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = month_buttons()
    texts = [b["text"] for row in rows for b in row]
    assert "2025-12" in texts


def test_month_buttons_callback_data_prefix():
    fixed = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = month_buttons()
    for row in rows:
        for b in row:
            assert b["callback_data"].startswith(f"backup_monthly{_CB_SEP}")


def test_month_buttons_previous_months_only():
    """Month buttons start from the previous month, not the current one."""
    fixed = datetime.datetime(2026, 8, 15, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = month_buttons()
    texts = [b["text"] for row in rows for b in row]
    assert "2026-07" in texts
    assert "2026-08" not in texts


# ---------------------------------------------------------------------------
# instance_buttons
# ---------------------------------------------------------------------------


def test_instance_buttons_one_button_per_name():
    rows = instance_buttons("sync", ["David", "Eli"])
    all_buttons = [b for row in rows for b in row]
    assert len(all_buttons) == 2
    labels = [b["text"] for b in all_buttons]
    assert "David" in labels
    assert "Eli" in labels


def test_instance_buttons_callback_data_encodes_cmd_and_lowercase_name():
    rows = instance_buttons("sync", ["David", "Eli"])
    all_buttons = [b for row in rows for b in row]
    data = {b["text"]: b["callback_data"] for b in all_buttons}
    assert data["David"] == f"sync{_CB_SEP}david"
    assert data["Eli"] == f"sync{_CB_SEP}eli"


def test_instance_buttons_rows_split_at_three():
    names = [str(i) for i in range(5)]
    rows = instance_buttons("sync", names)
    assert all(len(row) <= 3 for row in rows)
    assert sum(len(row) for row in rows) == 5


def test_instance_buttons_empty_instances():
    rows = instance_buttons("sync", [])
    assert rows == [[]] or rows == []


# ---------------------------------------------------------------------------
# instance_buttons_for_resync
# ---------------------------------------------------------------------------


def test_instance_buttons_for_resync_encodes_date_and_instance():
    rows = instance_buttons_for_resync("2026-07-15", ["David", "Eli"])
    all_buttons = [b for row in rows for b in row]
    for btn in all_buttons:
        assert "2026-07-15" in btn["callback_data"]
        assert btn["callback_data"].startswith(f"resync{_CB_SEP}")


def test_instance_buttons_for_resync_label_is_instance_name():
    rows = instance_buttons_for_resync("2026-07-15", ["David"])
    all_buttons = [b for row in rows for b in row]
    assert all_buttons[0]["text"] == "David"


# ---------------------------------------------------------------------------
# resync_date_buttons
# ---------------------------------------------------------------------------


def test_resync_date_buttons_default_count():
    fixed = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = resync_date_buttons("david")
    all_buttons = [b for row in rows for b in row]
    assert len(all_buttons) == _RESYNC_DAY_COUNT


def test_resync_date_buttons_starts_from_yesterday():
    fixed = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = resync_date_buttons("david")
    texts = [b["text"] for row in rows for b in row]
    assert "2026-07-14" in texts
    assert "2026-07-15" not in texts


def test_resync_date_buttons_encode_instance():
    fixed = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)
    with patch("app.bot_keyboards.datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fixed
        rows = resync_date_buttons("david")
    for row in rows:
        for btn in row:
            assert "david" in btn["callback_data"]
            assert btn["callback_data"].startswith(f"resync{_CB_SEP}")
