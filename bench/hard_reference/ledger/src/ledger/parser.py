import json
import re
from datetime import datetime
from decimal import Decimal

AMOUNT = re.compile(r"[0-9]+(\.[0-9]+)?")
MINOR_DIGITS = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}
TYPES = {"charge", "refund", "fee", "chargeback"}


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp needs an offset")
    return parsed


def _text(item, key):
    value = item.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a nonempty string")
    return value


def parse_event(line, rates):
    item = json.loads(line)
    if not isinstance(item, dict):
        raise ValueError("not an object")
    kind = _text(item, "type")
    if kind not in TYPES:
        raise ValueError("unknown type")
    currency = _text(item, "currency")
    if currency not in rates:
        raise ValueError("unknown currency")
    amount = item.get("amount")
    if not isinstance(amount, str) or not AMOUNT.fullmatch(amount):
        raise ValueError("bad amount")
    fraction = amount.partition(".")[2]
    if len(fraction) > MINOR_DIGITS.get(currency, 2) or Decimal(amount) <= 0:
        raise ValueError("bad amount")
    if kind == "fee" and currency != "USD":
        raise ValueError("fees must be in USD")
    event = {
        "id": _text(item, "id"), "type": kind, "account": _text(item, "account").lower(),
        "amount": Decimal(amount), "currency": currency, "ts": parse_timestamp(item.get("ts")),
        "test": item.get("test") is True,
    }
    if kind in ("refund", "chargeback"):
        event["charge_id"] = _text(item, "charge_id")
    return event
