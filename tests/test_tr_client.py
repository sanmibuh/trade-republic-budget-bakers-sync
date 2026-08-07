from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.tr_client import fetch_timeline_events


# ---------------------------------------------------------------------------
# fetch_timeline_events
# ---------------------------------------------------------------------------

SINCE = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_fetch_timeline_returns_list_directly():
    events = [{"id": "1", "eventType": "INTEREST_PAYMENT"}, {"id": "2"}]
    client = MagicMock()
    client.timeline.return_value = events
    result = fetch_timeline_events(client, since=SINCE)
    assert result == events


def test_fetch_timeline_returns_dict_with_items():
    events = [{"id": "1"}]
    client = MagicMock()
    client.timeline.return_value = {"items": events}
    result = fetch_timeline_events(client, since=SINCE)
    assert result == events


def test_fetch_timeline_returns_dict_with_data():
    events = [{"id": "2"}]
    client = MagicMock()
    client.timeline.return_value = {"data": events}
    result = fetch_timeline_events(client, since=SINCE)
    assert result == events


def test_fetch_timeline_returns_empty_when_none():
    client = MagicMock()
    client.timeline.return_value = None
    result = fetch_timeline_events(client, since=SINCE)
    assert result == []


def test_fetch_timeline_filters_non_dict_items():
    client = MagicMock()
    client.timeline.return_value = [{"id": "ok"}, "not-a-dict", 42, None]
    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "ok"}]


def test_fetch_timeline_falls_back_to_get_timeline():
    client = MagicMock(spec=["get_timeline"])
    client.get_timeline.return_value = [{"id": "fallback"}]
    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "fallback"}]


def test_fetch_timeline_raises_when_no_method_found():
    client = MagicMock(spec=[])  # no methods
    with pytest.raises(RuntimeError, match="No supported timeline method"):
        fetch_timeline_events(client, since=SINCE)


def test_fetch_timeline_skips_type_error_and_tries_next():
    """If a method raises TypeError, the next provider is tried."""
    client = MagicMock()
    client.timeline.side_effect = TypeError("bad args")
    client.get_timeline.return_value = [{"id": "second"}]
    result = fetch_timeline_events(client, since=SINCE)
    assert result == [{"id": "second"}]


def test_fetch_timeline_unknown_return_type_returns_empty():
    client = MagicMock()
    client.timeline.return_value = "unexpected string"
    result = fetch_timeline_events(client, since=SINCE)
    assert result == []
