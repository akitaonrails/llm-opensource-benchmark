"""Hidden tests for the hard ledger canary. Graded only after the model's last turn.

Test names start with a category: spec_, context_ (decisions buried in the long first
prompt), turn2_, turn3_, turn4_.
"""
import contextlib
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(os.environ["LEDGER_PROJECT"]).resolve()
sys.path.insert(0, str(PROJECT))

from src.ledger.engine import load_rates, reconcile  # noqa: E402
from src.ledger.parser import parse_timestamp  # noqa: E402

RATES_PATH = PROJECT / "docs" / "rates.json"
T0 = "2025-03-01T00:00:00Z"


def ts(minute):
    return f"2025-03-01T00:{minute:02}:00Z"


def ev(event_id, kind, account, amount, currency="USD", at=T0, **extra):
    return json.dumps({"id": event_id, "type": kind, "account": account, "amount": amount,
                       "currency": currency, "ts": at, **extra}) + "\n"


def rates():
    return load_rates(RATES_PATH)


def run(text, **options):
    with contextlib.redirect_stderr(io.StringIO()) as errors:
        result = reconcile(io.StringIO(text), rates(), **options)
    return result, errors.getvalue()


def cli(text, *arguments, check=True):
    return subprocess.run([sys.executable, "-m", "src.ledger.cli", "-", *arguments], cwd=PROJECT,
                          input=text, text=True, capture_output=True, check=check)


