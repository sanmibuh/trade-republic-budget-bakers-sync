from __future__ import annotations

import logging

from app.logging_setup import _suppress_noisy_loggers, configure_logging, setup_logging

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


_NOISY_LOGGERS = ("httpx", "telegram", "hpack")


def test_setup_logging_suppresses_noisy_library_loggers(tmp_path):
    """httpx / telegram / hpack loggers must be set to WARNING after setup_logging."""
    root = logging.getLogger()
    before = set(root.handlers)
    original_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
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
    """httpx / telegram / hpack loggers must be set to WARNING after configure_logging."""
    root = logging.getLogger()
    before = set(root.handlers)
    original_levels = {name: logging.getLogger(name).level for name in _NOISY_LOGGERS}
    try:
        for h in root.handlers[:]:
            root.removeHandler(h)
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
