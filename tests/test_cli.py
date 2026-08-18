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
    with patch("app.main.run", return_value=0) as mock_run:
        result = _runner().invoke(cli, ["sync"])
    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_sync_exits_with_run_return_code():
    with patch("app.main.run", return_value=1):
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
        patch("app.__main__.setup_logging") as mock_setup_log,
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_auto") as mock_auto,
    ):
        result = _runner().invoke(cli, ["backup", "auto"])
    assert result.exit_code == 0
    mock_auto.assert_called_once()
    mock_setup_log.assert_called_once_with(tmp_path)


# ---------------------------------------------------------------------------
# backup monthly
# ---------------------------------------------------------------------------


def test_backup_monthly_default_calls_run_monthly(tmp_path):
    with (
        patch("app.__main__.setup_logging"),
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
        patch("app.__main__.setup_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_monthly") as mock_monthly,
    ):
        result = _runner().invoke(cli, ["backup", "monthly", "2026-07"])
    assert result.exit_code == 0
    assert mock_monthly.call_args.args[3] == 2026  # year
    assert mock_monthly.call_args.args[4] == 7  # month


def test_backup_monthly_invalid_param_exits(tmp_path):
    with (
        patch("app.__main__.setup_logging"),
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
        patch("app.__main__.setup_logging"),
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
        patch("app.__main__.setup_logging"),
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
        patch("app.__main__.setup_logging"),
        patch("app.config.BackupConfig.from_env", return_value=_mock_cfg(tmp_path)),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
    ):
        result = _runner().invoke(cli, ["backup", "yearly", "not-a-year"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# login command
# ---------------------------------------------------------------------------


def test_login_help():
    result = _runner().invoke(cli, ["login", "--help"])
    assert result.exit_code == 0
    assert "login" in result.output.lower()


def test_login_calls_run_login():
    with patch("app.main.run_login", return_value=0) as mock_run:
        result = _runner().invoke(cli, ["login"])
    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_login_exits_with_return_code():
    with patch("app.main.run_login", return_value=1):
        result = _runner().invoke(cli, ["login"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# submit-code command
# ---------------------------------------------------------------------------


def test_submit_code_writes_code_file(tmp_path):
    from app.twofa import CODE_FILENAME, PENDING_FILENAME

    (tmp_path / PENDING_FILENAME).write_text("")
    with patch("app.config.Config.from_env", return_value=_mock_cfg(tmp_path)):
        result = _runner().invoke(cli, ["submit-code", "123456"])

    assert result.exit_code == 0
    assert (tmp_path / CODE_FILENAME).read_text() == "123456"


def test_submit_code_rejects_when_no_pending_marker(tmp_path):
    """submit-code must fail with a clear error when no login is waiting."""
    with patch("app.config.Config.from_env", return_value=_mock_cfg(tmp_path)):
        result = _runner().invoke(cli, ["submit-code", "123456"])

    assert result.exit_code != 0
    assert "No active login request" in result.output


def test_submit_code_pending_marker_absent_does_not_write_code_file(tmp_path):
    """When no pending marker exists the code file must NOT be written."""
    from app.twofa import CODE_FILENAME

    with patch("app.config.Config.from_env", return_value=_mock_cfg(tmp_path)):
        _runner().invoke(cli, ["submit-code", "999999"])

    assert not (tmp_path / CODE_FILENAME).exists()


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


# ---------------------------------------------------------------------------
# check-session command
# ---------------------------------------------------------------------------

_FAR_FUTURE = 9_999_999_999  # Unix timestamp well beyond any realistic date
_PAST = 1_000_000_000  # Unix timestamp in 2001 — always expired


def _netscape_cookie(name: str, value: str, expires: int) -> str:
    return (
        "# Netscape HTTP Cookie File\n"
        f".api.traderepublic.com\tTRUE\t/\tTRUE\t{expires}\t{name}\t{value}\n"
    )


def test_check_session_exits_zero_when_valid_cookie_present(tmp_path):
    """Exit 0 when cookies.txt contains at least one non-expired cookie."""
    (tmp_path / "cookies.txt").write_text(
        _netscape_cookie("tr_session", "abc", _FAR_FUTURE)
    )
    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 0


def test_check_session_exits_one_when_all_cookies_expired(tmp_path):
    """Exit 1 when cookies.txt exists but all cookies are expired."""
    (tmp_path / "cookies.txt").write_text(_netscape_cookie("tr_session", "abc", _PAST))
    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 1


def test_check_session_exits_one_when_cookies_missing(tmp_path):
    """Exit 1 when cookies.txt is absent — login required."""
    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 1


def test_check_session_exits_one_when_only_credentials_json_present(tmp_path):
    """credentials.json alone does not count — session lives in cookies.txt."""
    (tmp_path / "credentials.json").write_text("{}")
    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 1


def test_check_session_exits_one_when_cookies_file_empty(tmp_path):
    """Exit 1 when cookies.txt is empty (no cookies at all)."""
    (tmp_path / "cookies.txt").write_text("# Netscape HTTP Cookie File\n")
    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 1


def test_check_session_exits_zero_when_mixed_cookies_one_valid(tmp_path):
    """Exit 0 when cookies.txt has a mix of expired and valid cookies."""
    content = (
        "# Netscape HTTP Cookie File\n"
        f".api.traderepublic.com\tTRUE\t/\tTRUE\t{_PAST}\told_token\texpired\n"
        f".api.traderepublic.com\tTRUE\t/\tTRUE\t{_FAR_FUTURE}\ttr_session\tvalid\n"
    )
    (tmp_path / "cookies.txt").write_text(content)
    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 0


def test_check_session_help():
    result = _runner().invoke(cli, ["check-session", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# resync command
# ---------------------------------------------------------------------------


def test_resync_help():
    result = _runner().invoke(cli, ["resync", "--help"])
    assert result.exit_code == 0
    assert "resync" in result.output.lower() or "date" in result.output.lower()


def test_resync_calls_run_resync_with_date():
    with patch("app.main.run_resync", return_value=0) as mock_run:
        result = _runner().invoke(cli, ["resync", "2026-07-15"])
    assert result.exit_code == 0
    mock_run.assert_called_once_with("2026-07-15")


def test_resync_exits_with_run_resync_return_code():
    with patch("app.main.run_resync", return_value=1):
        result = _runner().invoke(cli, ["resync", "2026-07-15"])
    assert result.exit_code == 1


def test_resync_requires_date_argument():
    result = _runner().invoke(cli, ["resync"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# check-session — auth_state from DB overrides cookie check
# ---------------------------------------------------------------------------


def _write_valid_cookie(tmp_path) -> None:
    """Write a valid (non-expired) cookies.txt so the cookie check passes."""
    _FAR_FUTURE = 9_999_999_999
    (tmp_path / "cookies.txt").write_text(
        "# Netscape HTTP Cookie File\n"
        f".api.traderepublic.com\tTRUE\t/\tTRUE\t{_FAR_FUTURE}\ttr_session\tabc\n"
    )


def test_check_session_exits_one_when_auth_state_failed(tmp_path, monkeypatch):
    """Exit 1 when cookies are valid but auth_state='failed' is persisted in DB."""
    from app.persistence import EventRepository

    _write_valid_cookie(tmp_path)
    monkeypatch.setenv("INSTANCE", "david")
    db_path = tmp_path / "sync.db"
    with EventRepository(db_path) as repo:
        repo.set_auth_state("david", "failed")

    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 1


def test_check_session_exits_one_when_auth_state_expired(tmp_path, monkeypatch):
    """Exit 1 when cookies are valid but auth_state='expired' is persisted in DB."""
    from app.persistence import EventRepository

    _write_valid_cookie(tmp_path)
    monkeypatch.setenv("INSTANCE", "david")
    db_path = tmp_path / "sync.db"
    with EventRepository(db_path) as repo:
        repo.set_auth_state("david", "expired")

    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 1


def test_check_session_exits_zero_when_auth_state_ok(tmp_path, monkeypatch):
    """Exit 0 when cookies valid and auth_state='ok'."""
    from app.persistence import EventRepository

    _write_valid_cookie(tmp_path)
    monkeypatch.setenv("INSTANCE", "david")
    db_path = tmp_path / "sync.db"
    with EventRepository(db_path) as repo:
        repo.set_auth_state("david", "ok")

    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 0


def test_check_session_exits_zero_when_no_auth_state_record(tmp_path, monkeypatch):
    """Exit 0 when cookies valid and no auth_state row exists (backwards compatible)."""
    _write_valid_cookie(tmp_path)
    monkeypatch.setenv("INSTANCE", "david")

    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 0


def test_check_session_exits_two_when_db_is_unreadable(tmp_path, monkeypatch):
    """Exit 2 when cookies are valid but sync.db cannot be read (corrupted/locked).

    The bot interprets exit 2 as an unknown state (None) rather than
    a hard auth failure, preventing false-positive ⚠️ alerts.
    """
    _write_valid_cookie(tmp_path)
    monkeypatch.setenv("INSTANCE", "david")
    # Write a non-SQLite file so sqlite3.connect raises DatabaseError
    (tmp_path / "sync.db").write_text("not a sqlite database")

    with patch("app.config.read_data_dir", return_value=tmp_path):
        result = _runner().invoke(cli, ["check-session"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# check-pending command
# ---------------------------------------------------------------------------


def test_check_pending_exits_zero_when_pending_file_present(tmp_path):
    """check-pending exits 0 when the .tr_2fa_pending marker exists."""
    from app.twofa import PENDING_FILENAME

    (tmp_path / PENDING_FILENAME).touch()
    with patch("app.config.Config.from_env", return_value=_mock_cfg(tmp_path)):
        result = _runner().invoke(cli, ["check-pending"])
    assert result.exit_code == 0


def test_check_pending_exits_one_when_pending_file_absent(tmp_path):
    """check-pending exits 1 when no login is currently waiting."""
    with patch("app.config.Config.from_env", return_value=_mock_cfg(tmp_path)):
        result = _runner().invoke(cli, ["check-pending"])
    assert result.exit_code == 1


def test_check_pending_help():
    result = _runner().invoke(cli, ["check-pending", "--help"])
    assert result.exit_code == 0
    assert "pending" in result.output.lower()


# ---------------------------------------------------------------------------
# sync --instance flag
# ---------------------------------------------------------------------------


def test_sync_with_instance_flag_loads_from_config_file(tmp_path):
    """sync --instance <name> resolves config from InstancesConfig and passes it to run()."""
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run", return_value=0) as mock_run,
    ):
        result = _runner().invoke(
            cli,
            ["sync", "--instance", "david"],
            env={"INSTANCES_CONFIG": str(cfg_file)},
        )

    assert result.exit_code == 0
    mock_instances.to_config.assert_called_once_with("david")
    mock_run.assert_called_once_with(cfg=mock_cfg)


def test_sync_without_instance_flag_uses_env(monkeypatch):
    """sync without --instance falls back to Config.from_env() (backward compat)."""
    with patch("app.main.run", return_value=0) as mock_run:
        result = _runner().invoke(cli, ["sync"])
    assert result.exit_code == 0
    # run() called with no cfg argument (None default)
    mock_run.assert_called_once_with(cfg=None)


def test_sync_with_instance_flag_missing_instances_config_env(tmp_path):
    """sync --instance without INSTANCES_CONFIG env var exits with an error."""
    result = _runner().invoke(
        cli, ["sync", "--instance", "david"], env={"INSTANCES_CONFIG": ""}
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# login --instance flag
# ---------------------------------------------------------------------------


def test_login_with_instance_flag_loads_from_config_file(tmp_path):
    """login --instance <name> resolves config from InstancesConfig."""
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run_login", return_value=0) as mock_run,
    ):
        result = _runner().invoke(
            cli,
            ["login", "--instance", "david"],
            env={"INSTANCES_CONFIG": str(cfg_file)},
        )

    assert result.exit_code == 0
    mock_instances.to_config.assert_called_once_with("david")
    mock_run.assert_called_once_with(cfg=mock_cfg)


def test_login_without_instance_flag_uses_env():
    """login without --instance falls back to Config.from_env() (backward compat)."""
    with patch("app.main.run_login", return_value=0) as mock_run:
        result = _runner().invoke(cli, ["login"])
    assert result.exit_code == 0
    mock_run.assert_called_once_with(cfg=None)
