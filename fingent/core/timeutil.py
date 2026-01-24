"""
Time utilities for Fingent.

Handles timezone conversions and standardized timestamp formats.
All internal timestamps use UTC, converted for display.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, Union

import pytz

from fingent.core.config import get_settings


def get_timezone() -> pytz.timezone:
    """Get configured timezone."""
    settings = get_settings()
    return pytz.timezone(settings.timezone)


def now_utc() -> datetime:
    """Get current UTC time."""
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    """Get current time in configured timezone."""
    tz = get_timezone()
    return datetime.now(tz)


def to_utc(dt: datetime) -> datetime:
    """Convert datetime to UTC."""
    if dt.tzinfo is None:
        # Assume it's in configured timezone
        tz = get_timezone()
        dt = tz.localize(dt)
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime) -> datetime:
    """Convert datetime to configured timezone."""
    tz = get_timezone()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def format_timestamp(dt: Optional[datetime] = None, fmt: str = "iso") -> str:
    """
    Format datetime to string.

    Args:
        dt: Datetime to format. Uses current UTC time if not provided.
        fmt: Format type - 'iso', 'display', 'date', 'time'

    Returns:
        Formatted string
    """
    if dt is None:
        dt = now_utc()

    formats = {
        "iso": "%Y-%m-%dT%H:%M:%SZ",
        "display": "%Y-%m-%d %H:%M:%S",
        "date": "%Y-%m-%d",
        "time": "%H:%M:%S",
        "log": "%Y-%m-%d %H:%M:%S.%f",
    }

    return dt.strftime(formats.get(fmt, fmt))


def parse_timestamp(s: str, fmt: str = "iso") -> datetime:
    """
    Parse string to datetime.

    Args:
        s: String to parse
        fmt: Format type or strptime format string

    Returns:
        Parsed datetime (UTC)
    """
    formats = {
        "iso": "%Y-%m-%dT%H:%M:%SZ",
        "display": "%Y-%m-%d %H:%M:%S",
        "date": "%Y-%m-%d",
    }

    format_str = formats.get(fmt, fmt)
    dt = datetime.strptime(s, format_str)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def days_ago(n: int, from_dt: Optional[datetime] = None) -> datetime:
    """Get datetime n days ago."""
    base = from_dt or now_utc()
    return base - timedelta(days=n)


def hours_ago(n: int, from_dt: Optional[datetime] = None) -> datetime:
    """Get datetime n hours ago."""
    base = from_dt or now_utc()
    return base - timedelta(hours=n)


def is_market_hours(dt: Optional[datetime] = None) -> bool:
    """
    Check if given time is during US market hours.

    US Market: 9:30 AM - 4:00 PM Eastern
    """
    if dt is None:
        dt = now_utc()

    # Convert to Eastern time
    eastern = pytz.timezone("America/New_York")
    dt_eastern = dt.astimezone(eastern)

    # Check if weekday
    if dt_eastern.weekday() >= 5:  # Saturday = 5, Sunday = 6
        return False

    # Check time
    market_open = dt_eastern.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = dt_eastern.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= dt_eastern <= market_close


def generate_run_id(prefix: str = "run") -> str:
    """Generate a unique run ID based on timestamp."""
    return f"{prefix}_{format_timestamp(now_utc(), '%Y%m%d_%H%M%S')}"


def calculate_change(
    current: float,
    previous: float,
    as_percentage: bool = True,
) -> Optional[float]:
    """
    Calculate percentage change between two values.

    Args:
        current: Current value
        previous: Previous value
        as_percentage: If True, return as percentage (0.05 = 5%)

    Returns:
        Change ratio/percentage, or None if previous is 0
    """
    if previous == 0:
        return None
    change = (current - previous) / previous
    return change if as_percentage else change * 100
