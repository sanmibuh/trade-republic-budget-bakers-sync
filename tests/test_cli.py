from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from app.__main__ import cli


def _runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

def test_help_shows_commands():
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "sync" in result.output
    assert "backup" in result.output


def test_sync_help():
    result = _runner().invoke(cli, ["sync", "--help"])
    assert result.exit_code == 0
    assert "sync" in result.output.lower()


def test_backup_help():
    result = _runner().invoke(cli, ["backup", "--help"])
    assert result.exit_code == 0
    assert "auto" in result.output
    assert "monthly" in result.output
    assert "yearly" in result.output


# ---------------------------------------------------------------------------
# sync command
# ---------------------------------------------------------------------------

def test_sync_calls_run():
    with (
        patch("app.main.run", return_value=0) as mock_run,
        patch("app.__main__.configure_logging"),
    ):
        result = _runner().invoke(cli, ["sync"])
    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_sync_exits_with_run_return_code():
    with (
        patch("app.main.run", return_value=1),
        patch("app.__main__.configure_logging"),
    ):
        result = _runner().invoke(cli, ["sync"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# backup command — mode validation
# ---------------------------------------------------------------------------

def test_backup_invalid_mode_rejected():
    result = _runner().invoke(cli, ["backup", "weekly"])
    assert result.exit_code != 0
    assert "invalid choice" in result.output.lower() or "Error" in result.output


def test_backup_no_mode_shows_help():
    result = _runner().invoke(cli, ["backup"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _mock_cfg(tmp_path):
    cfg = MagicMock()
    cfg.wallet_api_key = "key"
    cfg.telegram_bot_token = None
    cfg.telegram_chat_id = None
    cfg.owner_name = "Test"
    cfg.data_dir = tmp_path
    return cfg


# ---------------------------------------------------------------------------
# backup auto
# ---------------------------------------------------------------------------

def test_backup_auto_calls_run_auto(tmp_path):
    with (
        patch("app.__main__.configure_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_auto") as mock_auto,
    ):
        result = _runner().invoke(cli, ["backup", "auto"])
    assert result.exit_code == 0
    mock_auto.assert_called_once()


# ---------------------------------------------------------------------------
# backup monthly
# ---------------------------------------------------------------------------

def test_backup_monthly_default_calls_run_monthly(tmp_path):
    with (
        patch("app.__main__.configure_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_monthly") as mock_monthly,
    ):
        result = _runner().invoke(cli, ["backup", "monthly"])
    assert result.exit_code == 0
    mock_monthly.assert_called_once()


def test_backup_monthly_with_param(tmp_path):
    with (
        patch("app.__main__.configure_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_monthly") as mock_monthly,
    ):
        result = _runner().invoke(cli, ["backup", "monthly", "2026-07"])
    assert result.exit_code == 0
    assert mock_monthly.call_args.args[3] == 2026  # year
    assert mock_monthly.call_args.args[4] == 7     # month


def test_backup_monthly_invalid_param_exits(tmp_path):
    with (
        patch("app.__main__.configure_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
    ):
        result = _runner().invoke(cli, ["backup", "monthly", "not-a-date"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# backup yearly
# ---------------------------------------------------------------------------

def test_backup_yearly_default_calls_run_yearly(tmp_path):
    with (
        patch("app.__main__.configure_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_yearly") as mock_yearly,
    ):
        result = _runner().invoke(cli, ["backup", "yearly"])
    assert result.exit_code == 0
    mock_yearly.assert_called_once()


def test_backup_yearly_with_param(tmp_path):
    with (
        patch("app.__main__.configure_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_yearly") as mock_yearly,
    ):
        result = _runner().invoke(cli, ["backup", "yearly", "2025"])
    assert result.exit_code == 0
    assert mock_yearly.call_args.args[3] == 2025


def test_backup_yearly_invalid_param_exits(tmp_path):
    with (
        patch("app.__main__.configure_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
    ):
        result = _runner().invoke(cli, ["backup", "yearly", "not-a-year"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# bot command
# ---------------------------------------------------------------------------

def test_bot_help():
    result = _runner().invoke(cli, ["bot", "--help"])
    assert result.exit_code == 0
    assert "bot" in result.output.lower()


def test_bot_calls_run():
    with (
        patch("app.bot.run") as mock_run,
        patch("app.__main__.configure_logging"),
    ):
        _runner().invoke(cli, ["bot"])
    mock_run.assert_called_once()
