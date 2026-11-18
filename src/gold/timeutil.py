"""Time helpers shared by every Gold loader.

All Silver timestamps are UTC ISO-8601 with a trailing Z. Reporting dates are derived
in Asia/Ho_Chi_Minh so a daily snapshot matches the business day the crawl represents.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from src.gold.contract import GOLD_TIMEZONE

_TZ = ZoneInfo(GOLD_TIMEZONE)


def parse_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse a Silver UTC timestamp into an aware datetime; None stays None."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def date_key(value: date) -> int:
    """YYYYMMDD integer key used by dim_date and every fact."""
    return int(f"{value.year:04d}{value.month:02d}{value.day:02d}")


def report_date(cutoff: Optional[str]) -> date:
    """The business day a release reports on, in the Gold reporting timezone."""
    parsed = parse_utc(cutoff)
    if parsed is None:
        raise ValueError(f"Invalid or missing snapshot_cutoff_at: {cutoff!r}")
    return parsed.astimezone(_TZ).date()


def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a YYYY-MM-DD business date such as posted_date."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def days_between(earlier: Optional[str], later: Optional[str]) -> Optional[int]:
    """Whole days from earlier to later; None when either side is unparseable."""
    start, end = parse_utc(earlier), parse_utc(later)
    if start is None or end is None:
        return None
    return (end - start).days


def within_window(observed_at: Optional[str], cutoff: Optional[str], window_days: int) -> bool:
    """True when an observation is at or before cutoff and inside the evidence window."""
    stamp, limit = parse_utc(observed_at), parse_utc(cutoff)
    if stamp is None or limit is None:
        return False
    if stamp > limit:
        return False
    return stamp >= limit - timedelta(days=window_days)


def daterange(start: date, end: date):
    """Inclusive date range for dim_date generation."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)
