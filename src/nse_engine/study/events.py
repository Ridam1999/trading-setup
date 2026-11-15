"""Build causal event studies with next-session limit fills and baseline returns."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.backtest.costs import CostsConfig, round_trip_cost
from nse_engine.features.indicators import regime_labels
from nse_engine.setups.base import Setup, eligible_rows
from nse_engine.study.config import StudySettings


def simulate_event(
    symbol_frame: pd.DataFrame,
    signal_index: int,
    horizon: int,
    stop_loss_pct: float | None,
    target_pct: float | None,
    settings: StudySettings,
    costs: CostsConfig,
) -> dict[str, Any] | None:
    """Simulate one close signal, next-day limit entry, and stop/target/time exit."""
    if horizon < 1 or signal_index + horizon >= len(symbol_frame):
        return None
    if (stop_loss_pct is not None and stop_loss_pct <= 0) or (
        target_pct is not None and target_pct <= 0
    ):
        raise ValueError("Stop and target percentages must be positive")
    signal_row = symbol_frame.iloc[signal_index]
    entry_row = symbol_frame.iloc[signal_index + 1]
    if "circuit_locked" in entry_row and pd.notna(entry_row["circuit_locked"]):
        if bool(entry_row["circuit_locked"]):
            return None
    limit_price = float(signal_row["close"]) * (1 + settings.entry_limit_buffer_bps / 10000)
    if float(entry_row["open"]) <= limit_price:
        base_entry = float(entry_row["open"])
    elif float(entry_row["low"]) <= limit_price:
        base_entry = limit_price
    else:
        return None

    slip = costs.slippage_bps / 10000
    entry_price = base_entry * (1 + slip)
    stop_pct = stop_loss_pct
    target_pct_value = target_pct
    stop_price = entry_price * (1 - stop_pct / 100) if stop_pct is not None else None
    target_price = (
        entry_price * (1 + target_pct_value / 100) if target_pct_value is not None else None
    )
    exit_index = signal_index + horizon
    base_exit: float | None = None
    for bar_index in range(signal_index + 1, signal_index + horizon + 1):
        bar = symbol_frame.iloc[bar_index]
        low = float(bar["low"])
        high = float(bar["high"])
        open_price = float(bar["open"])
        stop_hit = stop_price is not None and low <= stop_price
        target_hit = target_price is not None and high >= target_price
        if stop_hit:
            assert stop_price is not None
            base_exit = open_price if open_price <= stop_price else stop_price
            exit_index = bar_index
            break
        if target_hit:
            assert target_price is not None
            base_exit = target_price
            exit_index = bar_index
            break
    if base_exit is None:
        base_exit = float(symbol_frame.iloc[exit_index]["close"])
    exit_price = base_exit * (1 - slip)
    gross_return = exit_price / entry_price - 1
    quantity = settings.notional_rupees / entry_price
    charges = round_trip_cost(entry_price, exit_price, quantity, costs)
    net_return = gross_return - charges / settings.notional_rupees
    path = symbol_frame.iloc[signal_index + 1 : exit_index + 1]
    adverse = float(path["low"].min()) / base_entry - 1
    favourable = float(path["high"].max()) / base_entry - 1
    return {
        "entry_date": entry_row["date"],
        "entry_price": entry_price,
        "exit_date": symbol_frame.iloc[exit_index]["date"],
        "exit_price": exit_price,
        "gross_return": gross_return,
        "net_return": net_return,
        "mae": adverse,
        "mfe": favourable,
        "holding_days": exit_index - signal_index,
        "costs_paid": charges,
    }


def _simulate_with_stress(
    symbol_frame: pd.DataFrame,
    signal_index: int,
    horizon: int,
    stop_loss_pct: float | None,
    target_pct: float | None,
    settings: StudySettings,
    costs: CostsConfig,
) -> dict[str, Any] | None:
    """Evaluate a trade at all configured cost/slippage stress multiples."""
    original_slippage = costs.slippage_bps
    base_result: dict[str, Any] | None = None
    for multiplier in costs.stress_multipliers:
        stressed_costs = costs.model_copy(update={"slippage_bps": original_slippage * multiplier})
        result = simulate_event(
            symbol_frame, signal_index, horizon, stop_loss_pct, target_pct, settings, stressed_costs
        )
        if result is None:
            return None
        result["net_return"] = result["gross_return"] - (
            result["costs_paid"] * multiplier / settings.notional_rupees
        )
        result[f"net_return_{multiplier:g}x_cost"] = result["net_return"]
        if multiplier == 1.0:
            base_result = result.copy()
        elif base_result is not None:
            base_result[f"net_return_{multiplier:g}x_cost"] = result["net_return"]
    return base_result


def build_event_study(
    frame: pd.DataFrame,
    setup: Setup,
    settings: StudySettings,
    costs: CostsConfig,
    split_start: date,
    split_end: date,
) -> pd.DataFrame:
    """Create signal events, eligible-universe baselines, and stress returns."""
    if frame.empty:
        raise ValueError("No OHLCV data is available for the event study")
    required = set(setup.required_features()) | {
        "symbol",
        "date",
        "open",
        "low",
        "high",
        "close",
        "volume",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Study data is missing columns: {', '.join(sorted(missing))}")

    all_bars = frame.sort_values(["symbol", "date"]).reset_index(drop=True).copy()
    benchmark = all_bars.loc[all_bars["symbol"] == settings.benchmark_symbol].sort_values("date")
    if benchmark.empty:
        regimes = pd.DataFrame(
            {"trend_regime": "unknown", "volatility_regime": "unknown"},
            index=pd.Index(all_bars["date"].drop_duplicates(), name="date"),
        )
    else:
        labels = regime_labels(
            benchmark.set_index("date")["close"],
            settings.benchmark_trend_length,
            settings.volatility_lookback,
        )
        regimes = labels
        regimes.index.name = "date"

    signal_keys: set[tuple[str, date]] = set()
    eligible_keys: set[tuple[str, date]] = set()
    by_symbol = {
        symbol: group.reset_index(drop=True) for symbol, group in all_bars.groupby("symbol")
    }
    for symbol, group in by_symbol.items():
        eligibility = eligible_rows(group, setup.config)
        signals = setup.signals(group).astype(bool) & eligibility
        for row_index, row in group.iterrows():
            day = row["date"]
            if day < split_start or day > split_end:
                continue
            key = (str(symbol), day)
            if bool(eligibility.iloc[row_index]):
                eligible_keys.add(key)
            if bool(signals.iloc[row_index]):
                signal_keys.add(key)

    event_dates = {day for _, day in signal_keys}
    candidate_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    stop_value = setup.config.exit_rules.get("stop_loss_pct")
    target_value = setup.config.exit_rules.get("target_pct")
    stop_loss_pct = float(stop_value) if isinstance(stop_value, int | float) else None
    target_pct = float(target_value) if isinstance(target_value, int | float) else None
    for symbol, group in by_symbol.items():
        for signal_index, row in group.iterrows():
            signal_date = row["date"]
            key = (str(symbol), signal_date)
            if signal_date not in event_dates or key not in eligible_keys:
                continue
            regime = regimes.loc[signal_date] if signal_date in regimes.index else None
            if regime is None:
                trend_regime, volatility_regime = "unknown", "unknown"
            else:
                trend_regime = str(regime["trend_regime"])
                volatility_regime = str(regime["volatility_regime"])
                if trend_regime == "<NA>" or trend_regime == "nan":
                    trend_regime = "unknown"
                if volatility_regime == "<NA>" or volatility_regime == "nan":
                    volatility_regime = "unknown"
            for horizon in settings.horizons:
                result = _simulate_with_stress(
                    group,
                    signal_index,
                    horizon,
                    stop_loss_pct,
                    target_pct,
                    settings,
                    costs,
                )
                if result is None or result["exit_date"] > split_end:
                    continue
                candidate_rows.append(
                    {
                        "symbol": str(symbol),
                        "signal_date": signal_date,
                        "horizon": horizon,
                        "trend_regime": trend_regime,
                        "volatility_regime": volatility_regime,
                        **result,
                    }
                )
                if key in signal_keys:
                    signal_rows.append(
                        {
                            "symbol": str(symbol),
                            "signal_date": signal_date,
                            "horizon": horizon,
                            "trend_regime": trend_regime,
                            "volatility_regime": volatility_regime,
                            **result,
                        }
                    )
    if not signal_rows:
        return pd.DataFrame()

    candidates = pd.DataFrame(candidate_rows)
    events = pd.DataFrame(signal_rows)
    date_baseline = candidates.groupby(["signal_date", "horizon"])["net_return"].mean()
    regime_baseline = candidates.groupby(["horizon", "trend_regime", "volatility_regime"])[
        "net_return"
    ].mean()
    events["baseline_net_return"] = [
        date_baseline.loc[(row.signal_date, row.horizon)] for row in events.itertuples()
    ]
    events["regime_matched_baseline"] = [
        regime_baseline.loc[(row.horizon, row.trend_regime, row.volatility_regime)]
        for row in events.itertuples()
    ]
    events["excess_over_baseline"] = events["net_return"] - events["baseline_net_return"]
    events["excess_over_regime_baseline"] = events["net_return"] - events["regime_matched_baseline"]
    return events.sort_values(["signal_date", "horizon", "symbol"], ignore_index=True)
