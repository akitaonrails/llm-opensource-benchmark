def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def summarize(records, since=None, until=None):
    selected = [
        record
        for record in records
        if (since is None or record["timestamp"] >= since)
        and (until is None or record["timestamp"] <= until)
    ]
    count = len(selected)
    errors = sum(record["status"] >= 500 for record in selected)
    return {
        "count": count,
        "bytes": sum(record["bytes"] for record in selected),
        "error_rate": errors / count if count else None,
        "p95_ms": percentile([record["latency_ms"] for record in selected], 0.95),
    }
