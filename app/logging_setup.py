from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LOG_FMT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def _make_formatter() -> logging.Formatter:
    return logging.Formatter(fmt=_LOG_FMT, datefmt=_DATE_FMT)


def setup_logging(log_dir: Path) -> None:
    """Configure root logger: DEBUG to rotating file, INFO to console.

    Called once at process startup — not per-run.  All services (sync, backup,
    bot) share the same log directory so that ``{DATA_DIR}/logs/sync.log``
    receives output from every in-process call without handler lifecycle
    management.

    Idempotent: if a ``RotatingFileHandler`` for ``log_dir/sync.log`` is already
    attached to the root logger, the function returns early without adding
    duplicate handlers.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "sync.log"

    root = logging.getLogger()

    # Guard against duplicate handlers when called more than once in the same
    # process (e.g. during testing or if a CLI entry point calls it twice).
    if any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and Path(h.baseFilename) == log_file.resolve()
        for h in root.handlers
    ):
        return

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


def configure_logging() -> None:
    """Minimal console-only logging setup for entry points that have no data dir.

    Intended as a fallback for contexts where ``setup_logging`` cannot be called
    (e.g. library use or tests).  All current CLI commands use ``setup_logging``
    with a resolved data directory instead.
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured
    root.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(_make_formatter())
    root.addHandler(ch)
