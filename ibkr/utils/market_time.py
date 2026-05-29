"""US equity-market time helpers for IBKR mode.

IBKR mode is dedicated to US stocks, so all session logic and persisted/displayed
application timestamps should be based on the US market clock instead of the
machine's local timezone.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

try:
    from PySide6.QtCore import QDate
except Exception:  # pragma: no cover - PySide6 may be absent in headless tests
    QDate = None

US_MARKET_TZ = ZoneInfo("America/New_York")
US_MARKET_TZ_NAME = "America/New_York"
US_MARKET_TZ_IBKR_NAME = "US/Eastern"
US_MARKET_OPEN = time(9, 30)
US_MARKET_CLOSE = time(16, 0)
US_PRE_MARKET_OPEN = time(4, 0)
US_AFTER_HOURS_CLOSE = time(20, 0)
US_MARKET_SESSION_PREMARKET = "Premarket"
US_MARKET_SESSION_RTH = "Regular Trading Hours"
US_MARKET_SESSION_POSTMARKET = "Post Market"
US_MARKET_SESSION_CLOSED = "Closed"


def market_now() -> datetime:
    """Return the current US stock-market time as a timezone-aware datetime."""
    return datetime.now(tz=US_MARKET_TZ)


def market_today() -> date:
    """Return today's date on the US stock-market calendar."""
    return market_now().date()


def market_now_naive() -> datetime:
    """Return current US market time without tzinfo for legacy serializers/UI."""
    return market_now().replace(tzinfo=None)


def market_timestamp() -> float:
    """Return a POSIX timestamp for the current US market-clock instant."""
    return market_now().timestamp()


def market_isoformat(*, naive: bool = True) -> str:
    """Return current US market time as ISO text.

    Existing IBKR-mode JSON stores mostly used naive ISO strings.  Keep that as
    the default so old parsers keep working while the clock remains New York.
    """
    dt = market_now_naive() if naive else market_now()
    return dt.isoformat()


def market_strftime(fmt: str) -> str:
    """Format the current US market time."""
    return market_now().strftime(fmt)


def market_qdate():
    """Return a QDate matching the US market date."""
    if QDate is None:
        raise RuntimeError("PySide6 is required for market_qdate()")
    today = market_today()
    return QDate(today.year, today.month, today.day)


def to_market_time(value: datetime) -> datetime:
    """Convert a datetime to US market time, treating naive values as market time."""
    if value.tzinfo is None:
        return value.replace(tzinfo=US_MARKET_TZ)
    return value.astimezone(US_MARKET_TZ)


def to_market_date(value: datetime | date) -> date:
    """Return the date a datetime/date belongs to on the US market calendar."""
    if isinstance(value, datetime):
        return to_market_time(value).date()
    return value


def is_regular_market_open(moment: datetime | None = None) -> bool:
    """Return whether the supplied/current market-time moment is during US RTH."""
    now = to_market_time(moment) if moment is not None else market_now()
    return now.weekday() < 5 and US_MARKET_OPEN <= now.time() <= US_MARKET_CLOSE


def market_session_label(moment: datetime | None = None) -> str:
    """Return the named US equities session for the supplied/current market time."""
    now = to_market_time(moment) if moment is not None else market_now()
    if now.weekday() >= 5:
        return US_MARKET_SESSION_CLOSED

    now_time = now.time()
    if US_PRE_MARKET_OPEN <= now_time < US_MARKET_OPEN:
        return US_MARKET_SESSION_PREMARKET
    if US_MARKET_OPEN <= now_time <= US_MARKET_CLOSE:
        return US_MARKET_SESSION_RTH
    if US_MARKET_CLOSE < now_time <= US_AFTER_HOURS_CLOSE:
        return US_MARKET_SESSION_POSTMARKET
    return US_MARKET_SESSION_CLOSED


def utc_now() -> datetime:
    """Return the current UTC instant; useful for TTL/duration metadata."""
    return datetime.now(timezone.utc)
