from __future__ import annotations

import logging

from app.logging_setup import (
    _NOISY_LOGGERS,
    _suppress_noisy_loggers,
    configure_logging,
    setup_logging,
)

_EXPECTED_FMT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_EXPECTED_DATEFMT = "%Y-%m-%d %H:%M:%S"


def test_setup_logging_creates_log_file(tmp_path):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        result = setup_logging(tmp_path)
        log_file = tmp_path / "sync.log"
        assert log_file.exists()
        assert result is None
    finally:
        # Remove handlers added by this call to avoid polluting other tests
        for h in root.handlers[:]:
            if h not in original_handlers:
                root.removeHandler(h)
                h.close()


def test_setup_logging_adds_file_and_console_handlers(tmp_path):
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        setup_logging(tmp_path)
        added = [h for h in root.handlers if h not in before]
        handler_types = {type(h).__name__ for h in added}
        assert "RotatingFileHandler" in handler_types
        assert "StreamHandler" in handler_types
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_setup_logging_creates_data_dir_if_missing(tmp_path):
    log_dir = tmp_path / "nested" / "logs"
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        setup_logging(log_dir)
        assert log_dir.exists()
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_configure_logging_adds_stream_handler():
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        # Remove all handlers to ensure root has none
        for h in root.handlers[:]:
            root.removeHandler(h)
        configure_logging()
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        # Restore original handlers
        for h in before:
            root.addHandler(h)


def test_configure_logging_idempotent():
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        for h in root.handlers[:]:
            root.removeHandler(h)
        configure_logging()
        count_after_first = len(root.handlers)
        configure_logging()  # second call should be a no-op
        assert len(root.handlers) == count_after_first
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        for h in before:
            root.addHandler(h)


def test_setup_logging_idempotent(tmp_path):
    """Calling setup_logging() twice for the same log_dir must not duplicate handlers."""
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        setup_logging(tmp_path)
        handlers_after_first = [h for h in root.handlers if h not in before]
        setup_logging(tmp_path)  # second call — same dir
        handlers_after_second = [h for h in root.handlers if h not in before]
        assert len(handlers_after_second) == len(handlers_after_first), (
            "setup_logging() added duplicate handlers on second call"
        )
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_setup_logging_suppresses_noisy_library_loggers(tmp_path):
    """httpx / telegram / hpack loggers must be raised to WARNING after setup_logging."""
    root = logging.getLogger()
    before = set(root.handlers)
    original_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
        # Start from a known low level so the raise-to-WARNING is always exercised.
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
        setup_logging(tmp_path)
        for name in _NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING, (
                f"Logger {name!r} should be WARNING after setup_logging"
            )
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)


def test_configure_logging_suppresses_noisy_library_loggers():
    """httpx / telegram / hpack loggers must be raised to WARNING after configure_logging."""
    root = logging.getLogger()
    before = set(root.handlers)
    original_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
        for h in root.handlers[:]:
            root.removeHandler(h)
        # Start from a known low level so the raise-to-WARNING is always exercised.
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
        configure_logging()
        for name in _NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING, (
                f"Logger {name!r} should be WARNING after configure_logging"
            )
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        for h in before:
            root.addHandler(h)
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)


def test_suppress_noisy_loggers_does_not_lower_stricter_level(tmp_path):
    """A logger already at ERROR must not be lowered to WARNING."""
    root = logging.getLogger()
    before = set(root.handlers)
    probe = "httpx"
    original_level = logging.getLogger(probe).level
    try:
        logging.getLogger(probe).setLevel(logging.ERROR)
        setup_logging(tmp_path)
        assert logging.getLogger(probe).level == logging.ERROR, (
            f"Logger {probe!r} should remain at ERROR; it must not be lowered to WARNING"
        )
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        logging.getLogger(probe).setLevel(original_level)


