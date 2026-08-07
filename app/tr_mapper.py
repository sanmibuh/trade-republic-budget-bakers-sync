from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Time normalisation
# ---------------------------------------------------------------------------

def normalize_event_time(event: dict[str, Any]) -> str:
    for key in ("timestamp", "createdAt", "created_at", "date", "recordDate"):
        value = event.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value.isoformat()
        s = str(value)
        s = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', s)
        return s
    return datetime.now(timezone.utc).isoformat()


def filter_by_lookback(events: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    filtered = []
    for event in events:
        event_time = normalize_event_time(event)
        parsed = None
        try:
            parsed = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is not None and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed is None or parsed >= since:
            filtered.append(event)
    return filtered


# ---------------------------------------------------------------------------
# Amount extraction
# ---------------------------------------------------------------------------

def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        normalized = value.replace(",", ".").replace("€", "").replace(" ", "").strip()
        if normalized == "":
            return Decimal(0)
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return Decimal(0)
    return Decimal(0)


def _get_first_match(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def extract_amount(event: dict[str, Any], *keys: str) -> Decimal:
    value = _get_first_match(event, *keys)
    if isinstance(value, dict):
        value = _get_first_match(value, "value", "amount", "gross")
    elif value is None and isinstance(event.get("amount"), dict):
        value = _get_first_match(event["amount"], *keys)
    return _to_decimal(value)


# ---------------------------------------------------------------------------
# Record builder helpers
# ---------------------------------------------------------------------------

_EVENT_TITLES: dict[str, str] = {
    "INTEREST_PAYOUT": "Interest Payout",
    "INTEREST_PAYMENT": "Interest Payment",
    "SPARE_CHANGE_AGGREGATE": "Round-up Investment",
    "SAVEBACK_AGGREGATE": "Saveback",
    "BUY_ORDER": "Buy Order",
    "SELL_ORDER": "Sell Order",
    "SAVINGS_PLAN": "Savings Plan",
    "TRADING_SAVINGSPLAN_EXECUTED": "Savings Plan",
    "CARD_TRANSACTION": "Card Transaction",
    "CARD_VERIFICATION": "Card Verification",
    "PAYMENT_INBOUND": "Payment Inbound",
    "BANK_TRANSACTION_INCOMING": "Bank Transfer In",
    "BANK_TRANSACTION_OUTGOING": "Bank Transfer Out",
}

_INTEREST_TYPES = {"INTEREST_PAYOUT", "INTEREST_PAYMENT"}


def _event_note(event: dict[str, Any], event_type: str) -> str:
    tr_title = str(_get_first_match(event, "title", "name", "description") or "").strip()
    mapped = _EVENT_TITLES.get(event_type, "")
    if event_type in _INTEREST_TYPES and mapped and tr_title:
        return f"{mapped}: {tr_title}"
    return tr_title or mapped or event_type or "Trade Republic event"


def _make_record(
    account_id: str,
    amount: Decimal,
    note: str,
    record_date: str,
    *,
    transfer_account_id: str | None = None,
    payment_type: str | None = None,
    counter_party: str | None = None,
    unpaired_transfer: bool = False,
) -> dict[str, Any]:
    r: dict[str, Any] = {
        "accountId": account_id,
        "amount": {"value": float(amount)},
        "recordDate": record_date,
        "note": note,
        "paymentType": payment_type or ("transfer" if transfer_account_id else "web_payment"),
    }
    if transfer_account_id:
        r["transfer"] = {"pairingMode": "new", "accountId": transfer_account_id}
    elif unpaired_transfer:
        r["transfer"] = {"pairingMode": "unpaired"}
    if counter_party:
        r["counterParty"] = counter_party[:255]
    return r


# ---------------------------------------------------------------------------
# Event type handlers (one function per behaviour)
# ---------------------------------------------------------------------------

def _handle_cash(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    return [_make_record(cash_account_id, amount, note, record_date)]


def _handle_transfer_to_portfolio(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    return [_make_record(cash_account_id, amount, note, record_date, transfer_account_id=portfolio_account_id)]


def _handle_saveback(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    return [_make_record(portfolio_account_id, amount, note, record_date)]


def _handle_card(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    return [_make_record(cash_account_id, amount, note, record_date, payment_type="debit_card")]


def _handle_bank_transaction(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    event_type = str(_get_first_match(event, "eventType", "type", "event_type") or "").upper()
    counter_party = str(event.get("subtitle") or "").strip() or None
    tr_title = str(_get_first_match(event, "title", "name", "description") or "").strip()
    direction = "From" if event_type == "BANK_TRANSACTION_INCOMING" else "To"
    transfer_note = f"{direction}: {tr_title}" if tr_title else note
    return [_make_record(
        cash_account_id, amount, transfer_note, record_date,
        payment_type="transfer",
        counter_party=counter_party,
        unpaired_transfer=True,
    )]


# ---------------------------------------------------------------------------
# Registry: event_type → handler
# Adding a new event type = add one line here + one handler function above.
# ---------------------------------------------------------------------------

_EventHandler = Callable[
    [dict[str, Any], Decimal, str, str, str, str],
    list[dict[str, Any]],
]

_HANDLERS: dict[str, _EventHandler] = {
    "INTEREST_PAYMENT":             _handle_cash,
    "INTEREST_PAYOUT":              _handle_cash,
    "PAYMENT_INBOUND":              _handle_cash,
    "BUY_ORDER":                    _handle_transfer_to_portfolio,
    "SELL_ORDER":                   _handle_transfer_to_portfolio,
    "SAVINGS_PLAN":                 _handle_transfer_to_portfolio,
    "TRADING_SAVINGSPLAN_EXECUTED": _handle_transfer_to_portfolio,
    "SAVEBACK_AGGREGATE":           _handle_transfer_to_portfolio,
    "SPARE_CHANGE_AGGREGATE":       _handle_transfer_to_portfolio,
    "SAVEBACK":                     _handle_saveback,
    "CARD_TRANSACTION":             _handle_card,
    "CARD_VERIFICATION":            _handle_cash,   # always zero-amount; handler never reached
    "BANK_TRANSACTION_INCOMING":    _handle_bank_transaction,
    "BANK_TRANSACTION_OUTGOING":    _handle_bank_transaction,
}

KNOWN_EVENT_TYPES: frozenset[str] = frozenset(_HANDLERS)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

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
    amount = extract_amount(event, "amount", "value", "grossAmount", "gross", "total")

    log.debug("Building record(s) for event type=%s amount=%s — raw: %s", event_type, amount, event)
    if amount == 0:
        log.debug("Skipping zero-amount event (type=%s)", event_type)
        return []

    record_date = normalize_event_time(event)
    note = _event_note(event, event_type)
    handler = _HANDLERS.get(event_type)
    if handler is None:
        log.warning("Unknown TR event type %r — falling back to cash handler", event_type)
        handler = _handle_cash
    return handler(event, amount, note, record_date, cash_account_id, portfolio_account_id)
