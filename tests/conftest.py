"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auto_init_db(monkeypatch):
    """Wrap EventRepository.__init__ to call init_db automatically in tests.

    Production code no longer calls DDL inside EventRepository.__init__ —
    init_db() must be called once at process startup before any repository is
    opened.  In tests, most fixtures create a repository directly without
    going through the full startup path.  This autouse fixture restores the
    expected behaviour transparently so individual tests do not need to call
    init_db() explicitly.

    Tests that want to assert on the init_db() call itself (e.g.
    test_init_db_*) are not affected because they call init_db() directly
    and never rely on EventRepository.__init__ performing initialisation.
    """
    from app import persistence
    from app.persistence import init_db

    original_init = persistence.EventRepository.__init__

    def _patched_init(self, db_path, instance="", **kwargs):
        init_db(db_path)
        original_init(self, db_path, instance, **kwargs)

    monkeypatch.setattr(persistence.EventRepository, "__init__", _patched_init)