class HiddenLedgerTests(unittest.TestCase):
    # --- specification -------------------------------------------------------------------
    def test_spec_charges_and_fees(self):
        text = ev("e1", "charge", "a", "10.00") + ev("e2", "fee", "a", "0.30") + ev("e3", "charge", "b", "5")
        self.assertEqual(run(text)[0], {"accounts": {"a": "9.70", "b": "5.00"}, "rejected": 0, "total": "14.70"})

    def test_spec_round_once_half_even(self):
        # 3 x 1.085 = 3.255 exactly -> 3.26 half-even; per-event rounding gives 3.24, floats 3.25.
        text = "".join(ev(f"e{i}", "charge", "a", "1.00", "EUR") for i in range(3))
        text += ev("e9", "charge", "b", "1.00", "EUR")  # 1.085 -> 1.08
        self.assertEqual(run(text)[0], {"accounts": {"a": "3.26", "b": "1.08"}, "rejected": 0, "total": "4.34"})

    def test_spec_multi_currency_exact(self):
        text = ev("e1", "charge", "a", "1000", "JPY") + ev("e2", "charge", "a", "0.01", "GBP")
        text += ev("e3", "charge", "b", "0.10", "EUR") + ev("e4", "charge", "b", "0.20", "EUR")
        result = run(text)[0]
        self.assertEqual(result["accounts"], {"a": "6.72", "b": "0.33"})  # 6.724675, 0.3255

    def test_spec_invalid_amounts(self):
        bad = ["1.234", "-1.00", "1e2", 1.0, "0.00", " 1.00", "1.", ".5"]
        text = "".join(ev(f"b{i}", "charge", "a", amount) for i, amount in enumerate(bad))
        text += ev("j1", "charge", "a", "10.5", "JPY") + ev("ok", "charge", "a", "2.50")
        result, errors = run(text)
        self.assertEqual(result["accounts"], {"a": "2.50"})
        self.assertEqual(result["rejected"], 9)
        self.assertGreaterEqual(errors.count("warning"), 9)

    def test_spec_invalid_fields(self):
        lines = [
            "{broken\n", "[]\n", "42\n",
            ev("x1", "charge", "a", "1.00", "XYZ"),
            ev("x2", "bonus", "a", "1.00"),
            ev("x3", "charge", "a", "1.00", at="2025-03-01T00:00:00"),
            ev("", "charge", "a", "1.00"),
            ev("x5", "charge", "", "1.00"),
            json.dumps({"id": "x6", "type": "charge", "account": "a", "amount": "1.00", "currency": "USD"}) + "\n",
            "\n", "   \n",
            ev("ok", "charge", "a", "1.00"),
        ]
        result, _ = run("".join(lines))
        self.assertEqual(result, {"accounts": {"a": "1.00"}, "rejected": 9, "total": "1.00"})

    def test_spec_duplicate_first_wins(self):
        text = ev("e1", "charge", "a", "10.00") + ev("e1", "charge", "a", "99.00") + ev("e1", "fee", "b", "1.00")
        self.assertEqual(run(text)[0], {"accounts": {"a": "10.00"}, "rejected": 2, "total": "10.00"})

    def test_spec_invalid_line_does_not_reserve_id(self):
        text = ev("e1", "charge", "a", "1.234") + ev("e1", "charge", "a", "5.00")
        self.assertEqual(run(text)[0], {"accounts": {"a": "5.00"}, "rejected": 1, "total": "5.00"})

    def test_spec_refund_limit_rejects_whole(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0))
        text += ev("r1", "refund", "a", "6.00", at=ts(1), charge_id="c1")
        text += ev("r2", "refund", "a", "5.00", at=ts(2), charge_id="c1")
        text += ev("r3", "refund", "a", "4.00", at=ts(3), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "0.00"}, "rejected": 1, "total": "0.00"})

    def test_spec_refund_must_match_currency_and_account(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0))
        text += ev("r1", "refund", "a", "1.00", "EUR", at=ts(1), charge_id="c1")
        text += ev("r2", "refund", "b", "1.00", at=ts(2), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "10.00"}, "rejected": 2, "total": "10.00"})

    def test_spec_window_offsets_and_bounds(self):
        text = ev("e1", "charge", "a", "1.00", at="2025-03-01T00:30:00+01:00")  # 23:30Z the day before
        text += ev("e2", "charge", "a", "2.00", at="2025-02-28T19:30:00-05:00")  # 00:30Z
        text += ev("e3", "charge", "a", "4.00", at="2025-03-01T00:00:00Z")  # at since: included
        text += ev("e4", "charge", "a", "8.00", at="2025-03-01T02:00:00+01:00")  # 01:00Z, at until: excluded
        result = run(text, since=parse_timestamp("2025-03-01T00:00:00Z"),
                     until=parse_timestamp("2025-03-01T01:00:00Z"))[0]
        self.assertEqual(result, {"accounts": {"a": "6.00"}, "rejected": 0, "total": "6.00"})

    def test_spec_window_limit_uses_whole_input(self):
        text = ev("c1", "charge", "a", "10.00", at="2025-02-01T00:00:00Z")
        text += ev("r1", "refund", "a", "4.00", at=ts(10), charge_id="c1")
        text += ev("r2", "refund", "a", "7.00", at=ts(20), charge_id="c1")
        result = run(text, since=parse_timestamp(T0))[0]
        self.assertEqual(result, {"accounts": {"a": "-4.00"}, "rejected": 1, "total": "-4.00"})

    def test_spec_cli_format(self):
        text = ev("e1", "charge", "zero", "1.00") + ev("e2", "fee", "zero", "1.00")
        text += ev("e3", "fee", "neg", "1.5") + "{bad\n"
        first, second = cli(text), cli(text)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stdout,
                         '{"accounts": {"neg": "-1.50", "zero": "0.00"}, "rejected": 1, "total": "-1.50"}\n')
        self.assertNotIn("warning", first.stdout)
        self.assertIn("warning", first.stderr)

    def test_spec_empty_input(self):
        self.assertEqual(run("\n\n")[0], {"accounts": {}, "rejected": 0, "total": "0.00"})
        self.assertEqual(cli("").stdout, '{"accounts": {}, "rejected": 0, "total": "0.00"}\n')

    def test_spec_cli_rates_option(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("LEDGER_TMP")) as directory:
            path = Path(directory) / "rates.json"
            path.write_text(json.dumps({"USD": "1", "EUR": "2"}), encoding="utf-8")
            completed = cli(ev("e1", "charge", "a", "1.25", "EUR"), "--rates", str(path))
        self.assertEqual(json.loads(completed.stdout)["accounts"], {"a": "2.50"})

    def test_spec_load_rates_returns_decimals(self):
        loaded = rates()
        self.assertEqual(loaded["EUR"], Decimal("1.085"))
        self.assertTrue(all(isinstance(value, Decimal) for value in loaded.values()))

    # --- decisions buried in the first prompt's transcript ---------------------------------
    def test_context_account_ids_case_insensitive(self):
        text = ev("e1", "charge", "ACCT_A", "5.00") + ev("e2", "charge", "acct_a", "1.00")
        self.assertEqual(run(text)[0], {"accounts": {"acct_a": "6.00"}, "rejected": 0, "total": "6.00"})

    def test_context_refund_account_match_ignores_case(self):
        text = ev("c1", "charge", "Shop", "10.00", at=ts(0)) + ev("r1", "refund", "SHOP", "3.00", at=ts(1), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"shop": "7.00"}, "rejected": 0, "total": "7.00"})

    def test_context_test_events_ignored_silently(self):
        text = ev("t1", "charge", "a", "100.00", test=True) + ev("e1", "charge", "b", "1.00")
        result, errors = run(text)
        self.assertEqual(result, {"accounts": {"b": "1.00"}, "rejected": 0, "total": "1.00"})
        self.assertNotIn("warning", errors)

    def test_context_test_event_ids_are_reserved(self):
        text = ev("e1", "charge", "a", "100.00", test=True) + ev("e1", "charge", "a", "1.00")
        self.assertEqual(run(text)[0], {"accounts": {}, "rejected": 1, "total": "0.00"})

    def test_context_fees_must_be_usd(self):
        text = ev("e1", "charge", "a", "10.00") + ev("e2", "fee", "a", "1.00", "EUR")
        self.assertEqual(run(text)[0], {"accounts": {"a": "10.00"}, "rejected": 1, "total": "10.00"})

    def test_context_house_accounts_excluded_from_total(self):
        text = ev("e1", "charge", "house_ops", "50.00") + ev("e2", "charge", "HOUSE_FX", "7.00")
        text += ev("e3", "charge", "a", "10.00")
        self.assertEqual(run(text)[0], {"accounts": {"a": "10.00", "house_fx": "7.00", "house_ops": "50.00"},
                                        "rejected": 0, "total": "10.00"})

    # --- turn 2: unordered exporter ---------------------------------------------------------
    def test_turn2_refund_before_charge_in_file(self):
        text = ev("r1", "refund", "a", "3.00", at=ts(5), charge_id="c1") + ev("c1", "charge", "a", "10.00", at=ts(0))
        self.assertEqual(run(text)[0], {"accounts": {"a": "7.00"}, "rejected": 0, "total": "7.00"})

    def test_turn2_limit_in_timestamp_order(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0))
        text += ev("rA", "refund", "a", "8.00", at=ts(3), charge_id="c1")
        text += ev("rB", "refund", "a", "5.00", at=ts(2), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "5.00"}, "rejected": 1, "total": "5.00"})

    def test_turn2_limit_ties_use_file_order(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0))
        text += ev("rA", "refund", "a", "6.00", at=ts(2), charge_id="c1")
        text += ev("rB", "refund", "a", "7.00", at="2025-03-01T01:02:00+01:00", charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "4.00"}, "rejected": 1, "total": "4.00"})

    def test_turn2_orphan_refund_rejected(self):
        text = ev("r1", "refund", "a", "1.00", charge_id="missing") + ev("c1", "charge", "a", "2.00")
        self.assertEqual(run(text)[0], {"accounts": {"a": "2.00"}, "rejected": 1, "total": "2.00"})

    def test_turn2_duplicates_still_first_in_file(self):
        text = ev("e1", "charge", "a", "1.00", at=ts(9)) + ev("e1", "charge", "a", "2.00", at=ts(1))
        self.assertEqual(run(text)[0], {"accounts": {"a": "1.00"}, "rejected": 1, "total": "1.00"})

    # --- turn 3: chargebacks and report currency ------------------------------------------
    def test_turn3_chargeback_ignores_refund_limit(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0)) + ev("r1", "refund", "a", "8.00", at=ts(1), charge_id="c1")
        text += ev("k1", "chargeback", "a", "10.00", at=ts(2), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "-8.00"}, "rejected": 0, "total": "-8.00"})

    def test_turn3_chargeback_consumes_refund_limit(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0)) + ev("k1", "chargeback", "a", "7.00", at=ts(1), charge_id="c1")
        text += ev("r1", "refund", "a", "5.00", at=ts(2), charge_id="c1")
        text += ev("r2", "refund", "a", "3.00", at=ts(3), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "0.00"}, "rejected": 1, "total": "0.00"})

    def test_turn3_second_chargeback_rejected(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0)) + ev("k1", "chargeback", "a", "1.00", at=ts(1), charge_id="c1")
        text += ev("k2", "chargeback", "a", "1.00", at=ts(2), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "9.00"}, "rejected": 1, "total": "9.00"})

    def test_turn3_chargeback_over_charge_amount_rejected(self):
        text = ev("c1", "charge", "a", "10.00", at=ts(0)) + ev("k1", "chargeback", "a", "10.01", at=ts(1), charge_id="c1")
        self.assertEqual(run(text)[0], {"accounts": {"a": "10.00"}, "rejected": 1, "total": "10.00"})

    def test_turn3_chargeback_before_charge_in_file(self):
        text = ev("k1", "chargeback", "A", "4.00", at=ts(1), charge_id="c1") + ev("c1", "charge", "a", "10.00", at=ts(0))
        self.assertEqual(run(text)[0], {"accounts": {"a": "6.00"}, "rejected": 0, "total": "6.00"})

    def test_turn3_report_currency(self):
        text = ev("e1", "charge", "a", "10.00") + "".join(ev(f"b{i}", "charge", "b", "1.00", "EUR") for i in range(3))
        expected = {"accounts": {"a": "9.22", "b": "3.00"}, "rejected": 0, "total": "12.22"}
        self.assertEqual(run(text, currency="EUR")[0], expected)
        self.assertEqual(json.loads(cli(text, "--currency", "EUR").stdout), expected)

    def test_turn3_report_currency_keeps_earlier_rules(self):
        text = ev("e1", "charge", "House_Ops", "100", "JPY") + ev("e2", "charge", "B", "1.00", "GBP")
        expected = {"accounts": {"b": "1.00", "house_ops": "0.53"}, "rejected": 0, "total": "1.00"}
        self.assertEqual(json.loads(cli(text, "--currency", "GBP").stdout), expected)

    def test_turn3_unknown_report_currency(self):
        self.assertEqual(json.loads(cli(ev("e1", "charge", "a", "1.00"), "--currency", "USD").stdout)["total"], "1.00")
        completed = cli(ev("e1", "charge", "a", "1.00"), "--currency", "XYZ", check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")

    # --- turn 4: money module refactor ---------------------------------------------------------
    def test_turn4_money_convert_exact(self):
        from src.ledger.money import convert
        loaded = rates()
        self.assertEqual(convert(Decimal("3.00"), "EUR", "USD", loaded), Decimal("3.255"))
        self.assertEqual(convert(Decimal("3.255"), "USD", "EUR", loaded), Decimal("3"))
        value = convert(Decimal("10"), "USD", "EUR", loaded)
        self.assertLess(abs(value - Decimal("9.216589861751152073732718894009216589862")), Decimal("1e-35"))

    def test_turn4_public_api_still_importable(self):
        from src.ledger import cli as cli_module
        self.assertTrue(callable(cli_module.main))
        self.assertEqual(run(ev("e1", "charge", "a", "1.00"), since=None, until=None)[0]["total"], "1.00")


if __name__ == "__main__":
    unittest.main()
