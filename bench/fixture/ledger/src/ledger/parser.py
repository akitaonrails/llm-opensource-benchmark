import json
import sys
from datetime import datetime


def parse_timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return parsed


def iter_events(lines):
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            yield number, {
                "id": item["id"],
                "type": item["type"],
                "account": item["account"],
                "amount": float(item["amount"]),
                "currency": item["currency"],
                "ts": parse_timestamp(item["ts"]),
                "charge_id": item.get("charge_id"),
            }
        except (json.JSONDecodeError, KeyError, ValueError) as error:
            print(f"warning: line {number}: {error}", file=sys.stderr)
