from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.twofa import (
    TelegramCodeProvider,
    TerminalCodeProvider,
    select_code_provider,
)

# ---------------------------------------------------------------------------
# TerminalCodeProvider
# ---------------------------------------------------------------------------

def test_terminal_provider_reads_and_strips_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda: "  123456 \n")
    assert TerminalCodeProvider().get_code() == "123456"


# ---------------------------------------------------------------------------
# TelegramCodeProvider
# ---------------------------------------------------------------------------

def _fake_clock():
    """Return a monotonic-like clock that advances by 1.0 on every call."""
    state = {"t": 0.0}

    def _now() -> float:
        state["t"] += 1.0
        return state["t"]

    return _now


def test_telegram_provider_prompts_then_returns_written_code(tmp_path):
    code_file = tmp_path / ".tr_2fa_code"
    prompt = MagicMock()

    # The code appears on the second poll: sleep writes it.
    def _sleep(_seconds: float) -> None:
        code_file.write_text("654321")

    provider = TelegramCodeProvider(
        code_file,
        prompt,
        timeout=100.0,
        poll_interval=1.0,
        sleep=_sleep,
        now=_fake_clock(),
    )

    assert provider.get_code() == "654321"
    prompt.assert_called_once()


def test_telegram_provider_deletes_code_file_after_reading(tmp_path):
    code_file = tmp_path / ".tr_2fa_code"

    def _prompt() -> None:
        code_file.write_text("111222")

    provider = TelegramCodeProvider(
        code_file,
        _prompt,
        timeout=100.0,
        poll_interval=1.0,
        sleep=lambda _s: None,
        now=_fake_clock(),
    )

    provider.get_code()
    assert not code_file.exists()


def test_telegram_provider_clears_stale_file_before_prompting(tmp_path):
    code_file = tmp_path / ".tr_2fa_code"
    code_file.write_text("STALE")
    seen: list[bool] = []

    def _prompt() -> None:
        # When prompting, the stale file must already be gone.
        seen.append(code_file.exists())
        code_file.write_text("999888")

    provider = TelegramCodeProvider(
        code_file,
        _prompt,
        timeout=100.0,
        poll_interval=1.0,
        sleep=lambda _s: None,
        now=_fake_clock(),
    )

    assert provider.get_code() == "999888"
    assert seen == [False]


def test_telegram_provider_times_out(tmp_path):
    code_file = tmp_path / ".tr_2fa_code"
    provider = TelegramCodeProvider(
        code_file,
        MagicMock(),
        timeout=3.0,
        poll_interval=1.0,
        sleep=lambda _s: None,
        now=_fake_clock(),
    )

    with pytest.raises(TimeoutError):
        provider.get_code()


def test_telegram_provider_ignores_empty_file(tmp_path):
    code_file = tmp_path / ".tr_2fa_code"
    code_file.write_text("   ")
    calls = {"n": 0}

    def _sleep(_seconds: float) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            code_file.write_text("424242")

    provider = TelegramCodeProvider(
        code_file,
        MagicMock(),
        timeout=100.0,
        poll_interval=1.0,
        sleep=_sleep,
        now=_fake_clock(),
    )

    assert provider.get_code() == "424242"


# ---------------------------------------------------------------------------
# select_code_provider
# ---------------------------------------------------------------------------

def test_select_returns_terminal_when_tty(tmp_path):
    provider = select_code_provider(
        data_dir=tmp_path,
        notifier=MagicMock(),
        instance="david",
        isatty=True,
        telegram_configured=True,
    )
    assert isinstance(provider, TerminalCodeProvider)


def test_select_returns_telegram_when_no_tty_but_configured(tmp_path):
    notifier = MagicMock()
    provider = select_code_provider(
        data_dir=tmp_path,
        notifier=notifier,
        instance="david",
        isatty=False,
        telegram_configured=True,
    )
    assert isinstance(provider, TelegramCodeProvider)


def test_select_telegram_prompt_calls_notifier_with_instance(tmp_path):
    notifier = MagicMock()
    provider = select_code_provider(
        data_dir=tmp_path,
        notifier=notifier,
        instance="david",
        isatty=False,
        telegram_configured=True,
    )
    provider._prompt()
    notifier.login_code_request.assert_called_once_with("david")


def test_select_telegram_uses_expected_code_file_path(tmp_path):
    provider = select_code_provider(
        data_dir=tmp_path,
        notifier=MagicMock(),
        instance="david",
        isatty=False,
        telegram_configured=True,
    )
    assert provider._code_file == tmp_path / ".tr_2fa_code"


def test_select_returns_none_when_no_tty_and_not_configured(tmp_path):
    provider = select_code_provider(
        data_dir=tmp_path,
        notifier=MagicMock(),
        instance="david",
        isatty=False,
        telegram_configured=False,
    )
    assert provider is None