def test_setup_logging_resuppresses_noisy_loggers_on_repeated_call(tmp_path):
    """setup_logging must re-suppress noisy loggers even when handlers already exist.

    If a noisy logger is reset to DEBUG between two setup_logging calls (e.g. in
    a long-running process or between tests), the second call must still raise it
    back to WARNING — the idempotency guard must not skip suppression.
    """
    root = logging.getLogger()
    before = set(root.handlers)
    original_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
        # First call: establishes handlers and suppresses noisy loggers.
        setup_logging(tmp_path)
        # Simulate noisy loggers being reset (e.g. by a library or test teardown).
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
        # Second call hits the idempotency guard — suppression must still happen.
        setup_logging(tmp_path)
        for name in _NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING, (
                f"Logger {name!r} should be WARNING after repeated setup_logging call"
            )
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)


def test_configure_logging_resuppresses_noisy_loggers_on_repeated_call():
    """configure_logging must re-suppress noisy loggers even when handlers already exist."""
    root = logging.getLogger()
    before = set(root.handlers)
    original_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
        for h in root.handlers[:]:
            root.removeHandler(h)
        configure_logging()
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.DEBUG)
        configure_logging()  # hits the idempotency guard
        for name in _NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING, (
                f"Logger {name!r} should be WARNING after repeated configure_logging call"
            )
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        for h in before:
            root.addHandler(h)
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)


def test_setup_logging_suppresses_notset_loggers_inheriting_debug_from_root(tmp_path):
    """Noisy loggers at NOTSET must be suppressed on the first setup_logging call.

    Before setup_logging runs, root is at WARNING (its default before any
    explicit configuration).  A NOTSET noisy logger then inherits WARNING, so
    the pre-guard suppression call sees effective=WARNING and skips it.  After
    root is raised to DEBUG those loggers become verbose again.  The fix is to
    call suppression *after* root.setLevel(DEBUG) on the first call too.
    """
    root = logging.getLogger()
    before = set(root.handlers)
    original_root = root.level
    original_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
        root.setLevel(logging.WARNING)  # simulate default state before any setup call
        for name in _NOISY_LOGGERS:
            logging.getLogger(name).setLevel(
                logging.NOTSET
            )  # inherits WARNING → skipped
        setup_logging(tmp_path)
        # After setup_logging root is DEBUG; NOTSET loggers now inherit DEBUG.
        # They must have been suppressed to WARNING.
        for name in _NOISY_LOGGERS:
            assert logging.getLogger(name).level == logging.WARNING, (
                f"Logger {name!r} should be WARNING; suppression must run after "
                "root.setLevel(DEBUG) so NOTSET loggers are caught"
            )
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        root.setLevel(original_root)
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)


def test_suppress_noisy_loggers_respects_effective_level_via_parent():
    """A logger at NOTSET inheriting ERROR from root must not be set to WARNING."""
    root = logging.getLogger()
    probe = "httpx"
    original_explicit = logging.getLogger(probe).level
    original_root = root.level
    try:
        logging.getLogger(probe).setLevel(logging.NOTSET)
        root.setLevel(logging.ERROR)
        _suppress_noisy_loggers()
        # Logger must remain NOTSET — effective level (ERROR) was already >= WARNING.
        assert logging.getLogger(probe).level == logging.NOTSET, (
            f"Logger {probe!r} was NOTSET (inheriting ERROR from root); "
            "_suppress_noisy_loggers must not lower it to WARNING"
        )
    finally:
        logging.getLogger(probe).setLevel(original_explicit)
        root.setLevel(original_root)


def test_setup_logging_and_configure_logging_use_same_format(tmp_path):
    """Both functions must produce handlers with identical formatter settings."""
    root = logging.getLogger()
    before = set(root.handlers)
    try:
        for h in root.handlers[:]:
            root.removeHandler(h)

        setup_logging(tmp_path)
        setup_handlers = [h for h in root.handlers if h not in before]
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()

        configure_logging()
        configure_handlers = [h for h in root.handlers if h not in before]

        for h in setup_handlers + configure_handlers:
            assert h.formatter is not None
            assert h.formatter._fmt == _EXPECTED_FMT
            assert h.formatter.datefmt == _EXPECTED_DATEFMT
    finally:
        for h in root.handlers[:]:
            if h not in before:
                root.removeHandler(h)
                h.close()
        for h in before:
            root.addHandler(h)
