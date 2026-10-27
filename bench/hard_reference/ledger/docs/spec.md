# Ledger specification

Ledger reads newline-delimited JSON payment events and writes one JSON object with each account's net balance in US dollars. It uses only the Python standard library.

## Events

Each nonempty line is one object:

```json
{"id":"evt_001","type":"charge","account":"acct_a","amount":"10.00","currency":"EUR","ts":"2025-03-01T10:00:00+01:00"}
```

- `id` is a nonempty string.
- `type` is `charge`, `refund` or `fee`.
- `account` is a nonempty string.
- `amount` is a JSON string holding a strictly positive decimal: digits, optionally followed by a dot and more digits. No sign, exponent or whitespace. It may not have more fractional digits than the currency allows: USD, EUR and GBP allow 2, JPY allows 0.
- `currency` is a code present in the rates file.
- `ts` is an ISO-8601 timestamp with an explicit offset. `Z` is accepted.
- A `refund` also has `charge_id`, the id of the charge it refunds.

A line that is not valid JSON, is not an object, or breaks any rule above is skipped with one warning on standard error (`warning: line N: reason`), and processing continues. Empty lines are ignored without a warning.

## Acceptance

- Event ids are unique. The first valid event with a given id is used; later valid events with the same id are skipped with a warning, even when their contents differ. A line skipped as invalid does not reserve its id.
- A `charge` adds its amount to its account. A `fee` subtracts its amount from its account.
- A `refund` subtracts its amount from the account of the charge it refunds. It must reference a charge accepted earlier in the input, in the same currency and the same account. Refunds for one charge may not add up to more than the charge amount: a refund that would exceed it is rejected whole, with a warning.
- Amounts are exact decimals. Never use binary floating point for money.

## Conversion and rounding

`docs/rates.json` gives US dollars per unit of each currency, as decimal strings. Each account keeps an exact sum per currency. At the end, each currency sum is converted to US dollars exactly, the converted sums are added, and the account total is rounded once to cents with round-half-even.

## Window

`--since` and `--until` select which accepted events change balances. They compare instants, so offsets must be normalized. `--since` includes events at the bound and `--until` excludes them. Validation, duplicate detection and the refund limit always consider the whole input, whatever the window.

## Output

The CLI is `python -m src.ledger.cli INPUT [--since TS] [--until TS] [--rates PATH]`. `INPUT` is a path or `-` for standard input. `--rates` defaults to `docs/rates.json` in the project.

It prints one JSON object followed by a newline:

```json
{"accounts": {"acct_a": "12.34", "acct_b": "-0.50"}, "rejected": 1, "total": "11.84"}
```

- `accounts` maps each account with at least one accepted event in the window to its balance.
- `rejected` counts the nonempty lines skipped with a warning.
- `total` is the sum of the rounded account balances.
- All money values are strings with exactly two decimals. A zero balance is `"0.00"`, never `"-0.00"`.
- Keys are sorted, including account keys. Warnings never appear on standard output.

## Internal API

- `src.ledger.parser.parse_timestamp(value)` returns an aware `datetime`.
- `src.ledger.engine.load_rates(path)` returns a dict mapping currency codes to `Decimal`.
- `src.ledger.engine.reconcile(lines, rates, since=None, until=None)` takes an iterable of text lines, the rates dict and optional aware datetimes, writes warnings to standard error, and returns the output object as a dict.
- `src.ledger.cli.main()` implements the command.

Run the visible suite with `python -m unittest discover -s tests -v`.
