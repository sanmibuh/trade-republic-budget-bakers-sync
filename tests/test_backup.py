from __future__ import annotations

import json
from datetime import UTC, date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.backup import (
    _month_range,
    _parse_monthly_param,
    _parse_yearly_param,
    _previous_month,
    _write_json,
    _year_range,
    run_auto,
    run_monthly,
    run_yearly,
)

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


def test_month_range_regular():
    assert _month_range(2026, 7) == ("2026-07-01", "2026-07-31")


def test_month_range_february_non_leap():
    assert _month_range(2025, 2) == ("2025-02-01", "2025-02-28")


def test_month_range_february_leap():
    assert _month_range(2024, 2) == ("2024-02-01", "2024-02-29")


def test_year_range():
    assert _year_range(2025) == ("2025-01-01", "2025-12-31")


def test_previous_month_mid_year():
    assert _previous_month(date(2026, 8, 15)) == (2026, 7)


def test_previous_month_january_wraps():
    assert _previous_month(date(2026, 1, 10)) == (2025, 12)


# ---------------------------------------------------------------------------
# _parse_monthly_param
# ---------------------------------------------------------------------------


def test_parse_monthly_param_none_returns_previous_month():
    year, month = _parse_monthly_param(None)
    assert isinstance(year, int)
    assert 1 <= month <= 12


def test_parse_monthly_param_valid_string():
    assert _parse_monthly_param("2026-03") == (2026, 3)


def test_parse_monthly_param_invalid_raises():
    with pytest.raises(ValueError):
        _parse_monthly_param("not-a-date")


def test_parse_monthly_param_wrong_format_raises():
    with pytest.raises(ValueError):
        _parse_monthly_param("2026/03")


# ---------------------------------------------------------------------------
# _parse_yearly_param
# ---------------------------------------------------------------------------


def test_parse_yearly_param_none_returns_previous_year():
    from datetime import datetime

    year = _parse_yearly_param(None)
    assert year == datetime.now(UTC).year - 1


def test_parse_yearly_param_valid_string():
    assert _parse_yearly_param("2025") == 2025


def test_parse_yearly_param_invalid_raises():
    with pytest.raises(ValueError):
        _parse_yearly_param("not-a-year")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(**overrides):
    client = MagicMock()
    client.get_accounts.return_value = overrides.get("accounts", [{"id": "a1"}])
    client.get_categories.return_value = overrides.get(
        "categories", [{"id": "c1"}, {"id": "c2"}]
    )
    client.get_budgets.return_value = overrides.get("budgets", [])
    client.get_labels.return_value = overrides.get("labels", [{"id": "l1"}])
    client.get_records.return_value = overrides.get(
        "records", [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}]
    )
    return client


def _make_notifier():
    notifier = MagicMock()
    notifier.backup_complete.return_value = True
    return notifier


# ---------------------------------------------------------------------------
# run_monthly
# ---------------------------------------------------------------------------


