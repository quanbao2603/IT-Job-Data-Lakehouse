"""Deterministic target selection for retention-managed Bronze compaction."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


def target_partition_prefixes(
    as_of: str | date,
    sources: Iterable[str],
    *,
    age_days: int = 2,
) -> list[str]:
    """Return one ingestion-day prefix per source, old enough to compact."""
    if age_days < 1:
        raise ValueError("age_days must be >= 1")
    if isinstance(as_of, date):
        current = as_of
    else:
        current = date.fromisoformat(str(as_of)[:10])
    target = current - timedelta(days=age_days)
    return [
        f"{str(source).strip().lower()}/ingest_year={target:%Y}/"
        f"ingest_month={target:%m}/ingest_day={target:%d}"
        for source in sorted({str(source).strip().lower() for source in sources if str(source).strip()})
    ]
