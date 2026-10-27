import json

from .parser import iter_events


def load_rates(path):
    with open(path, encoding="utf-8") as source:
        return {code: float(rate) for code, rate in json.load(source).items()}


def reconcile(lines, rates, since=None, until=None):
    balances = {}
    charges = {}
    rejected = 0
    for number, event in iter_events(lines):
        if since is not None and event["ts"] < since:
            continue
        if until is not None and event["ts"] > until:
            continue
        usd = round(event["amount"] * rates[event["currency"]], 2)
        if event["type"] == "charge":
            charges[event["id"]] = event
            balances[event["account"]] = balances.get(event["account"], 0) + usd
        elif event["type"] == "fee":
            balances[event["account"]] = balances.get(event["account"], 0) - usd
        elif event["type"] == "refund":
            charge = charges.get(event["charge_id"])
            if charge is None:
                rejected += 1
                continue
            balances[charge["account"]] = balances.get(charge["account"], 0) - usd
    accounts = {name: f"{value:.2f}" for name, value in balances.items()}
    return {"accounts": accounts, "rejected": rejected, "total": f"{sum(balances.values()):.2f}"}
