import pandas as pd

from chart_engine.core.session_filter import filter_ibkr_premarket_candles


def _bars(times):
    return pd.DataFrame(
        {
            "time": times,
            "open": [1.0] * len(times),
            "high": [1.0] * len(times),
            "low": [1.0] * len(times),
            "close": [1.0] * len(times),
            "volume": [100] * len(times),
        }
    )


def test_disabled_filters_naive_ibkr_premarket_exchange_wall_clock():
    df = _bars([
        "2026-05-29 03:59:00",
        "2026-05-29 04:00:00",
        "2026-05-29 09:29:00",
        "2026-05-29 09:30:00",
    ])

    filtered = filter_ibkr_premarket_candles(
        df,
        show_premarket_candles=False,
        broker_name="ibkr",
        interval="minute",
    )

    assert filtered["time"].astype(str).tolist() == [
        "2026-05-29 03:59:00",
        "2026-05-29 09:30:00",
    ]


def test_disabled_filters_timezone_aware_ibkr_premarket_in_new_york():
    df = _bars(pd.to_datetime([
        "2026-05-29T12:00:00Z",  # 08:00 America/New_York premarket
        "2026-05-29T13:30:00Z",  # 09:30 America/New_York RTH open
    ]))

    filtered = filter_ibkr_premarket_candles(
        df,
        show_premarket_candles=False,
        broker_name="ibkr",
        interval="5minute",
    )

    assert filtered["time"].tolist() == [pd.Timestamp("2026-05-29T13:30:00Z")]


def test_disabled_filters_naive_ibkr_postmarket_exchange_wall_clock():
    df = _bars([
        "2026-05-29 16:00:00",
        "2026-05-29 16:01:00",
        "2026-05-29 20:00:00",
        "2026-05-29 20:01:00",
    ])

    filtered = filter_ibkr_premarket_candles(
        df,
        show_premarket_candles=True,
        show_postmarket_candles=False,
        broker_name="ibkr",
        interval="minute",
    )

    assert filtered["time"].astype(str).tolist() == [
        "2026-05-29 16:00:00",
        "2026-05-29 20:01:00",
    ]


def test_disabled_filters_timezone_aware_ibkr_postmarket_in_new_york():
    df = _bars(pd.to_datetime([
        "2026-05-29T20:00:00Z",  # 16:00 America/New_York RTH close
        "2026-05-29T21:30:00Z",  # 17:30 America/New_York post market
    ]))

    filtered = filter_ibkr_premarket_candles(
        df,
        show_premarket_candles=True,
        show_postmarket_candles=False,
        broker_name="ibkr",
        interval="5minute",
    )

    assert filtered["time"].tolist() == [pd.Timestamp("2026-05-29T20:00:00Z")]


def test_can_filter_premarket_and_postmarket_together():
    df = _bars([
        "2026-05-29 08:00:00",
        "2026-05-29 09:30:00",
        "2026-05-29 17:00:00",
    ])

    filtered = filter_ibkr_premarket_candles(
        df,
        show_premarket_candles=False,
        show_postmarket_candles=False,
        broker_name="ibkr",
        interval="minute",
    )

    assert filtered["time"].astype(str).tolist() == ["2026-05-29 09:30:00"]


def test_enabled_or_non_ibkr_leaves_data_unchanged():
    df = _bars(["2026-05-29 08:00:00", "2026-05-29 17:00:00"])

    assert filter_ibkr_premarket_candles(
        df,
        show_premarket_candles=True,
        show_postmarket_candles=True,
        broker_name="ibkr",
        interval="minute",
    ).equals(df)
    assert filter_ibkr_premarket_candles(
        df,
        show_premarket_candles=False,
        show_postmarket_candles=False,
        broker_name="kite",
        interval="minute",
    ).equals(df)
