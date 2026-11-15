"""Data health checks distinguish observed exchange sessions from calendar weekdays."""

from datetime import date

import pandas as pd

from nse_engine.data.quality import check_ohlcv


def test_missing_sessions_use_market_dates_observed_across_universe() -> None:
    """A date absent across the entire import is not called a missing session."""
    frame = pd.DataFrame(
        {
            "symbol": ["AAA", "AAA", "BBB", "BBB", "BBB"],
            "date": [
                date(2024, 1, 2),
                date(2024, 1, 4),
                date(2024, 1, 2),
                date(2024, 1, 3),
                date(2024, 1, 4),
            ],
            "open": [10.0, 11.0, 20.0, 21.0, 22.0],
            "high": [10.0, 11.0, 20.0, 21.0, 22.0],
            "low": [10.0, 11.0, 20.0, 21.0, 22.0],
            "close": [10.0, 11.0, 20.0, 21.0, 22.0],
            "volume": [1000, 1000, 1000, 1000, 1000],
        }
    )
    calendar = pd.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "is_trading_day": [True, True, True],
        }
    )

    result = check_ohlcv(frame, calendar, 100, 5)

    missing = result.findings.loc[result.findings["check"] == "missing_trading_day"]
    assert missing[["symbol", "date"]].to_dict("records") == [
        {"symbol": "AAA", "date": date(2024, 1, 3)}
    ]
