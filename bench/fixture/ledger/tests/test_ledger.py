import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

from src.ledger.engine import load_rates, reconcile

ROOT = Path(__file__).resolve().parents[1]
RATES = load_rates(ROOT / "docs" / "rates.json")


def event(event_id, kind, account, amount, currency="USD", ts="2025-03-01T00:00:00Z", **extra):
    return json.dumps({"id": event_id, "type": kind, "account": account, "amount": amount,
                       "currency": currency, "ts": ts, **extra}) + "\n"


def run(text, **options):
    with contextlib.redirect_stderr(io.StringIO()) as errors:
        result = reconcile(io.StringIO(text), RATES, **options)
    return result, errors.getvalue()


class LedgerTests(unittest.TestCase):
    def test_charge_and_fee(self):
        result, _ = run(event("e1", "charge", "acct_a", "10.00") + event("e2", "fee", "acct_a", "0.25"))
        self.assertEqual(result, {"accounts": {"acct_a": "9.75"}, "rejected": 0, "total": "9.75"})

    def test_malformed_line_is_skipped_with_warning(self):
        result, errors = run("{oops\n" + event("e1", "charge", "acct_a", "1.00"))
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["accounts"], {"acct_a": "1.00"})
        self.assertIn("warning", errors)

    def test_empty_input(self):
        result, _ = run("")
        self.assertEqual(result, {"accounts": {}, "rejected": 0, "total": "0.00"})

    def test_cli_keys_are_sorted(self):
        completed = subprocess.run(
            [sys.executable, "-m", "src.ledger.cli", "-"], cwd=ROOT, text=True, capture_output=True, check=True,
            input=event("e2", "charge", "zeta", "1.00") + event("e1", "charge", "alpha", "2.00"),
        )
        pairs = json.loads(completed.stdout, object_pairs_hook=list)
        self.assertEqual([key for key, _ in pairs], ["accounts", "rejected", "total"])
        self.assertEqual([key for key, _ in dict(pairs)["accounts"]], ["alpha", "zeta"])


if __name__ == "__main__":
    unittest.main()
