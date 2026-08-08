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


# ---------------------------------------------------------------------------
# WalletClient GET methods
# ---------------------------------------------------------------------------

def _mock_get_response(status_code: int, body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body
    resp.raise_for_status = MagicMock()
    return resp


def test_get_accounts_returns_list():
    client = _make_client()
    data = [{"id": "a1", "name": "Cash"}, {"id": "a2", "name": "Portfolio"}]
    client.session.get = MagicMock(return_value=_mock_get_response(200, data))

    result = client.get_accounts()

    assert result == data
    client.session.get.assert_called_once()
    url = client.session.get.call_args.args[0]
    assert url.endswith("/accounts")


def test_get_categories_returns_list():
    client = _make_client()
    data = [{"id": "c1", "name": "Food"}]
    client.session.get = MagicMock(return_value=_mock_get_response(200, data))

    result = client.get_categories()
    assert result == data


def test_get_budgets_returns_list():
    client = _make_client()
    data = [{"id": "b1", "name": "Monthly"}]
    client.session.get = MagicMock(return_value=_mock_get_response(200, data))

    result = client.get_budgets()
    assert result == data


def test_get_labels_returns_list():
    client = _make_client()
    data = [{"id": "l1", "name": "TR"}]
    client.session.get = MagicMock(return_value=_mock_get_response(200, data))

    result = client.get_labels()
    assert result == data


def test_get_records_passes_date_params():
    client = _make_client()
    data = [{"id": "r1", "amount": 100}]
    client.session.get = MagicMock(return_value=_mock_get_response(200, data))

    result = client.get_records("2026-07-01", "2026-07-31")

    assert result == data
    params = client.session.get.call_args.kwargs["params"]
    assert params["recordDate"] == "gte.2026-07-01"
    assert params["recordDateTo"] == "lte.2026-07-31"


def test_get_pagination_follows_next_offset():
    """_get_all should follow nextOffset until exhausted."""
    client = _make_client()
    page1 = {"data": [{"id": "r1"}], "nextOffset": 1}
    page2 = [{"id": "r2"}]  # plain list, no nextOffset → stop

    responses = [
        _mock_get_response(200, page1),
        _mock_get_response(200, page2),
    ]
    client.session.get = MagicMock(side_effect=responses)

    result = client.get_accounts()

    assert client.session.get.call_count == 2
    assert len(result) == 2
    assert result[0]["id"] == "r1"
    assert result[1]["id"] == "r2"


def test_get_raises_on_http_error():
    client = _make_client()
    resp = MagicMock()
    resp.json.return_value = {}
    resp.raise_for_status.side_effect = Exception("403 Forbidden")
    client.session.get = MagicMock(return_value=resp)

    with pytest.raises(Exception, match="403"):
        client.get_accounts()


def test_get_all_dict_page_non_list_appended():
    """When data["data"] is not a list, it should be appended as a single item."""
    client = _make_client()
    # dict response where "data" is a dict (not list) and no nextOffset
    body = {"data": {"id": "singleton"}}
    client.session.get = MagicMock(return_value=_mock_get_response(200, body))

    result = client.get_accounts()

    assert result == [{"id": "singleton"}]


def test_get_all_unexpected_response_type_breaks():
    """Non-list, non-dict response should log a warning and return empty."""
    client = _make_client()
    resp = MagicMock()
    resp.json.return_value = "unexpected string"
    resp.raise_for_status = MagicMock()
    client.session.get = MagicMock(return_value=resp)

    result = client.get_accounts()

    assert result == []


def test_get_all_dict_with_next_offset_paginates():
    """Dict response with nextOffset should fetch next page."""
    client = _make_client()
    page1 = {"data": [{"id": "r1"}], "nextOffset": 42}
    page2 = {"data": [{"id": "r2"}]}  # no nextOffset → stop

    responses = [
        _mock_get_response(200, page1),
        _mock_get_response(200, page2),
    ]
    client.session.get = MagicMock(side_effect=responses)

    result = client.get_accounts()

    assert client.session.get.call_count == 2
    assert [r["id"] for r in result] == ["r1", "r2"]
    # Second call must include offset=42
    second_call_params = client.session.get.call_args_list[1].kwargs["params"]
    assert second_call_params["offset"] == 42
