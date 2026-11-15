"""Trading-calendar construction from weekdays and configured exchange holidays."""

from __future__ import annotations

from datetime import date

import pandas as pd  # type: ignore[import-untyped]


def build_calendar(frame: pd.DataFrame, holidays: list[date] | None = None) -> pd.DataFrame:
    """Mark observed market dates as trading days and configured holidays as closed."""
    if frame.empty:
        raise ValueError("Cannot infer a calendar from empty market data")
    holiday_set = set(holidays or [])
    first = min(frame["date"])
    last = max(frame["date"])
    days = pd.date_range(first, last, freq="D").date
    rows = [
        {
            "date": day,
            "is_trading_day": day.weekday() < 5 and day not in holiday_set,
            "note": "Configured exchange holiday" if day in holiday_set else None,
        }
        for day in days
    ]
    return pd.DataFrame(rows)
