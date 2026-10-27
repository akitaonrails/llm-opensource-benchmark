import argparse
import json
import sys

from .parser import iter_records, parse_timestamp
from .stats import summarize


def main():
    argument_parser = argparse.ArgumentParser(description="Summarize a JSONL request log")
    argument_parser.add_argument("input", help="JSONL path, or - for standard input")
    argument_parser.add_argument("--since")
    argument_parser.add_argument("--until")
    arguments = argument_parser.parse_args()

    since = parse_timestamp(arguments.since) if arguments.since else None
    until = parse_timestamp(arguments.until) if arguments.until else None
    if arguments.input == "-":
        summary = summarize(iter_records(sys.stdin), since=since, until=until)
    else:
        with open(arguments.input, encoding="utf-8") as source:
            summary = summarize(iter_records(source), since=since, until=until)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
