import json
import sys
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from .money import convert
from .parser import parse_event

CENT = Decimal("0.01")


def load_rates(path):
    with open(path, encoding="utf-8") as source:
        return {code: Decimal(rate) for code, rate in json.load(source).items()}


def _money(value):
    value = value.quantize(CENT, rounding=ROUND_HALF_EVEN)
    return "0.00" if value == 0 else f"{value:.2f}"


def reconcile(lines, rates, since=None, until=None, currency="USD"):
    if currency not in rates:
        raise ValueError(f"unknown currency {currency}")
    rejected = 0

    def warn(number, reason):
        nonlocal rejected
        rejected += 1
        print(f"warning: line {number}: {reason}", file=sys.stderr)

    seen, accepted = set(), []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            event = parse_event(line, rates)
        except (ValueError, TypeError, KeyError) as error:
            warn(number, error)
            continue
        if event["id"] in seen:
            warn(number, "duplicate id")
            continue
        seen.add(event["id"])
        if not event["test"]:
            accepted.append((number, event))

    charges = {event["id"]: event for _, event in accepted if event["type"] == "charge"}
    linked = sorted((item for item in accepted if item[1]["type"] in ("refund", "chargeback")),
                    key=lambda item: (item[1]["ts"], item[0]))
    used, chargebacks, rejected_numbers = {}, set(), set()
    for number, event in linked:
        charge = charges.get(event["charge_id"])
        if charge is None or charge["currency"] != event["currency"] or charge["account"] != event["account"]:
            warn(number, "refund does not match a charge")
            rejected_numbers.add(number)
            continue
        spent = used.get(charge["id"], Decimal(0))
        if event["type"] == "chargeback":
            if charge["id"] in chargebacks or event["amount"] > charge["amount"]:
                warn(number, "chargeback rejected")
                rejected_numbers.add(number)
                continue
            chargebacks.add(charge["id"])
            used[charge["id"]] = min(charge["amount"], spent + event["amount"])
        else:
            if spent + event["amount"] > charge["amount"]:
                warn(number, "refund exceeds charge")
                rejected_numbers.add(number)
                continue
            used[charge["id"]] = spent + event["amount"]

    sums = {}
    for number, event in accepted:
        if number in rejected_numbers:
            continue
        if (since is not None and event["ts"] < since) or (until is not None and event["ts"] >= until):
            continue
        sign = 1 if event["type"] == "charge" else -1
        per_currency = sums.setdefault(event["account"], {})
        per_currency[event["currency"]] = per_currency.get(event["currency"], Decimal(0)) + sign * event["amount"]
    accounts = {}
    with localcontext() as context:
        context.prec = 60
        for account, per_currency in sums.items():
            total = sum((convert(amount, code, currency, rates) for code, amount in per_currency.items()), Decimal(0))
            accounts[account] = total.quantize(CENT, rounding=ROUND_HALF_EVEN)
    total = sum((value for name, value in accounts.items() if not name.startswith("house_")), Decimal(0))
    return {"accounts": {name: _money(accounts[name]) for name in sorted(accounts)},
            "rejected": rejected, "total": _money(total)}
