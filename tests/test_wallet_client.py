from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.wallet_client import WalletClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client() -> WalletClient:
    return WalletClient(api_key="test-key", base_url="https://example.com/wallet")


def _mock_response(status_code: int, body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# WalletClient.post_records
# ---------------------------------------------------------------------------

def test_post_records_empty_returns_empty():
    client = _make_client()
    assert client.post_records([]) == []


def test_post_records_200_returns_results():
    client = _make_client()
    results = [{"inputIndex": 0, "id": "abc", "success": True}]
    client.session.post = MagicMock(return_value=_mock_response(200, {"results": results}))

    out = client.post_records([{"accountId": "x"}])

    assert out == results
    client.session.post.assert_called_once()
    _, kwargs = client.session.post.call_args
    assert kwargs["params"] == {"returnData": "false"}


def test_post_records_207_returns_results():
    client = _make_client()
    results = [
        {"inputIndex": 0, "id": "abc", "success": True},
        {"inputIndex": 1, "success": False, "error": {"message": "bad"}},
    ]
    client.session.post = MagicMock(return_value=_mock_response(207, {"results": results}))

    out = client.post_records([{}, {}])
    assert out == results


def test_post_records_400_returns_results():
    client = _make_client()
    results = [{"inputIndex": 0, "success": False, "error": {"message": "validation"}}]
    client.session.post = MagicMock(return_value=_mock_response(400, {"results": results}))

    out = client.post_records([{}])
    assert out == results


def test_post_records_500_returns_results():
    client = _make_client()
    results = [{"inputIndex": 0, "success": False, "error": {"message": "internal error"}}]
    client.session.post = MagicMock(return_value=_mock_response(500, {"results": results}))

    out = client.post_records([{}])
    assert out == results


def test_post_records_401_raises():
    client = _make_client()
    resp = MagicMock()
    resp.status_code = 401
    resp.raise_for_status.side_effect = Exception("Unauthorized")
    client.session.post = MagicMock(return_value=resp)

    with pytest.raises(Exception, match="Unauthorized"):
        client.post_records([{}])


def test_post_records_chunks_at_20():
    """21 records must produce exactly 2 POST calls (chunks of 20 + 1)."""
    client = _make_client()

    def _ok_response(request_args, **kwargs):
        chunk = kwargs.get("json") or request_args[1]
        results = [
            {"inputIndex": i, "id": f"id-{i}", "success": True}
            for i in range(len(chunk))
        ]
        return _mock_response(200, {"results": results})

    client.session.post = MagicMock(side_effect=_ok_response)

    records = [{"accountId": f"acc-{i}"} for i in range(21)]
    results = client.post_records(records)

    assert client.session.post.call_count == 2
    assert len(results) == 21
    # inputIndex must be rebased: second chunk item 0 → global index 20
    assert results[20]["inputIndex"] == 20
