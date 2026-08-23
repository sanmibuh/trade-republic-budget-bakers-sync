from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.twofa import (
    PENDING_FILENAME,
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
        code_file=tmp_path / ".tr_2fa_code_david",
        pending_file=tmp_path / ".tr_2fa_pending_david",
        notifier=MagicMock(),
        instance="david",
        isatty=True,
        telegram_configured=True,
    )
    assert isinstance(provider, TerminalCodeProvider)


def test_select_returns_telegram_when_no_tty_but_configured(tmp_path):
    notifier = MagicMock()
    provider = select_code_provider(
        code_file=tmp_path / ".tr_2fa_code_david",
        pending_file=tmp_path / ".tr_2fa_pending_david",
        notifier=notifier,
        instance="david",
        isatty=False,
        telegram_configured=True,
    )
    assert isinstance(provider, TelegramCodeProvider)


def test_select_telegram_prompt_calls_notifier_with_instance(tmp_path):
    notifier = MagicMock()
    provider = select_code_provider(
        code_file=tmp_path / ".tr_2fa_code_david",
        pending_file=tmp_path / ".tr_2fa_pending_david",
        notifier=notifier,
        instance="david",
        isatty=False,
        telegram_configured=True,
    )
    provider._prompt()
    notifier.login_code_request.assert_called_once_with("david")


def test_select_telegram_uses_expected_code_file_path(tmp_path):
    code_file = tmp_path / ".tr_2fa_code_david"
    provider = select_code_provider(
        code_file=code_file,
        pending_file=tmp_path / ".tr_2fa_pending_david",
        notifier=MagicMock(),
        instance="david",
        isatty=False,
        telegram_configured=True,
    )
    assert provider._code_file == code_file


def test_select_returns_none_when_no_tty_and_not_configured(tmp_path):
    provider = select_code_provider(
        code_file=tmp_path / ".tr_2fa_code_david",
        pending_file=tmp_path / ".tr_2fa_pending_david",
        notifier=MagicMock(),
        instance="david",
        isatty=False,
        telegram_configured=False,
    )
    assert provider is None


# ---------------------------------------------------------------------------
# TelegramCodeProvider — on_timeout callback
# ---------------------------------------------------------------------------


def test_telegram_provider_calls_on_timeout_before_raising(tmp_path):
    """on_timeout must be called before TimeoutError is raised."""
    code_file = tmp_path / ".tr_2fa_code"
    on_timeout = MagicMock()

    provider = TelegramCodeProvider(
        code_file,
        MagicMock(),
        timeout=3.0,
        poll_interval=1.0,
        sleep=lambda _s: None,
        now=_fake_clock(),
        on_timeout=on_timeout,
    )

    with pytest.raises(TimeoutError):
        provider.get_code()

    on_timeout.assert_called_once()


def test_telegram_provider_does_not_call_on_timeout_when_code_received(tmp_path):
    """on_timeout must NOT be called when the code is received in time."""
    code_file = tmp_path / ".tr_2fa_code"
    on_timeout = MagicMock()

    def _sleep(_seconds: float) -> None:
        code_file.write_text("123456")

    provider = TelegramCodeProvider(
        code_file,
        MagicMock(),
        timeout=100.0,
        poll_interval=1.0,
        sleep=_sleep,
        now=_fake_clock(),
        on_timeout=on_timeout,
    )

    provider.get_code()
    on_timeout.assert_not_called()


def test_telegram_provider_on_timeout_none_does_not_raise_on_timeout(tmp_path):
    """When on_timeout is None (default), TimeoutError is still raised without errors."""
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


# ---------------------------------------------------------------------------
# select_code_provider — on_timeout wiring
# ---------------------------------------------------------------------------


def test_select_telegram_wires_on_timeout_to_notifier(tmp_path):
    """select_code_provider must wire on_timeout to notifier.login_code_timeout(instance)."""
    notifier = MagicMock()
    provider = select_code_provider(
        code_file=tmp_path / ".tr_2fa_code_david",
        pending_file=tmp_path / ".tr_2fa_pending_david",
        notifier=notifier,
        instance="david",
        isatty=False,
        telegram_configured=True,
    )
    assert isinstance(provider, TelegramCodeProvider)
    provider._on_timeout()
    notifier.login_code_timeout.assert_called_once_with("david")


# ---------------------------------------------------------------------------
# TelegramCodeProvider — pending marker
# ---------------------------------------------------------------------------


def test_telegram_provider_creates_pending_marker_while_polling(tmp_path):
    """The pending marker must exist while polling for the code."""
    code_file = tmp_path / ".tr_2fa_code"
    pending_file = tmp_path / PENDING_FILENAME
    marker_seen: list[bool] = []

    def _sleep(_seconds: float) -> None:
        marker_seen.append(pending_file.exists())
        code_file.write_text("111111")

    provider = TelegramCodeProvider(
        code_file,
        MagicMock(),
        timeout=100.0,
        poll_interval=1.0,
        sleep=_sleep,
        now=_fake_clock(),
    )

    provider.get_code()
    assert any(marker_seen), "Pending marker was never observed during polling"


