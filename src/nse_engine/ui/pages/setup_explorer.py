"""Read-only price and signal explorer backed by the adjusted store and events."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.reporting import charts
from nse_engine.ui import common
from nse_engine.ui.config import load_ui_settings


def render() -> None:
    """Render candles for one symbol with saved event markers."""
    common.render_header("Setup explorer")
    settings = load_ui_settings()
    bars = common.load_ohlcv()
    if bars.empty or "symbol" not in bars.columns:
        common.st.info("No adjusted OHLCV rows found. Import data before using this page.")
        common.render_footer()
        return

    symbols = sorted(bars["symbol"].dropna().unique().tolist())
    default_index = (
        symbols.index(settings.default_symbol) if settings.default_symbol in symbols else 0
    )
    symbol = common.st.selectbox("Symbol", symbols, index=default_index)
    bars_to_show = common.st.slider(
        "Bars to display",
        min_value=settings.min_bars,
        max_value=settings.max_bars,
        value=settings.default_bars,
        step=settings.min_bars,
    )
    symbol_bars = bars.loc[bars["symbol"] == symbol].tail(bars_to_show)

    events = common.load_events()
    if events.empty or "symbol" not in events.columns:
        symbol_events = pd.DataFrame()
    else:
        symbol_events = events.loc[events["symbol"] == symbol]

    common.st.plotly_chart(charts.candlestick_chart(symbol_bars, symbol_events))
    if symbol_events.empty:
        common.st.info("No saved events for this symbol.")
    else:
        common.st.dataframe(symbol_events.head(settings.table_rows), hide_index=True)
    common.render_footer()
