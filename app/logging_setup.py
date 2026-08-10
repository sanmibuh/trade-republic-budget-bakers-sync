from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LOG_FMT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _make_formatter() -> logging.Formatter:
    return logging.Formatter(fmt=_LOG_FMT, datefmt=_DATE_FMT)


def setup_logging(log_dir: Path) -> logging.Logger:
    """Configure root logger: DEBUG to rotating file, INFO to console."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "sync.log"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = _make_formatter()

    # Rotating file: 5 MB per file, keep 5 backups → max 25 MB on disk
    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console: INFO and above, same format
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    root.addHandler(fh)
    root.addHandler(ch)

    return logging.getLogger("sync")


def configure_logging() -> None:
    """Minimal console-only logging setup for entry points without a data dir (e.g. backup CLI)."""
    root = logging.getLogger()
    if root.handlers:
        return  # already configured
    root.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(_make_formatter())
    root.addHandler(ch)