def test_telegram_provider_clears_pending_marker_on_success(tmp_path):
    """The pending marker must be removed when the code is received."""
    code_file = tmp_path / ".tr_2fa_code"
    pending_file = tmp_path / PENDING_FILENAME

    def _sleep(_seconds: float) -> None:
        code_file.write_text("222222")

    provider = TelegramCodeProvider(
        code_file,
        MagicMock(),
        timeout=100.0,
        poll_interval=1.0,
        sleep=_sleep,
        now=_fake_clock(),
    )

    provider.get_code()
    assert not pending_file.exists()


def test_telegram_provider_clears_pending_marker_on_timeout(tmp_path):
    """The pending marker must be removed when the timeout elapses."""
    code_file = tmp_path / ".tr_2fa_code"
    pending_file = tmp_path / PENDING_FILENAME

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

    assert not pending_file.exists()


# ---------------------------------------------------------------------------
# TelegramCodeProvider — explicit pending_file parameter (issue #174)
# ---------------------------------------------------------------------------


def test_telegram_provider_uses_explicit_pending_file(tmp_path):
    """When pending_file is passed explicitly, it must be used instead of the default."""
    code_file = tmp_path / ".tr_2fa_code_alice"
    explicit_pending = tmp_path / ".tr_2fa_pending_alice"
    marker_seen: list[bool] = []

    def _sleep(_seconds: float) -> None:
        marker_seen.append(explicit_pending.exists())
        code_file.write_text("999999")

    provider = TelegramCodeProvider(
        code_file,
        MagicMock(),
        pending_file=explicit_pending,
        timeout=100.0,
        poll_interval=1.0,
        sleep=_sleep,
        now=_fake_clock(),
    )

    provider.get_code()
    assert any(marker_seen), "Explicit pending marker was never observed"
    assert not explicit_pending.exists()


def test_telegram_provider_explicit_pending_file_does_not_collide_with_default(
    tmp_path,
):
    """Explicit pending_file must not touch the default derived pending path."""
    code_file = tmp_path / ".tr_2fa_code_alice"
    explicit_pending = tmp_path / ".tr_2fa_pending_alice"
    default_pending = code_file.parent / PENDING_FILENAME  # would be .tr_2fa_pending

    def _sleep(_s: float) -> None:
        code_file.write_text("123456")

    TelegramCodeProvider(
        code_file,
        MagicMock(),
        pending_file=explicit_pending,
        timeout=100.0,
        poll_interval=1.0,
        sleep=_sleep,
        now=_fake_clock(),
    ).get_code()

    assert not default_pending.exists(), "Default pending file must not be created"


# ---------------------------------------------------------------------------
# select_code_provider — flat file paths (issue #174)
# ---------------------------------------------------------------------------


def test_select_code_provider_uses_provided_code_file(tmp_path):
    """select_code_provider must pass code_file directly to TelegramCodeProvider."""
    code_file = tmp_path / ".tr_2fa_code_alice"
    pending_file = tmp_path / ".tr_2fa_pending_alice"
    provider = select_code_provider(
        code_file=code_file,
        pending_file=pending_file,
        notifier=MagicMock(),
        instance="alice",
        isatty=False,
        telegram_configured=True,
    )
    assert isinstance(provider, TelegramCodeProvider)
    assert provider._code_file == code_file


def test_select_code_provider_uses_provided_pending_file(tmp_path):
    """select_code_provider must pass pending_file directly to TelegramCodeProvider."""
    code_file = tmp_path / ".tr_2fa_code_alice"
    pending_file = tmp_path / ".tr_2fa_pending_alice"
    provider = select_code_provider(
        code_file=code_file,
        pending_file=pending_file,
        notifier=MagicMock(),
        instance="alice",
        isatty=False,
        telegram_configured=True,
    )
    assert isinstance(provider, TelegramCodeProvider)
    assert provider._pending_file == pending_file


def test_select_code_provider_returns_terminal_when_isatty(tmp_path):
    """When isatty=True, select_code_provider must return TerminalCodeProvider."""
    provider = select_code_provider(
        code_file=tmp_path / ".tr_2fa_code_x",
        pending_file=tmp_path / ".tr_2fa_pending_x",
        notifier=MagicMock(),
        instance="x",
        isatty=True,
        telegram_configured=True,
    )
    assert isinstance(provider, TerminalCodeProvider)


def test_select_code_provider_returns_none_when_no_tty_no_telegram(tmp_path):
    """When isatty=False and telegram_configured=False, must return None."""
    provider = select_code_provider(
        code_file=tmp_path / ".tr_2fa_code_x",
        pending_file=tmp_path / ".tr_2fa_pending_x",
        notifier=MagicMock(),
        instance="x",
        isatty=False,
        telegram_configured=False,
    )
    assert provider is None
