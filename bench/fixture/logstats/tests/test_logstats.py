import io
import contextlib
import json
import subprocess
import sys
import unittest
from pathlib import Path

from src.logstats.parser import iter_records, parse_timestamp
from src.logstats.stats import summarize


ROOT = Path(__file__).resolve().parents[1]


def record(timestamp="2025-01-01T00:00:00Z", status=200, size=100, latency=10, path="/api"):
    return {
        "timestamp": parse_timestamp(timestamp),
        "path": path,
        "status": status,
        "bytes": size,
        "latency_ms": latency,
    }


class LogstatsTests(unittest.TestCase):
    def test_basic_summary(self):
        result = summarize([record(status=503, size=40, latency=12)])
        self.assertEqual(result, {"count": 1, "bytes": 40, "error_rate": 1.0, "p95_ms": 12})

    def test_malformed_json_is_skipped_with_warning(self):
        lines = io.StringIO('{bad json}\n{"timestamp":"2025-01-01T00:00:00Z","path":"/","status":200,"bytes":3,"latency_ms":4}\n')
        with contextlib.redirect_stderr(io.StringIO()) as captured:
            self.assertEqual(len(list(iter_records(lines))), 1)
        self.assertIn("warning", captured.getvalue())

    def test_timezone_offsets_compare_as_instants(self):
        early = record(timestamp="2025-01-01T01:00:00+01:00")
        later = record(timestamp="2025-01-01T00:30:00Z")
        result = summarize([early, later], since=parse_timestamp("2025-01-01T00:15:00Z"))
        self.assertEqual(result["count"], 1)

    def test_empty_input_has_zero_error_rate(self):
        result = summarize([])
        self.assertEqual(result, {"count": 0, "bytes": 0, "error_rate": 0.0, "p95_ms": None})

    def test_until_is_exclusive(self):
        cutoff = parse_timestamp("2025-01-01T00:00:00Z")
        result = summarize([record(), record(timestamp="2025-01-01T00:00:01Z")], until=cutoff)
        self.assertEqual(result["count"], 0)

    def test_cli_key_order_is_deterministic(self):
        completed = subprocess.run(
            [sys.executable, "-m", "src.logstats.cli", "-"],
            cwd=ROOT,
            input="",
            text=True,
            capture_output=True,
            check=True,
        )
        pairs = json.loads(completed.stdout, object_pairs_hook=list)
        self.assertEqual([key for key, _ in pairs], ["bytes", "count", "error_rate", "p95_ms"])


if __name__ == "__main__":
    unittest.main()