def test_run_monthly_creates_file(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    counts = run_monthly(client, notifier, tmp_path, 2026, 7)

    out = tmp_path / "backups" / "monthly" / "wallet-monthly-2026-07.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["mode"] == "monthly"
    assert payload["date_from"] == "2026-07-01"
    assert payload["date_to"] == "2026-07-31"
    assert len(payload["records"]) == 3
    assert counts["records"] == 3
    assert counts["accounts"] == 1


def test_run_monthly_overwrites_existing(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_monthly(client, notifier, tmp_path, 2026, 7)

    # Second call with different data
    client.get_records.return_value = [{"id": "new"}]
    run_monthly(client, notifier, tmp_path, 2026, 7)

    out = tmp_path / "backups" / "monthly" / "wallet-monthly-2026-07.json"
    payload = json.loads(out.read_text())
    assert len(payload["records"]) == 1


def test_run_monthly_notifies(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_monthly(client, notifier, tmp_path, 2026, 7)

    notifier.backup_complete.assert_called_once()
    kwargs = notifier.backup_complete.call_args.kwargs
    assert kwargs["mode"] == "monthly"
    assert kwargs["period"] == "2026-07"
    assert kwargs["date_from"] == "2026-07-01"
    assert kwargs["date_to"] == "2026-07-31"


def test_run_monthly_passes_date_range_to_client(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_monthly(client, notifier, tmp_path, 2026, 3)

    client.get_records.assert_called_once_with("2026-03-01", "2026-03-31")


# ---------------------------------------------------------------------------
# run_yearly
# ---------------------------------------------------------------------------


def test_run_yearly_creates_file(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_yearly(client, notifier, tmp_path, 2025)

    out = tmp_path / "backups" / "yearly" / "wallet-yearly-2025.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload["mode"] == "yearly"
    assert payload["date_from"] == "2025-01-01"
    assert payload["date_to"] == "2025-12-31"


def test_run_yearly_removes_covered_monthly_files(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    # Pre-create some monthly files for 2025
    monthly_dir = tmp_path / "backups" / "monthly"
    monthly_dir.mkdir(parents=True)
    for m in [1, 6, 12]:
        (monthly_dir / f"wallet-monthly-2025-{m:02d}.json").write_text("{}")
    # A file from a different year should NOT be removed
    (monthly_dir / "wallet-monthly-2024-12.json").write_text("{}")

    run_yearly(client, notifier, tmp_path, 2025)

    assert not (monthly_dir / "wallet-monthly-2025-01.json").exists()
    assert not (monthly_dir / "wallet-monthly-2025-06.json").exists()
    assert not (monthly_dir / "wallet-monthly-2025-12.json").exists()
    assert (monthly_dir / "wallet-monthly-2024-12.json").exists()


def test_run_yearly_reports_monthly_removed_count(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    monthly_dir = tmp_path / "backups" / "monthly"
    monthly_dir.mkdir(parents=True)
    for m in [3, 9]:
        (monthly_dir / f"wallet-monthly-2025-{m:02d}.json").write_text("{}")

    run_yearly(client, notifier, tmp_path, 2025)

    kwargs = notifier.backup_complete.call_args.kwargs
    assert kwargs["counts"]["monthly_removed"] == 2


def test_run_yearly_notifies(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_yearly(client, notifier, tmp_path, 2025)

    notifier.backup_complete.assert_called_once()
    kwargs = notifier.backup_complete.call_args.kwargs
    assert kwargs["mode"] == "yearly"
    assert kwargs["period"] == "2025"
    assert kwargs["date_from"] == "2025-01-01"
    assert kwargs["date_to"] == "2025-12-31"


# ---------------------------------------------------------------------------
# run_auto — normal month (not February)
# ---------------------------------------------------------------------------


def test_run_auto_backs_up_current_and_previous_month(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_auto(client, notifier, tmp_path, today=date(2026, 8, 10))

    assert (tmp_path / "backups" / "monthly" / "wallet-monthly-2026-08.json").exists()
    assert (tmp_path / "backups" / "monthly" / "wallet-monthly-2026-07.json").exists()


def test_run_auto_no_yearly_outside_february(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_auto(client, notifier, tmp_path, today=date(2026, 8, 10))

    yearly_dir = tmp_path / "backups" / "yearly"
    assert not yearly_dir.exists() or not any(yearly_dir.iterdir())


def test_run_auto_january_wraps_previous_month(tmp_path):
    """Running in January: previous month is December of previous year."""
    client = _make_client()
    notifier = _make_notifier()

    run_auto(client, notifier, tmp_path, today=date(2026, 1, 5))

    assert (tmp_path / "backups" / "monthly" / "wallet-monthly-2026-01.json").exists()
    assert (tmp_path / "backups" / "monthly" / "wallet-monthly-2025-12.json").exists()
    # Not February → no yearly
    yearly_dir = tmp_path / "backups" / "yearly"
    assert not yearly_dir.exists() or not any(yearly_dir.iterdir())


# ---------------------------------------------------------------------------
# run_auto — February trigger for yearly
# ---------------------------------------------------------------------------


def test_run_auto_february_generates_yearly_when_missing(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    run_auto(client, notifier, tmp_path, today=date(2026, 2, 3))

    # Monthly backups for Feb and Jan
    assert (tmp_path / "backups" / "monthly" / "wallet-monthly-2026-02.json").exists()
    assert (tmp_path / "backups" / "monthly" / "wallet-monthly-2026-01.json").exists()
    # Yearly for 2025 generated
    assert (tmp_path / "backups" / "yearly" / "wallet-yearly-2025.json").exists()


def test_run_auto_february_skips_yearly_when_already_exists(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    # Pre-create the yearly file
    yearly_dir = tmp_path / "backups" / "yearly"
    yearly_dir.mkdir(parents=True)
    (yearly_dir / "wallet-yearly-2025.json").write_text('{"mode":"yearly"}')

    run_auto(client, notifier, tmp_path, today=date(2026, 2, 15))

    # get_records called only twice (current + previous month), not a third time for yearly
    assert client.get_records.call_count == 2


def test_run_auto_february_cleans_up_monthly_files(tmp_path):
    client = _make_client()
    notifier = _make_notifier()

    monthly_dir = tmp_path / "backups" / "monthly"
    monthly_dir.mkdir(parents=True)
    for m in range(1, 13):
        (monthly_dir / f"wallet-monthly-2025-{m:02d}.json").write_text("{}")

    run_auto(client, notifier, tmp_path, today=date(2026, 2, 1))

    for m in range(1, 13):
        assert not (monthly_dir / f"wallet-monthly-2025-{m:02d}.json").exists()


def test_run_auto_february_yearly_idempotent(tmp_path):
    """Calling run_auto twice in February should not regenerate yearly the second time."""
    client = _make_client()
    notifier = _make_notifier()

    run_auto(client, notifier, tmp_path, today=date(2026, 2, 5))
    first_call_count = client.get_records.call_count  # 3: feb + jan + yearly

    run_auto(client, notifier, tmp_path, today=date(2026, 2, 6))
    second_call_count = client.get_records.call_count

    # Second run: only 2 more calls (feb + jan), not yearly again
    assert second_call_count - first_call_count == 2


# ---------------------------------------------------------------------------
# _write_json — atomic write
# ---------------------------------------------------------------------------


def test_write_json_creates_file_with_correct_content(tmp_path):
    path = tmp_path / "sub" / "out.json"
    payload = {"foo": "bar", "nums": [1, 2, 3]}

    _write_json(path, payload)

    assert path.exists()
    assert json.loads(path.read_text()) == payload


def test_write_json_no_tmp_file_remains(tmp_path):
    path = tmp_path / "out.json"
    _write_json(path, {"x": 1})

    assert not any(tmp_path.glob("*.tmp")), (
        "no .tmp files should remain after successful write"
    )


def test_write_json_uses_unique_tmp_then_rename(tmp_path, monkeypatch):
    """Atomic write must use a unique tmp file (not a fixed .tmp name) and rename it."""
    path = tmp_path / "out.json"
    replaced_sources: list[str] = []

    original_replace = Path.replace

    def capturing_replace(self: Path, target: Path) -> Path:
        replaced_sources.append(self.name)
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", capturing_replace)
    _write_json(path, {"v": 1})

    assert any(".tmp" in src for src in replaced_sources), (
        "_write_json must rename a .tmp file into the final path"
    )
    # The tmp name must NOT be simply "out.tmp" — it must be unique
    assert not any(src == "out.tmp" for src in replaced_sources), (
        "_write_json must use a unique tmp filename, not a fixed 'out.tmp'"
    )
    assert json.loads(path.read_text()) == {"v": 1}


def test_write_json_atomic_preserves_original_when_rename_fails(tmp_path, monkeypatch):
    """If rename fails after tmp write, the pre-existing file must remain intact
    and all .tmp files must be cleaned up."""
    path = tmp_path / "out.json"
    original = {"original": True}
    path.write_text(json.dumps(original))

    def failing_replace(self: Path, _target: Path) -> Path:
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "replace", failing_replace)

    with pytest.raises(OSError):
        _write_json(path, {"new": True})

    assert json.loads(path.read_text()) == original, (
        "original file must be untouched when rename fails"
    )
    assert not any(tmp_path.glob("*.tmp")), (
        ".tmp files must be cleaned up after rename failure"
    )


def test_write_json_overwrites_existing_file(tmp_path):
    path = tmp_path / "out.json"
    _write_json(path, {"v": 1})
    _write_json(path, {"v": 2})

    assert json.loads(path.read_text()) == {"v": 2}


# ---------------------------------------------------------------------------
# run_auto — today defaults to current date
# ---------------------------------------------------------------------------


def test_run_auto_uses_current_date_when_today_not_provided(tmp_path, monkeypatch):
    """run_auto with no today argument must fall back to datetime.now(UTC).date()."""
    from datetime import datetime

    fixed_date = date(2026, 8, 13)

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 13, 12, 0, 0, tzinfo=UTC)

    monkeypatch.setattr("app.backup.datetime", _FakeDatetime)

    client = _make_client()
    notifier = _make_notifier()

    run_auto(client, notifier, tmp_path)

    year_str = str(fixed_date.year)
    month_str = f"{fixed_date.month:02d}"
    monthly_dir = tmp_path / "backups" / "monthly"
    assert any(f"{year_str}-{month_str}" in f.name for f in monthly_dir.iterdir())
