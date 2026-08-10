from __future__ import annotations

import logging

from app.logging_setup import configure_logging, setup_logging

_EXPECTED_FMT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_EXPECTED_DATEFMT = "%Y-%m-%d %H:%M:%S"


def test_setup_logging_creates_log_file(tmp_path):
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    try:
        logger = setup_logging(tmp_path)
        log_file = tmp_path / "sync.log"
        assert log_file.exists()
        assert logger.name == "sync"
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
