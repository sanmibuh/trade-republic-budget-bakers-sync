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
# Details extraction helpers
# ---------------------------------------------------------------------------

def _resolve_detail_text(detail: dict[str, Any]) -> str | None:
    """Extract display text from a detail dict, preferring displayValue.text."""
    dv = detail.get("displayValue")
    if isinstance(dv, dict) and dv.get("text"):
        return dv["text"]
    return detail.get("text") or None


def _extract_detail_row(details: dict[str, Any], row_title: str) -> str | None:
    """Return the display text of the first table row matching row_title in a details payload."""
    for section in details.get("sections", []):
        data = section.get("data")
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict) or item.get("title") != row_title:
                continue
            detail = item.get("detail")
            if isinstance(detail, dict):
                return _resolve_detail_text(detail)
    return None


def _resolve_iban(item: dict[str, Any]) -> str | None:
    """Extract full or masked IBAN from a detail item."""
    try:
        full_iban: str = (
            item["detail"]["action"]["payload"]
            ["sections"][0]["data"][0]["title"]
        )
        return full_iban.replace(" ", "")
    except (KeyError, IndexError, TypeError):
        return (item.get("detail") or {}).get("text") or None


def _extract_iban_from_details(details: dict[str, Any]) -> str | None:
    """Return the full IBAN of the counterparty from a timeline detail payload.

    TR buries the full IBAN inside a nested infoPage action:
        sections[N].data[M].title == "IBAN"
        sections[N].data[M].detail.action.payload.sections[0].data[0].title
            == "ES86 0182 5297 2402 0031 7648"

    Falls back to the masked text (e.g. "..7648") if the deep path is absent.
    Returns None if no IBAN row is found.
    """
    for section in details.get("sections", []):
        data = section.get("data")
        if not isinstance(data, list):
            continue
        for item in data:
            if isinstance(item, dict) and item.get("title") == "IBAN":
                return _resolve_iban(item)
    return None


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

_PREFIXED_TYPES: frozenset[str] = frozenset({"INTEREST_PAYOUT", "INTEREST_PAYMENT", "SAVEBACK_AGGREGATE", "SPARE_CHANGE_AGGREGATE", "TRADING_SAVINGSPLAN_EXECUTED"})


def _event_note(event: dict[str, Any], event_type: str) -> str:
    tr_title = str(_get_first_match(event, "title", "name", "description") or "").strip()
    mapped = _EVENT_TITLES.get(event_type, "")
    if event_type in _PREFIXED_TYPES and mapped and tr_title:
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
    label_ids: list[str] | None = None,
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
    if label_ids:
        r["labelIds"] = label_ids
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


def _handle_investment(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    """Transfer to portfolio with Transaktion (units × price) appended to note."""
    details = event.get("details") or {}
    txn = _extract_detail_row(details, "Transaktion")
    full_note = f"{note} · {txn}" if txn else note
    return [_make_record(cash_account_id, amount, full_note, record_date, transfer_account_id=portfolio_account_id)]


def _handle_saveback(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    return [_make_record(portfolio_account_id, amount, note, record_date)]


def _gross_tax_note(gross: str | None, tax: str | None) -> str | None:
    """Return a 'gross X, tax Y' fragment, or None if no gross is available."""
    if gross and tax:
        return f"gross {gross}, tax {tax}"
    if gross:
        return f"gross {gross}"
    return None


def _handle_saveback_aggregate(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    """Transfer to portfolio with Transaktion + gross + tax appended to note."""
    details = event.get("details") or {}
    parts = [p for p in [
        _extract_detail_row(details, "Transaktion"),
        _gross_tax_note(_extract_detail_row(details, "Angefallen"), _extract_detail_row(details, "Steuern")),
    ] if p]
    full_note = f"{note} · {' · '.join(parts)}" if parts else note
    return [_make_record(cash_account_id, amount, full_note, record_date, transfer_account_id=portfolio_account_id)]


def _handle_interest(
    event: dict[str, Any], amount: Decimal, note: str, record_date: str,
    cash_account_id: str, portfolio_account_id: str,
) -> list[dict[str, Any]]:
    """Cash credit with gross accrued + tax withheld appended to note."""
    details = event.get("details") or {}
    gt = _gross_tax_note(_extract_detail_row(details, "Angesammelt"), _extract_detail_row(details, "Steuern"))
    full_note = f"{note} · {gt}" if gt else note
    return [_make_record(cash_account_id, amount, full_note, record_date)]


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
    tr_title = str(_get_first_match(event, "title", "name", "description") or "").strip()
    direction = "From" if event_type == "BANK_TRANSACTION_INCOMING" else "To"
    transfer_note = f"{direction}: {tr_title}" if tr_title else note

    # Prefer full IBAN from details; fall back to counterparty name
    details = event.get("details") or {}
    iban = _extract_iban_from_details(details)
    counter_party = iban or tr_title or None

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
    "INTEREST_PAYMENT":                         _handle_interest,
    "INTEREST_PAYOUT":                          _handle_interest,
    "PAYMENT_INBOUND":                          _handle_cash,
    "BUY_ORDER":                                _handle_transfer_to_portfolio,
    "SELL_ORDER":                               _handle_transfer_to_portfolio,
    "SAVINGS_PLAN":                             _handle_transfer_to_portfolio,
    "TRADING_SAVINGSPLAN_EXECUTED":             _handle_investment,
    "SAVEBACK_AGGREGATE":                       _handle_saveback_aggregate,
    "SPARE_CHANGE_AGGREGATE":                   _handle_investment,
    "SAVEBACK":                                 _handle_saveback,
    "CARD_TRANSACTION":                         _handle_card,
    "BANK_TRANSACTION_INCOMING":                _handle_bank_transaction,
    "BANK_TRANSACTION_OUTGOING":                _handle_bank_transaction,
}

# These event types are always zero-amount (document-only or verification events).
# They are excluded from KNOWN_EVENT_TYPES so they don't trigger unknown-type warnings,
# but no handler is needed since build_records_for_event short-circuits on zero amount.
_ZERO_AMOUNT_TYPES: frozenset[str] = frozenset({
    "CARD_VERIFICATION",
    "QUARTERLY_NET_WORTH_STATEMENT_CREATED",
    "EX_POST_COST_REPORT_CREATED",
})

KNOWN_EVENT_TYPES: frozenset[str] = frozenset(_HANDLERS) | _ZERO_AMOUNT_TYPES


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_records_for_event(
    event: dict[str, Any],
    *,
    cash_account_id: str,
    portfolio_account_id: str,
    label_ids: dict[str, str] | None = None,
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

    records = handler(event, amount, note, record_date, cash_account_id, portfolio_account_id)

    label_id = (label_ids or {}).get(event_type)
    if label_id:
        for record in records:
            record["labelIds"] = [label_id]

    return records
