from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests


class WalletClient:
    def __init__(self, api_key: str, base_url: str = "https://api.budgetbakers.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def post_record(
        self,
        *,
        account_id: str,
        amount: Decimal,
        event_time: str,
        note: str,
        tx_type: str | None = None,
        category: str | None = None,
        transfer_account_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "accountId": account_id,
            "amount": float(amount),
            "date": event_time,
            "note": note,
        }
        if tx_type:
            payload["type"] = tx_type
        if category:
            payload["categoryName"] = category
        if transfer_account_id:
            payload["transferAccountId"] = transfer_account_id

        response = self.session.post(f"{self.base_url}/api/v1/records", json=payload, timeout=30)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()


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
    if value is None and isinstance(event.get("amount"), dict):
        value = _get_first_match(event["amount"], *keys)
    return _to_decimal(value)


def normalize_event_time(event: dict[str, Any]) -> str:
    for key in ("timestamp", "createdAt", "created_at", "date"):
        value = event.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
    return datetime.now(timezone.utc).isoformat()


def sync_event_to_wallet(
    wallet_client: WalletClient,
    event: dict[str, Any],
    *,
    cash_account_id: str,
    portfolio_account_id: str,
) -> None:
    event_type = str(_get_first_match(event, "eventType", "type", "event_type") or "").upper()
    event_time = normalize_event_time(event)
    event_label = str(_get_first_match(event, "title", "name", "description") or event_type or "Trade Republic event")

    amount = extract_amount(event, "amount", "value", "grossAmount", "gross", "total")
    tax = extract_amount(event, "tax", "taxAmount", "withholdingTax")

    if event_type == "INTEREST_PAYMENT":
        wallet_client.post_record(
            account_id=cash_account_id,
            amount=amount,
            event_time=event_time,
            note=event_label,
            tx_type="income",
            category="Interests",
        )
        if tax > 0:
            wallet_client.post_record(
                account_id=cash_account_id,
                amount=-abs(tax),
                event_time=event_time,
                note=f"{event_label} tax",
                tx_type="expense",
                category="Taxes",
            )
        return

    if event_type in {"BUY_ORDER", "SAVINGS_PLAN", "SELL_ORDER"}:
        wallet_client.post_record(
            account_id=cash_account_id,
            amount=amount,
            event_time=event_time,
            note=event_label,
            transfer_account_id=portfolio_account_id,
        )
        return

    if event_type == "SAVEBACK":
        wallet_client.post_record(
            account_id=portfolio_account_id,
            amount=amount,
            event_time=event_time,
            note=event_label,
            tx_type="income",
            category="Cashback / Bonuses",
        )
        if tax > 0:
            wallet_client.post_record(
                account_id=portfolio_account_id,
                amount=-abs(tax),
                event_time=event_time,
                note=f"{event_label} tax",
                tx_type="expense",
                category="Taxes",
            )
        return

    wallet_client.post_record(
        account_id=cash_account_id,
        amount=amount,
        event_time=event_time,
        note=event_label,
    )
