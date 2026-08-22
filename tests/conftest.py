"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

import pytest


@pytest.fixture
def initialized_db(tmp_path):
    """Return a path to a freshly initialized SQLite database.

    Calls ``init_db`` explicitly so the schema is ready before any
    ``EventRepository`` is opened.  Tests that need a pre-initialized DB
    can request this fixture instead of calling ``init_db`` inline.
    """
    from app.persistence import init_db

    path = tmp_path / "test.db"
    init_db(path)
    return path
