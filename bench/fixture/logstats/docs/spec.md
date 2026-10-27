# Logstats specification

Logstats reads newline-delimited JSON request records and writes one JSON summary. It uses only the Python standard library.

## Input

Each nonempty line is one object with this shape:

```json
{"timestamp":"2025-01-02T03:04:05+00:00","path":"/api/items","status":200,"bytes":125,"latency_ms":17}
```

- `timestamp` is an ISO-8601 timestamp with an explicit timezone offset. `Z` is accepted.
- `path` is a string, `status` is an integer from 100 through 599, and `bytes` is a nonnegative integer.
- `latency_ms` is an integer from 0 through 60000. This fixed domain permits a fixed-size latency histogram.
- Malformed JSON, invalid records, and nonempty lines that are not objects are skipped. Each skipped line produces one warning on standard error; processing continues.
- Empty lines are ignored without a warning.
- Input is processed as a stream. Neither the parser nor the CLI loads the whole input into memory.

## Selection and summary

The CLI is `python -m src.logstats.cli INPUT [--since TIMESTAMP] [--until TIMESTAMP]`. `INPUT` is a path or `-` for standard input.

Optional bounds are compared as instants, so offsets must be normalized. `--since` includes records at the bound and `--until` excludes records at the bound.

The output object has exactly these fields:

- `count`: number of selected records.
- `bytes`: sum of their `bytes` values.
- `error_rate`: fraction with status 500 or greater, or `0.0` when count is zero.
- `p95_ms`: the 95th percentile latency, or `null` when count is zero.

Initially, p95 may use linear interpolation at position `(count - 1) * 0.95` in the sorted latencies. All request paths are included in this initial version.

JSON is emitted once, followed by a newline, with keys in deterministic lexicographic order: `bytes`, `count`, `error_rate`, `p95_ms`. Warnings never appear in standard output.

## Internal API

`src.logstats.parser.iter_records(lines)` yields validated records and writes warnings. `src.logstats.parser.parse_timestamp(value)` returns an aware `datetime`. `src.logstats.stats.summarize(records, since=None, until=None)` returns the summary dictionary; bounds are aware datetimes. `src.logstats.cli.main()` implements the command.

Run the visible suite with `python -m unittest discover -s tests -v`.
