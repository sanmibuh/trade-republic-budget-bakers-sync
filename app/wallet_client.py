from __future__ import annotations

import logging
from typing import Any

from app.http_client import build_session

log = logging.getLogger(__name__)


class WalletClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://rest.budgetbakers.com/wallet",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._get_base = f"{self.base_url}/v1/api"
        self.session = build_session(
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    _BATCH_SIZE = 20  # API hard limit for POST /v1/api/records

    def post_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """POST /v1/api/records?returnData=false — max 20 records per request.

        Automatically chunks larger inputs into sequential batches of 20 and
        merges the per-item results, adjusting inputIndex so callers always see
        a flat, zero-based index matching their original list.

        Returns the merged results array with per-item fields:
          inputIndex, id, success, error.

        HTTP 200  → all succeeded.
        HTTP 207  → mixed results (check each item).
        HTTP 400  → all failed (client errors).
        HTTP 500  → all failed (server errors).
        All four use the same CreateRecordsResponse schema.
        """
        if not records:
            return []

        all_results: list[dict[str, Any]] = []
        for chunk_start in range(0, len(records), self._BATCH_SIZE):
            chunk = records[chunk_start: chunk_start + self._BATCH_SIZE]
            log.debug(
                "POST /v1/api/records chunk offset=%d size=%d",
                chunk_start, len(chunk),
            )
            response = self.session.post(
                f"{self._get_base}/records",
                params={"returnData": "false"},
                json=chunk,
                timeout=30,
            )
            log.debug("POST /v1/api/records → %s", response.status_code)
            if response.status_code in (200, 207, 400, 500):
                chunk_results = response.json().get("results", [])
                # Re-base inputIndex to the global list position.
                for item in chunk_results:
                    rebased = dict(item)
                    if "inputIndex" in rebased:
                        rebased["inputIndex"] = chunk_start + rebased["inputIndex"]
                    all_results.append(rebased)
            else:
                response.raise_for_status()

        return all_results

    # ------------------------------------------------------------------
    # GET methods — Wallet backup API
    # ------------------------------------------------------------------

    def _get_all(self, resource: str, params: dict[str, Any] | None = None) -> list[dict]:
        """Fetch all pages for a given resource, following nextOffset pagination."""
        results: list[dict] = []
        offset: int | None = None
        base_params = dict(params or {})

        while True:
            req_params = dict(base_params)
            if offset is not None:
                req_params["offset"] = offset

            log.debug("GET %s offset=%s", resource, offset)
            response = self.session.get(
                f"{self._get_base}/{resource}",
                params=req_params,
                timeout=30,
            )
            log.debug("GET %s → %s", resource, response.status_code)
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                results.extend(data)
                break
            if isinstance(data, dict):
                offset = self._collect_page(resource, data, results)
                if not offset:
                    break
            else:
                log.warning("GET %s: unexpected response type %s", resource, type(data))
                break

        return results

    @staticmethod
    def _collect_page(resource: str, data: dict, results: list[dict]) -> int | None:
        """Append items from a paginated dict response into results. Returns next offset or None."""
        page = data.get(resource, [])
        if isinstance(page, list):
            results.extend(page)
        else:
            results.append(page)
        return data.get("nextOffset") or None

    def get_accounts(self) -> list[dict]:
        return self._get_all("accounts")

    def get_categories(self) -> list[dict]:
        return self._get_all("categories")

    def get_budgets(self) -> list[dict]:
        return self._get_all("budgets")

    def get_labels(self) -> list[dict]:
        return self._get_all("labels")

    def get_records(self, date_from: str, date_to: str) -> list[dict]:
        """Fetch all records within [date_from, date_to] (inclusive, YYYY-MM-DD).

        The API supports repeated `recordDate` params with AND logic:
        recordDate=gte.<from>&recordDate=lte.<to>
        """
        return self._get_all(
            "records",
            params={
                "recordDate": [f"gte.{date_from}", f"lte.{date_to}"],
            },
        )
