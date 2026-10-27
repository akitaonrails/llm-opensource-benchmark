import argparse
import json
import sys
from pathlib import Path

from .engine import load_rates, reconcile
from .parser import parse_timestamp

DEFAULT_RATES = Path(__file__).resolve().parents[2] / "docs" / "rates.json"


def main():
    parser = argparse.ArgumentParser(description="Reconcile payment events")
    parser.add_argument("input")
    parser.add_argument("--since")
    parser.add_argument("--until")
    parser.add_argument("--rates", default=str(DEFAULT_RATES))
    parser.add_argument("--currency", default="USD")
    arguments = parser.parse_args()
    rates = load_rates(arguments.rates)
    if arguments.currency not in rates:
        parser.error(f"unknown currency {arguments.currency}")
    since = parse_timestamp(arguments.since) if arguments.since else None
    until = parse_timestamp(arguments.until) if arguments.until else None
    if arguments.input == "-":
        result = reconcile(sys.stdin, rates, since, until, arguments.currency)
    else:
        with open(arguments.input, encoding="utf-8") as source:
            result = reconcile(source, rates, since, until, arguments.currency)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
