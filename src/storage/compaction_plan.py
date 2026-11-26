"""Deterministic target selection for retention-managed Bronze compaction."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


def target_partition_prefixes(
    as_of: str | date,
    sources: Iterable[str],
    *,
    age_days: int = 2,
    window_days: int = 1,
) -> list[str]:
    """Return ingestion-day prefixes per source, old enough to compact.

    A single day is not enough: each daily run writes one or two files per source, so a one-day
    window with a seven-file threshold never had anything to merge and compaction silently did
    nothing for weeks. The window walks back from `age_days` so the backlog drains too.
    """
    if age_days < 1:
        raise ValueError("age_days must be >= 1")
    if window_days < 1:
        raise ValueError("window_days must be >= 1")
    if isinstance(as_of, date):
        current = as_of
    else:
        current = date.fromisoformat(str(as_of)[:10])
    ordered = sorted({str(source).strip().lower() for source in sources if str(source).strip()})
    prefixes: list[str] = []
    for offset in range(age_days, age_days + window_days):
        target = current - timedelta(days=offset)
        prefixes.extend(
            f"{source}/ingest_year={target:%Y}/ingest_month={target:%m}/ingest_day={target:%d}"
            for source in ordered
        )
    return prefixes
