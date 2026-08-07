from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        normalized = value.replace(",", ".").replace("€", "").replace(" ", "").strip()
        if normalized == "":
            return Decimal("0")
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return Decimal("0")
    return Decimal("0")


def _get_first_match(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def extract_amount(event: dict[str, Any], *keys: str) -> Decimal:
    value = _get_first_match(event, *keys)
    # TR sends amount as {"value": 100.0, "currency": "EUR"} — unwrap it.
    if isinstance(value, dict):
        value = _get_first_match(value, "value", "amount", "gross")
    elif value is None and isinstance(event.get("amount"), dict):
        value = _get_first_match(event["amount"], *keys)
    return _to_decimal(value)


def normalize_event_time(event: dict[str, Any]) -> str:
    for key in ("timestamp", "createdAt", "created_at", "date", "recordDate"):
        value = event.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value.isoformat()
        s = str(value)
        # Normalize numeric TZ offset without colon (+0000 / -0500 → +00:00 / -05:00)
        # so the date is always valid ISO 8601 (required by BudgetBakers API).
        s = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', s)
        return s
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Event → record payload builder (pure, no HTTP)
# ---------------------------------------------------------------------------

_EVENT_TITLES: dict[str, str] = {
    "INTEREST_PAYOUT": "Interest Payout",
    "INTEREST_PAYMENT": "Interest Payment",
    "SPARE_CHANGE_AGGREGATE": "Round-up Investment",
    "SAVEBACK_AGGREGATE": "Saveback",
    "BUY_ORDER": "Buy Order",
    "SELL_ORDER": "Sell Order",
    "TRADING_SAVINGSPLAN_EXECUTED": "Savings Plan",
    "CARD_TRANSACTION": "Card Transaction",
    "CARD_VERIFICATION": "Card Verification",
    "PAYMENT_INBOUND": "Payment Inbound",
    "BANK_TRANSACTION_INCOMING": "Bank Transfer In",
    "BANK_TRANSACTION_OUTGOING": "Bank Transfer Out",
}


_INTEREST_TYPES = {"INTEREST_PAYOUT", "INTEREST_PAYMENT"}


def _event_note(event: dict[str, Any], event_type: str) -> str:
    """Return a human-readable note for the event.

    Default: TR's own title (fallback to mapped title, then event_type).
    INTEREST_PAYOUT / INTEREST_PAYMENT: "<MappedTitle>: <TR title>".
    """
    tr_title = str(_get_first_match(event, "title", "name", "description") or "").strip()
    mapped = _EVENT_TITLES.get(event_type, "")
    if event_type in _INTEREST_TYPES and mapped and tr_title:
        return f"{mapped}: {tr_title}"
    return tr_title or mapped or event_type or "Trade Republic event"

def build_records_for_event(
    event: dict[str, Any],
    *,
    cash_account_id: str,
    portfolio_account_id: str,
) -> list[dict[str, Any]]:
    """Convert a TR event into one or more BudgetBakers record dicts.

    Returns a list ready to be included in a POST /v1/api/records batch.
    Makes no HTTP calls.
    """
    event_type = str(_get_first_match(event, "eventType", "type", "event_type") or "").upper()
    record_date = normalize_event_time(event)
    note = _event_note(event, event_type)
    amount = extract_amount(event, "amount", "value", "grossAmount", "gross", "total")
    tax = extract_amount(event, "tax", "taxAmount", "withholdingTax")

    log.debug("Building record(s) for event type=%s amount=%s date=%s — raw: %s", event_type, amount, record_date, event)
    if amount == 0:
        log.debug("Skipping zero-amount event (type=%s) — raw event: %s", event_type, event)
        return []

    def _rec(
        account_id: str,
        amt: Decimal,
        note_: str,
        transfer_account_id: str | None = None,
        payment_type: str | None = None,
        counter_party: str | None = None,
        unpaired_transfer: bool = False,
    ) -> dict[str, Any]:
        r: dict[str, Any] = {
            "accountId": account_id,
            "amount": {"value": float(amt)},
            "recordDate": record_date,
            "note": note_,
            "paymentType": payment_type or ("transfer" if transfer_account_id else "web_payment"),
        }
        if transfer_account_id:
            r["transfer"] = {"pairingMode": "new", "accountId": transfer_account_id}
        elif unpaired_transfer:
            r["transfer"] = {"pairingMode": "unpaired"}
        if counter_party:
            r["counterParty"] = counter_party[:255]
        return r

    if event_type == "INTEREST_PAYMENT":
        return [_rec(cash_account_id, amount, note)]

    if event_type == "INTEREST_PAYOUT":
        return [_rec(cash_account_id, amount, note)]

    if event_type in {"BUY_ORDER", "SAVINGS_PLAN", "SELL_ORDER", "TRADING_SAVINGSPLAN_EXECUTED", "SAVEBACK_AGGREGATE", "SPARE_CHANGE_AGGREGATE"}:
        return [_rec(cash_account_id, amount, note, transfer_account_id=portfolio_account_id)]

    if event_type == "SAVEBACK":
        return [_rec(portfolio_account_id, amount, note)]

    if event_type == "CARD_TRANSACTION":
        return [_rec(cash_account_id, amount, note, payment_type="debit_card")]

    if event_type in {"BANK_TRANSACTION_INCOMING", "BANK_TRANSACTION_OUTGOING"}:
        counter_party = str(event.get("subtitle") or "").strip() or None
        tr_title = str(_get_first_match(event, "title", "name", "description") or "").strip()
        direction = "From" if event_type == "BANK_TRANSACTION_INCOMING" else "To"
        transfer_note = f"{direction}: {tr_title}" if tr_title else note
        return [_rec(
            cash_account_id, amount, transfer_note,
            payment_type="transfer",
            counter_party=counter_party,
            unpaired_transfer=True,
        )]

    # Default: cash account
    return [_rec(cash_account_id, amount, note)]
