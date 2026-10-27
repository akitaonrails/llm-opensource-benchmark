import json
import sys
from datetime import datetime


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp needs a timezone")
    return parsed


def _integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected an integer")
    if not minimum <= value <= maximum:
        raise ValueError("integer outside accepted range")
    return value


def iter_records(lines):
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if not isinstance(item["path"], str):
                raise ValueError("path must be a string")
            yield {
                "timestamp": parse_timestamp(item["timestamp"]),
                "path": item["path"],
                "status": _integer(item["status"], 100, 599),
                "bytes": _integer(item["bytes"], 0, 2**63 - 1),
                "latency_ms": _integer(item["latency_ms"], 0, 60000),
            }
        except (json.JSONDecodeError, KeyError, ValueError) as error:
            print(f"warning: skipped line {line_number}: {error}", file=sys.stderr)
