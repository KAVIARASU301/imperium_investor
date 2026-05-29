from datetime import datetime
from zoneinfo import ZoneInfo

from ibkr.utils.market_time import (
    US_MARKET_CLOSE,
    US_MARKET_OPEN,
    US_MARKET_TZ,
    market_session_label,
    is_regular_market_open,
    to_market_date,
    to_market_time,
)


def test_market_time_converts_utc_to_new_york_calendar_day():
    utc_value = datetime(2026, 5, 30, 1, 0, tzinfo=ZoneInfo("UTC"))

    market_value = to_market_time(utc_value)

    assert market_value.tzinfo == US_MARKET_TZ
    assert market_value.date().isoformat() == "2026-05-29"
    assert to_market_date(utc_value).isoformat() == "2026-05-29"


def test_regular_market_open_uses_us_equity_session():
    open_moment = datetime(2026, 5, 29, 10, 0, tzinfo=US_MARKET_TZ)
    before_open = datetime(2026, 5, 29, 9, 29, tzinfo=US_MARKET_TZ)
    weekend = datetime(2026, 5, 30, 10, 0, tzinfo=US_MARKET_TZ)

    assert US_MARKET_OPEN.hour == 9 and US_MARKET_OPEN.minute == 30
    assert US_MARKET_CLOSE.hour == 16 and US_MARKET_CLOSE.minute == 0
    assert is_regular_market_open(open_moment)
    assert not is_regular_market_open(before_open)
    assert not is_regular_market_open(weekend)


def test_market_session_label_names_extended_us_equity_sessions():
    premarket = datetime(2026, 5, 29, 8, 0, tzinfo=US_MARKET_TZ)
    regular = datetime(2026, 5, 29, 10, 0, tzinfo=US_MARKET_TZ)
    postmarket = datetime(2026, 5, 29, 17, 0, tzinfo=US_MARKET_TZ)
    closed = datetime(2026, 5, 29, 21, 0, tzinfo=US_MARKET_TZ)
    weekend = datetime(2026, 5, 30, 10, 0, tzinfo=US_MARKET_TZ)

    assert market_session_label(premarket) == "Premarket"
    assert market_session_label(regular) == "Regular Trading Hours"
    assert market_session_label(postmarket) == "Post Market"
    assert market_session_label(closed) == "Closed"
    assert market_session_label(weekend) == "Closed"
