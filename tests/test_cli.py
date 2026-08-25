from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
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


def test_sync_calls_run(tmp_path):
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.allow_insecure_ssl = False
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path
    with (
        patch("app.main.run", return_value=0) as mock_run,
        patch("app.__main__.setup_logging"),
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        result = _runner().invoke(cli, ["sync", "--instance", "user1"])
    assert result.exit_code == 0
    mock_run.assert_called_once()


def test_sync_exits_with_run_return_code(tmp_path):
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.allow_insecure_ssl = False
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path
    with (
        patch("app.main.run", return_value=1),
        patch("app.__main__.setup_logging"),
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        result = _runner().invoke(cli, ["sync", "--instance", "user1"])
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


def _mock_backup_cfg(tmp_path):
    cfg = MagicMock()
    cfg.wallet_api_key = "key"
    cfg.telegram_bot_token = None
    cfg.telegram_chat_id = None
    cfg.owner_name = "Test"
    cfg.data_dir = tmp_path
    cfg.allow_insecure_ssl = False
    return cfg


# ---------------------------------------------------------------------------
# backup auto
# ---------------------------------------------------------------------------


def test_backup_auto_calls_run_auto(tmp_path):
    with (
        patch("app.__main__.setup_logging") as mock_setup_log,
        patch(
            "app.__main__._resolve_backup_cfg",
            return_value=_mock_backup_cfg(tmp_path),
        ),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_auto") as mock_auto,
    ):
        result = _runner().invoke(cli, ["backup", "auto"])
    assert result.exit_code == 0
    mock_auto.assert_called_once()
    mock_setup_log.assert_called_once_with(tmp_path)


def test_backup_auto_loads_config_from_instances_yaml(tmp_path):
    """backup auto resolves config exclusively from InstancesConfig."""
    from app.config import BackupConfig

    expected_cfg = BackupConfig(
        owner_name="Backup",
        wallet_api_key="yamlkey",
        telegram_bot_token=None,
        telegram_chat_id=None,
        data_dir=tmp_path / "backup",
        allow_insecure_ssl=False,
    )
    with (
        patch("app.__main__.setup_logging"),
        patch(
            "app.__main__._resolve_backup_cfg", return_value=expected_cfg
        ) as mock_resolve,
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
        patch("app.backup.run_auto") as mock_auto,
    ):
        result = _runner().invoke(cli, ["backup", "auto"])
    assert result.exit_code == 0, result.output
    mock_resolve.assert_called_once()
    mock_auto.assert_called_once()


def test_backup_resolve_cfg_uses_instances_config_when_set(tmp_path, monkeypatch):
    """When instances.yml is present, _resolve_backup_cfg loads config from the YAML."""
    from app.__main__ import _resolve_backup_cfg
    from app.config import BackupConfig

    yaml = tmp_path / "instances.yml"
    yaml.write_text(f"""\
data_dir: {tmp_path}
sync:
  instances:
    - name: user1
      phone: "+34600000000"
      pin: "1234"
      wallet_api_key: "yamlkey"
      wallet_cash_account_id: "cash"
      wallet_portfolio_account_id: "port"
""")
    with patch("app.config.INSTANCES_CONFIG_PATH", yaml):
        cfg = _resolve_backup_cfg()

    assert isinstance(cfg, BackupConfig)
    assert cfg.wallet_api_key == "yamlkey"


def test_backup_resolve_cfg_raises_usage_error_when_instances_config_invalid(
    tmp_path,
):
    """When instances.yml is invalid, raise UsageError."""
    import click

    from app.__main__ import _resolve_backup_cfg

    bad_yaml = tmp_path / "instances.yml"
    bad_yaml.write_text(
        "sync:\n  instances: []\n"
    )  # valid YAML but no instances → ValueError
    with (
        patch("app.config.INSTANCES_CONFIG_PATH", bad_yaml),
        pytest.raises(click.UsageError),
    ):
        _resolve_backup_cfg()


# ---------------------------------------------------------------------------
# backup monthly
# ---------------------------------------------------------------------------


def test_backup_monthly_default_calls_run_monthly(tmp_path):
    with (
        patch("app.__main__.setup_logging"),
        patch(
            "app.__main__._resolve_backup_cfg",
            return_value=_mock_backup_cfg(tmp_path),
        ),
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
        patch(
            "app.__main__._resolve_backup_cfg",
            return_value=_mock_backup_cfg(tmp_path),
        ),
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
        patch(
            "app.__main__._resolve_backup_cfg",
            return_value=_mock_backup_cfg(tmp_path),
        ),
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
        patch(
            "app.__main__._resolve_backup_cfg",
            return_value=_mock_backup_cfg(tmp_path),
        ),
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
        patch(
            "app.__main__._resolve_backup_cfg",
            return_value=_mock_backup_cfg(tmp_path),
        ),
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
        patch(
            "app.__main__._resolve_backup_cfg",
            return_value=_mock_backup_cfg(tmp_path),
        ),
        patch("app.wallet_client.WalletClient"),
        patch("app.notifier.Notifier"),
    ):
        result = _runner().invoke(cli, ["backup", "yearly", "not-a-year"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# submit-code command
# ---------------------------------------------------------------------------


def test_submit_code_writes_code_file(tmp_path):
    from app.config import Config, InstancesConfig

    pending_file = tmp_path / ".tr_2fa_pending_user1"
    code_file = tmp_path / ".tr_2fa_code_user1"
    pending_file.write_text("")
    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = tmp_path
    mock_cfg.twofa_pending_file = pending_file
    mock_cfg.twofa_code_file = code_file
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path
    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        result = _runner().invoke(cli, ["submit-code", "--instance", "user1", "123456"])

    assert result.exit_code == 0
    assert code_file.read_text() == "123456"


def test_submit_code_rejects_when_no_pending_marker(tmp_path):
    """submit-code must fail with a clear error when no login is waiting."""
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = tmp_path
    mock_cfg.twofa_pending_file = tmp_path / ".tr_2fa_pending_user1"
    mock_cfg.twofa_code_file = tmp_path / ".tr_2fa_code_user1"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path
    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        result = _runner().invoke(cli, ["submit-code", "--instance", "user1", "123456"])

    assert result.exit_code != 0
    assert "No active login request" in result.output


def test_submit_code_pending_marker_absent_does_not_write_code_file(tmp_path):
    """When no pending marker exists the code file must NOT be written."""
    from app.config import Config, InstancesConfig

    code_file = tmp_path / ".tr_2fa_code_user1"
    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = tmp_path
    mock_cfg.twofa_pending_file = tmp_path / ".tr_2fa_pending_user1"
    mock_cfg.twofa_code_file = code_file
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path
    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        _runner().invoke(cli, ["submit-code", "--instance", "user1", "999999"])

    assert not code_file.exists()


# ---------------------------------------------------------------------------
# bot command
# ---------------------------------------------------------------------------


def test_bot_help():
    result = _runner().invoke(cli, ["bot", "--help"])
    assert result.exit_code == 0
    assert "bot" in result.output.lower()


def test_bot_help_lists_logs_and_code_commands():
    """`bot --help` must document /logs and /code — both are implemented commands."""
    result = _runner().invoke(cli, ["bot", "--help"])
    assert result.exit_code == 0
    assert "/logs" in result.output
    assert "/code" in result.output


def test_bot_calls_run():

    with (
        patch("app.bot.run") as mock_run,
        patch("app.__main__.setup_logging"),
        patch("app.config.InstancesConfig.load"),
    ):
        _runner().invoke(cli, ["bot"])
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# check-pending command
# ---------------------------------------------------------------------------


def test_check_pending_exits_zero_when_pending_file_present(tmp_path):
    """check-pending exits 0 when the .tr_2fa_pending marker exists."""
    from app.config import Config, InstancesConfig

    pending_file = tmp_path / ".tr_2fa_pending_user1"
    pending_file.touch()
    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = tmp_path
    mock_cfg.twofa_pending_file = pending_file
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path
    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        result = _runner().invoke(cli, ["check-pending", "--instance", "user1"])
    assert result.exit_code == 0


def test_check_pending_exits_one_when_pending_file_absent(tmp_path):
    """check-pending exits 1 when no login is currently waiting."""
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = tmp_path
    mock_cfg.twofa_pending_file = tmp_path / ".tr_2fa_pending_user1"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path
    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        result = _runner().invoke(cli, ["check-pending", "--instance", "user1"])
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
    mock_cfg.data_dir = tmp_path / "sync" / "user1"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run", return_value=0) as mock_run,
        patch("app.__main__.setup_logging"),
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        result = _runner().invoke(
            cli,
            ["sync", "--instance", "user1"],
        )

    assert result.exit_code == 0
    mock_instances.to_config.assert_called_once_with("user1")
    mock_run.assert_called_once_with(cfg=mock_cfg)


def test_sync_with_instance_flag_uses_data_dir_for_logging(tmp_path):
    """sync --instance must call setup_logging with instances_yaml.data_dir (root), not cfg.data_dir."""
    from app.config import Config, InstancesConfig

    data_dir = tmp_path / "data"
    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = data_dir / "sync" / "user1"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = data_dir

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run", return_value=0),
        patch("app.__main__.setup_logging") as mock_setup,
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        _runner().invoke(
            cli,
            ["sync", "--instance", "user1"],
        )

    mock_setup.assert_called_once_with(data_dir)


def test_bot_command_uses_yaml_data_dir_for_logging(tmp_path):
    """bot CLI command must derive the setup_logging path from InstancesConfig.data_dir."""
    from app.config import InstancesConfig

    yaml_root = tmp_path / "yaml_root"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.data_dir = yaml_root

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.bot.run"),
        patch("app.__main__.setup_logging") as mock_setup,
    ):
        _runner().invoke(cli, ["bot"])

    mock_setup.assert_called_once_with(yaml_root)


def test_bot_missing_instances_config_shows_clean_error():
    """bot when YAML file is missing must show a clean UsageError, not a traceback."""
    result = _runner().invoke(cli, ["bot"])
    assert result.exit_code != 0
    # Must not produce a raw exception traceback
    assert "Traceback" not in result.output
    assert "Error" in result.output or result.exit_code == 2


def test_bot_invalid_instances_config_shows_clean_error(tmp_path):
    """bot with a broken instances YAML must show a clean UsageError."""
    bad_yaml = tmp_path / "bad.yml"
    bad_yaml.write_text(": invalid: yaml: [")
    with patch("app.config.INSTANCES_CONFIG_PATH", bad_yaml):
        result = _runner().invoke(cli, ["bot"])
    assert result.exit_code != 0
    assert "Traceback" not in result.output


def test_bot_run_config_error_shows_clean_error(tmp_path):
    """ValueError raised inside bot.run() (e.g. second BotConfig.from_env load) must
    surface as a clean UsageError, not a raw traceback."""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    from app.config import InstancesConfig

    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.data_dir = tmp_path

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.__main__.setup_logging"),
        patch("app.bot.run", side_effect=ValueError("bad env var in second load")),
    ):
        result = _runner().invoke(cli, ["bot"])

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "bad env var" in result.output


def test_sync_with_instance_flag_missing_instances_config_env(tmp_path):
    """sync --instance when YAML file is missing exits with an error."""
    result = _runner().invoke(cli, ["sync", "--instance", "user1"])
    assert result.exit_code != 0


def test_sync_with_instance_flag_load_error_shown_as_click_error(tmp_path):
    """Errors from InstancesConfig.load() are shown as a Click UsageError, not a traceback."""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    with (
        patch("app.config.InstancesConfig.load", side_effect=ValueError("bad config")),
        patch("app.__main__.setup_logging"),
    ):
        result = _runner().invoke(
            cli,
            ["sync", "--instance", "user1"],
        )

    assert result.exit_code != 0
    assert "bad config" in result.output


def test_sync_with_blank_instance_flag_exits_with_error():
    """sync --instance '' (blank string) must error out, not silently use env vars."""
    result = _runner().invoke(cli, ["sync", "--instance", ""])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# list-instances command
# ---------------------------------------------------------------------------


def test_list_instances_outputs_names_one_per_line(tmp_path):
    """list-instances prints each instance name on its own line and exits 0."""
    from app.config import InstanceConfig, InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.instances = [
        MagicMock(spec=InstanceConfig, name_attr=None),
        MagicMock(spec=InstanceConfig, name_attr=None),
    ]
    mock_instances.instances[0].name = "david"
    mock_instances.instances[1].name = "eli"

    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        result = _runner().invoke(
            cli,
            ["list-instances"],
        )

    assert result.exit_code == 0
    assert result.output.strip().splitlines() == ["david", "eli"]


def test_list_instances_file_not_found_exits_with_error():
    """list-instances when YAML file is missing exits with an error."""
    result = _runner().invoke(cli, ["list-instances"])
    assert result.exit_code != 0


def test_list_instances_load_error_shown_as_click_error(tmp_path):
    """Errors from InstancesConfig.load() are shown as UsageError, not traceback."""
    with patch("app.config.InstancesConfig.load", side_effect=ValueError("bad yaml")):
        result = _runner().invoke(
            cli,
            ["list-instances"],
        )

    assert result.exit_code != 0
    assert "bad yaml" in result.output


def test_list_instances_permission_error_shown_as_click_error(tmp_path):
    """OSError (e.g. PermissionError) from reading the file is shown as UsageError."""
    with patch(
        "app.config.InstancesConfig.load", side_effect=PermissionError("denied")
    ):
        result = _runner().invoke(
            cli,
            ["list-instances"],
        )

    assert result.exit_code != 0
    assert "denied" in result.output


def test_sync_with_instance_flag_permission_error_shown_as_click_error(tmp_path):
    """PermissionError from InstancesConfig.load() in sync --instance must show UsageError."""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    with (
        patch("app.config.InstancesConfig.load", side_effect=PermissionError("denied")),
        patch("app.__main__.setup_logging"),
    ):
        result = _runner().invoke(
            cli,
            ["sync", "--instance", "user1"],
        )

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "denied" in result.output


def test_bot_command_does_not_load_instances_config_twice(tmp_path):
    """bot CLI must not load InstancesConfig a second time inside bot.run()."""
    from app.config import InstancesConfig

    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.data_dir = tmp_path
    mock_instances.telegram_bot_token = "testtoken"
    mock_instances.telegram_chat_id = "123456"
    mock_instances.allow_insecure_ssl = False
    mock_instances.instances = []
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    load_calls: list[object] = []

    def counting_load(path: object) -> object:
        load_calls.append(path)
        return mock_instances

    with (
        patch("app.config.InstancesConfig.load", side_effect=counting_load),
        patch("app.bot.TelegramBot") as mock_telegram,
        patch("app.__main__.setup_logging"),
    ):
        mock_telegram.return_value.run.return_value = None
        _runner().invoke(cli, ["bot"])

    assert len(load_calls) == 1, (
        f"InstancesConfig.load() called {len(load_calls)} times; expected 1"
    )


# ---------------------------------------------------------------------------
# list-schedules command
# ---------------------------------------------------------------------------


def _make_mock_instances_with_schedules(instances_data: list[tuple[str, str | None]]):
    """Return a mock InstancesConfig with instances having given (name, schedule) pairs."""
    from app.config import InstanceConfig, InstancesConfig

    mock_cfg = MagicMock(spec=InstancesConfig)
    mock_instances = []
    for name, schedule in instances_data:
        inst = MagicMock(spec=InstanceConfig)
        inst.name = name
        inst.schedule = schedule
        mock_instances.append(inst)
    mock_cfg.instances = mock_instances
    return mock_cfg


def test_list_schedules_outputs_name_tab_schedule(tmp_path):
    """list-schedules prints 'name<TAB>schedule' per instance when all have a schedule."""
    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    mock_cfg = _make_mock_instances_with_schedules(
        [("david", "0 8,14,21 * * *"), ("eli", "5 8,14,21 * * *")]
    )

    with patch("app.config.InstancesConfig.load", return_value=mock_cfg):
        result = _runner().invoke(
            cli,
            ["list-schedules"],
        )

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines == ["david\t0 8,14,21 * * *", "eli\t5 8,14,21 * * *"]


def test_list_schedules_omits_instances_with_no_schedule(tmp_path):
    """list-schedules skips instances whose schedule is None."""
    mock_cfg = _make_mock_instances_with_schedules(
        [("david", "0 8 * * *"), ("eli", None)]
    )

    with patch("app.config.InstancesConfig.load", return_value=mock_cfg):
        result = _runner().invoke(
            cli,
            ["list-schedules"],
        )

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert lines == ["david\t0 8 * * *"]


def test_list_schedules_file_not_found_exits_with_error():
    """list-schedules when YAML file is missing exits non-zero."""
    result = _runner().invoke(cli, ["list-schedules"])
    assert result.exit_code != 0


def test_list_schedules_load_error_shown_as_click_error(tmp_path):
    """Errors from InstancesConfig.load() are shown as UsageError, not traceback."""
    with patch("app.config.InstancesConfig.load", side_effect=ValueError("bad")):
        result = _runner().invoke(
            cli,
            ["list-schedules"],
        )

    assert result.exit_code != 0
    assert "bad" in result.output


# ---------------------------------------------------------------------------
# get-backup-schedule command
# ---------------------------------------------------------------------------


def test_get_backup_schedule_outputs_schedule(tmp_path):
    """get-backup-schedule prints the backup_schedule and exits 0."""
    from app.config import InstancesConfig

    cfg_file = tmp_path / "instances.yml"
    cfg_file.write_text("")

    mock_cfg = MagicMock(spec=InstancesConfig)
    mock_cfg.backup_schedule = "0 3 * * *"

    with patch("app.config.InstancesConfig.load", return_value=mock_cfg):
        result = _runner().invoke(
            cli,
            ["get-backup-schedule"],
        )

    assert result.exit_code == 0
    assert result.output.strip() == "0 3 * * *"


def test_get_backup_schedule_exits_zero_with_empty_output_when_not_set(tmp_path):
    """get-backup-schedule exits 0 and prints nothing when backup_schedule is None."""
    from app.config import InstancesConfig

    mock_cfg = MagicMock(spec=InstancesConfig)
    mock_cfg.backup_schedule = None

    with patch("app.config.InstancesConfig.load", return_value=mock_cfg):
        result = _runner().invoke(
            cli,
            ["get-backup-schedule"],
        )

    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_get_backup_schedule_file_not_found_exits_with_error():
    """get-backup-schedule when YAML file is missing exits non-zero."""
    result = _runner().invoke(cli, ["get-backup-schedule"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# New behaviour: legacy single-container mode removed (issue #162)
# ---------------------------------------------------------------------------


def test_sync_without_instance_flag_exits_with_error():
    """sync without --instance must exit with an error (no env-var fallback)."""
    result = _runner().invoke(cli, ["sync"])
    assert result.exit_code != 0


def test_resync_with_instance_flag_calls_run_resync(tmp_path):
    """resync --instance <name> resolves config from InstancesConfig."""
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.allow_insecure_ssl = False
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run_resync", return_value=0) as mock_run,
        patch("app.__main__.setup_logging"),
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        result = _runner().invoke(cli, ["resync", "--instance", "user1", "2026-07-15"])

    assert result.exit_code == 0
    mock_run.assert_called_once_with("2026-07-15", cfg=mock_cfg)


def test_resync_without_instance_flag_exits_with_error():
    """resync without --instance must exit with an error."""
    result = _runner().invoke(cli, ["resync", "2026-07-15"])
    assert result.exit_code != 0


def test_submit_code_with_instance_flag_writes_code_file(tmp_path):
    """submit-code --instance <name> writes code to the correct instance 2FA path."""
    from app.config import Config, InstancesConfig

    pending_file = tmp_path / ".tr_2fa_pending_user1"
    code_file = tmp_path / ".tr_2fa_code_user1"
    pending_file.write_text("")

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = tmp_path
    mock_cfg.twofa_pending_file = pending_file
    mock_cfg.twofa_code_file = code_file
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path

    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        result = _runner().invoke(cli, ["submit-code", "--instance", "user1", "123456"])

    assert result.exit_code == 0
    assert code_file.read_text() == "123456"


def test_submit_code_without_instance_flag_exits_with_error():
    """submit-code without --instance must exit with an error."""
    result = _runner().invoke(cli, ["submit-code", "123456"])
    assert result.exit_code != 0


def test_check_pending_with_instance_flag_exits_zero(tmp_path):
    """check-pending --instance exits 0 when pending file is present."""
    from app.config import Config, InstancesConfig

    pending_file = tmp_path / ".tr_2fa_pending_user1"
    pending_file.touch()

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.data_dir = tmp_path
    mock_cfg.twofa_pending_file = pending_file
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path

    with patch("app.config.InstancesConfig.load", return_value=mock_instances):
        result = _runner().invoke(cli, ["check-pending", "--instance", "user1"])

    assert result.exit_code == 0


def test_check_pending_without_instance_flag_exits_with_error():
    """check-pending without --instance must exit with an error."""
    result = _runner().invoke(cli, ["check-pending"])
    assert result.exit_code != 0


def test_backup_resolve_cfg_does_not_fall_back_to_env(tmp_path, monkeypatch):
    """_resolve_backup_cfg must NOT fall back to BackupConfig.from_env() when YAML is missing."""
    import click

    from app.__main__ import _resolve_backup_cfg

    monkeypatch.setenv("WALLET_API_KEY", "envkey")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    with (
        patch(
            "app.config.InstancesConfig.load",
            side_effect=FileNotFoundError("no yaml"),
        ),
        pytest.raises(click.UsageError),
    ):
        _resolve_backup_cfg()


# ---------------------------------------------------------------------------
# check-day command
# ---------------------------------------------------------------------------


def test_check_day_with_instance_flag_calls_run_check_day(tmp_path):
    """check-day --instance <name> resolves config and calls run_check_day."""
    from app.config import Config, InstancesConfig
    from app.main import CheckDayResult

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.allow_insecure_ssl = False
    mock_cfg.owner_name = "User1"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path

    check_result = CheckDayResult(date="2026-08-20")

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run_check_day", return_value=check_result) as mock_run,
        patch("app.__main__.setup_logging"),
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        result = _runner().invoke(
            cli, ["check-day", "--instance", "user1", "2026-08-20"]
        )

    assert result.exit_code == 0
    mock_run.assert_called_once_with("2026-08-20", cfg=mock_cfg)


def test_check_day_without_instance_flag_exits_with_error():
    """check-day without --instance must exit with an error."""
    result = _runner().invoke(cli, ["check-day", "2026-08-20"])
    assert result.exit_code != 0


def test_check_day_invalid_date_exits_one(tmp_path):
    """check-day exits 1 when run_check_day returns None (invalid date)."""
    from app.config import Config, InstancesConfig

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.allow_insecure_ssl = False
    mock_cfg.owner_name = "User1"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run_check_day", return_value=None),
        patch("app.__main__.setup_logging"),
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        result = _runner().invoke(
            cli, ["check-day", "--instance", "user1", "not-a-date"]
        )

    assert result.exit_code == 1


def test_check_day_prints_report(tmp_path):
    """check-day prints a formatted report to stdout."""
    from app.config import Config, InstancesConfig
    from app.main import CheckDayResult, EventSummary

    mock_cfg = MagicMock(spec=Config)
    mock_cfg.allow_insecure_ssl = False
    mock_cfg.owner_name = "MyAccount"
    mock_instances = MagicMock(spec=InstancesConfig)
    mock_instances.to_config.return_value = mock_cfg
    mock_instances.data_dir = tmp_path

    check_result = CheckDayResult(
        date="2026-08-20",
        processed=[
            EventSummary(
                event_id="abc123",
                timestamp="2026-08-20T08:12:00+00:00",
                amount="2.34",
                currency="EUR",
                description="Interest",
            )
        ],
        not_processed=[
            EventSummary(
                event_id="jkl012",
                timestamp="2026-08-20T21:00:00+00:00",
                amount="-100.00",
                currency="EUR",
                description="Trade",
            )
        ],
    )

    with (
        patch("app.config.InstancesConfig.load", return_value=mock_instances),
        patch("app.main.run_check_day", return_value=check_result),
        patch("app.__main__.setup_logging"),
        patch("app.http_client.configure"),
        patch("app.persistence.init_db"),
    ):
        result = _runner().invoke(
            cli, ["check-day", "--instance", "user1", "2026-08-20"]
        )

    assert result.exit_code == 0
    assert "2026-08-20" in result.output
    assert "Already processed (1)" in result.output
    assert "Not yet processed (1)" in result.output
    assert "abc123" in result.output
    assert "jkl012" in result.output
    assert "Total: 2 events found" in result.output
