from __future__ import annotations

import logging
from typing import Any

import urllib3
import requests

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

log = logging.getLogger(__name__)


class WalletClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://rest.budgetbakers.com/wallet",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        self.session.headers.update(
            {
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
                f"{self.base_url}/v1/api/records",
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
