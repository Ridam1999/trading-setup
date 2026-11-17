"""Build causal event studies with next-session limit fills and baseline returns."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from nse_engine.backtest.costs import CostsConfig, DeliveryCostRates, round_trip_cost
from nse_engine.backtest.fills import buy_limit_fill, sell_exit_fill, time_exit_fill
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
    slip = costs.slippage_bps / 10000
    entry_price = buy_limit_fill(
        float(signal_row["close"]),
        entry_row,
        settings.entry_limit_buffer_bps,
        costs.slippage_bps,
    )
    if entry_price is None:
        return None
    base_entry = entry_price / (1 + slip)
    stop_pct = stop_loss_pct
    target_pct_value = target_pct
    stop_price = entry_price * (1 - stop_pct / 100) if stop_pct is not None else None
    target_price = (
        entry_price * (1 + target_pct_value / 100) if target_pct_value is not None else None
    )
    exit_index = signal_index + horizon
    exit_price: float | None = None
    exit_reason: str | None = None
    for bar_index in range(signal_index + 1, signal_index + horizon + 1):
        bar = symbol_frame.iloc[bar_index]
        exit_price, exit_reason = sell_exit_fill(bar, stop_price, target_price, costs.slippage_bps)
        if exit_reason is not None:
            exit_index = bar_index
            break
    if exit_price is None:
        exit_price = time_exit_fill(symbol_frame.iloc[exit_index], costs.slippage_bps)
    if exit_price is None:
        return None
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


def _vectorized_entry_exit(
    group: pd.DataFrame,
    signal_positions: np.ndarray,
    horizon: int,
    settings: StudySettings,
    costs: CostsConfig,
    stop_loss_pct: float | None,
    target_pct: float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fill entry and exit prices for many candidate rows with the shared fill rules."""
    size = signal_positions.size
    empty = np.full(size, np.nan)
    empty_indices = np.full(size, -1, dtype=int)
    if size == 0 or horizon < 1:
        return empty, empty, empty_indices, np.zeros(size, dtype=bool)

    length = len(group)
    in_range = (signal_positions + horizon) < length
    if not in_range.any():
        return empty, empty, empty_indices, np.zeros(size, dtype=bool)

    opens = group["open"].to_numpy(dtype=float)
    highs = group["high"].to_numpy(dtype=float)
    lows = group["low"].to_numpy(dtype=float)
    closes = group["close"].to_numpy(dtype=float)
    locked = (
        group["circuit_locked"].fillna(False).astype(bool).to_numpy()
        if "circuit_locked" in group
        else np.zeros(length, dtype=bool)
    )
    slip = costs.slippage_bps / 10000
    next_positions = signal_positions + 1
    safe_next = np.minimum(next_positions, length - 1)
    limit = closes[signal_positions] * (1 + settings.entry_limit_buffer_bps / 10000)
    base_price = np.where(
        opens[safe_next] <= limit,
        opens[safe_next],
        np.where(lows[safe_next] <= limit, limit, np.nan),
    )
    entry_price = base_price * (1 + slip)
    valid = in_range & np.isfinite(entry_price) & ~locked[safe_next]

    stop_price = (
        np.full(size, np.nan) if stop_loss_pct is None else entry_price * (1 - stop_loss_pct / 100)
    )
    target_price = (
        np.full(size, np.nan) if target_pct is None else entry_price * (1 + target_pct / 100)
    )
    exit_price = np.full(size, np.nan)
    exit_indices = np.full(size, -1, dtype=int)
    active = valid.copy()

    for offset in range(1, horizon + 1):
        positions = signal_positions + offset
        eligible = active & (positions < length)
        if not eligible.any():
            continue
        safe = np.minimum(positions, length - 1)
        stop_hit = lows[safe] <= stop_price
        target_hit = highs[safe] >= target_price
        stop_fill = np.minimum(opens[safe], stop_price)
        chosen = np.where(
            stop_hit,
            stop_fill,
            np.where(target_hit, target_price, np.nan),
        )
        newly_exited = eligible & ~locked[safe] & np.isfinite(chosen)
        exit_price[newly_exited] = chosen[newly_exited] * (1 - slip)
        exit_indices[newly_exited] = positions[newly_exited]
        active[newly_exited] = False

    final_positions = signal_positions + horizon
    final_safe = np.minimum(final_positions, length - 1)
    time_exit = active & (final_positions < length) & ~locked[final_safe]
    exit_price[time_exit] = closes[final_safe[time_exit]] * (1 - slip)
    exit_indices[time_exit] = final_positions[time_exit]
    return entry_price, exit_price, exit_indices, valid & np.isfinite(exit_price)


def _net_return_after_costs(
    entry_price: float, exit_price: float, notional: float, costs: CostsConfig
) -> float:
    """Return one cost-adjusted trade return using the shared cost function."""
    charges = round_trip_cost(entry_price, exit_price, notional / entry_price, costs)
    return exit_price / entry_price - 1 - charges / notional


def _order_charges(turnover: np.ndarray, side: str, rates: DeliveryCostRates) -> np.ndarray:
    """Apply the configured charge schedule to a batch of one-sided turnovers."""
    brokerage = turnover * rates.brokerage_pct / 100
    if rates.brokerage_max_per_order:
        brokerage = np.minimum(brokerage, rates.brokerage_max_per_order)
    brokerage = np.maximum(brokerage, rates.brokerage_min_per_order)
    stt_rate = rates.stt_pct_buy if side == "buy" else rates.stt_pct_sell
    stt = turnover * stt_rate / 100
    exchange = turnover * rates.exchange_txn_pct / 100
    sebi = turnover * rates.sebi_fee_pct / 100
    ipft = turnover * rates.ipft_fee_pct / 100
    stamp = turnover * rates.stamp_duty_pct_buy / 100 if side == "buy" else 0.0
    dp = rates.dp_charge_per_sell_scrip if side == "sell" else 0.0
    gst = (brokerage + exchange + sebi + ipft + dp) * rates.gst_pct / 100
    return np.asarray(brokerage + stt + exchange + sebi + ipft + stamp + gst + dp, dtype=float)


def _round_trip_charges(
    entry_prices: np.ndarray,
    exit_prices: np.ndarray,
    notional: float,
    costs: CostsConfig,
) -> np.ndarray:
    """Return per-trade buy and sell charges for a batch of completed trades."""
    buy_turnover = np.full(entry_prices.shape, notional)
    sell_turnover = buy_turnover * exit_prices / entry_prices
    rates = costs.delivery_equity
    return np.asarray(
        _order_charges(buy_turnover, "buy", rates) + _order_charges(sell_turnover, "sell", rates),
        dtype=float,
    )


def _net_returns_after_costs(
    entry_prices: np.ndarray,
    exit_prices: np.ndarray,
    notional: float,
    costs: CostsConfig,
) -> np.ndarray:
    """Vectorize the shared cost model for a batch of completed baseline trades."""
    charges = _round_trip_charges(entry_prices, exit_prices, notional, costs)
    return np.asarray(exit_prices / entry_prices - 1 - charges / notional, dtype=float)


def _simulate_signal_batch(
    group: pd.DataFrame,
    signal_positions: np.ndarray,
    horizon: int,
    stop_loss_pct: float | None,
    target_pct: float | None,
    settings: StudySettings,
    costs: CostsConfig,
) -> dict[int, dict[str, Any]]:
    """Simulate every signal row at all cost stresses using vectorized fills."""
    notional = settings.notional_rupees
    base_slippage = costs.slippage_bps
    dates = group["date"].to_numpy()
    lows = group["low"].to_numpy(dtype=float)
    highs = group["high"].to_numpy(dtype=float)
    runs: dict[float, dict[str, np.ndarray]] = {}
    for multiplier in costs.stress_multipliers:
        stressed = costs.model_copy(update={"slippage_bps": base_slippage * multiplier})
        entries, exits, exit_indices, filled = _vectorized_entry_exit(
            group, signal_positions, horizon, settings, stressed, stop_loss_pct, target_pct
        )
        gross = np.asarray(exits / entries - 1, dtype=float)
        charges = _round_trip_charges(entries, exits, notional, stressed)
        net = np.asarray(gross - charges * multiplier / notional, dtype=float)
        runs[multiplier] = {
            "entries": entries,
            "exits": exits,
            "exit_indices": exit_indices,
            "filled": filled,
            "gross": gross,
            "charges": charges,
            "net": net,
        }

    base = runs.get(1.0)
    if base is None:
        return {}
    valid = base["filled"].copy()
    for multiplier, run in runs.items():
        if multiplier != 1.0:
            valid &= run["filled"]

    results: dict[int, dict[str, Any]] = {}
    slip = base_slippage / 10000
    for offset in np.flatnonzero(valid):
        position = int(signal_positions[offset])
        entry = float(base["entries"][offset])
        exit_price = float(base["exits"][offset])
        exit_index = int(base["exit_indices"][offset])
        base_entry = entry / (1 + slip)
        path = slice(position + 1, exit_index + 1)
        stress_values = {
            f"net_return_{multiplier:g}x_cost": float(run["net"][offset])
            for multiplier, run in runs.items()
        }
        results[position] = {
            "entry_date": dates[position + 1],
            "entry_price": entry,
            "exit_date": dates[exit_index],
            "exit_price": exit_price,
            "gross_return": float(base["gross"][offset]),
            "net_return": float(base["net"][offset]),
            "mae": float(lows[path].min()) / base_entry - 1,
            "mfe": float(highs[path].max()) / base_entry - 1,
            "holding_days": exit_index - position,
            "costs_paid": float(base["charges"][offset]),
            **stress_values,
        }
    return results


def build_event_study(
    frame: pd.DataFrame,
    setup: Setup,
    settings: StudySettings,
    costs: CostsConfig,
    split_start: date,
    split_end: date,
    point_in_time_membership: pd.Series | None = None,
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

    all_bars = frame.copy()
    if point_in_time_membership is not None:
        historically_selected = set(frame.loc[point_in_time_membership, "symbol"].astype(str))
        historically_selected.add(settings.benchmark_symbol)
        all_bars = all_bars.loc[all_bars["symbol"].isin(historically_selected)].copy()
    if setup.config.symbols:
        keep_symbols = set(setup.config.symbols) | {settings.benchmark_symbol}
        all_bars = all_bars.loc[all_bars["symbol"].isin(keep_symbols)].copy()
    if point_in_time_membership is not None:
        all_bars["_pit_member"] = (
            point_in_time_membership.reindex(all_bars.index).fillna(False).to_numpy(dtype=bool)
        )
    else:
        all_bars["_pit_member"] = True
    all_bars = all_bars.sort_values(["symbol", "date"]).reset_index(drop=True)
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
    signal_positions_by_symbol: dict[str, np.ndarray] = {}
    by_symbol = {
        symbol: group.reset_index(drop=True) for symbol, group in all_bars.groupby("symbol")
    }
    eligibilities: dict[str, pd.Series] = {}
    for symbol, group in by_symbol.items():
        eligibility = eligible_rows(group, setup.config) & group["_pit_member"]
        group["_eligible_rows"] = eligibility
        by_symbol[symbol] = group
        signals = setup.signals(group).astype(bool) & eligibility
        eligibilities[str(symbol)] = eligibility
        in_split = group["date"].between(split_start, split_end)
        signal_positions = (signals & in_split).to_numpy().nonzero()[0]
        signal_positions_by_symbol[str(symbol)] = signal_positions
        signal_keys.update((str(symbol), group.iloc[index]["date"]) for index in signal_positions)

    event_dates = {day for _, day in signal_keys}
    regime_lookup = regimes.to_dict("index")
    candidate_rows: list[dict[str, Any]] = []
    signal_rows: list[dict[str, Any]] = []
    stop_value = setup.config.exit_rules.get("stop_loss_pct")
    target_value = setup.config.exit_rules.get("target_pct")
    stop_loss_pct = float(stop_value) if isinstance(stop_value, int | float) else None
    target_pct = float(target_value) if isinstance(target_value, int | float) else None
    for symbol, group in by_symbol.items():
        candidate_mask = (
            eligibilities[str(symbol)]
            & group["date"].isin(event_dates)
            & group["date"].between(split_start, split_end)
        )
        candidate_positions = candidate_mask.to_numpy().nonzero()[0]
        if candidate_positions.size == 0:
            continue
        dates = group["date"].to_numpy()
        for horizon in settings.horizons:
            signal_results = _simulate_signal_batch(
                group,
                signal_positions_by_symbol[str(symbol)],
                horizon,
                stop_loss_pct,
                target_pct,
                settings,
                costs,
            )
            entries, exits, exit_indices, filled = _vectorized_entry_exit(
                group,
                candidate_positions,
                horizon,
                settings,
                costs,
                stop_loss_pct,
                target_pct,
            )
            net_returns = _net_returns_after_costs(
                entries[filled], exits[filled], settings.notional_rupees, costs
            )
            filled_positions = candidate_positions[filled]
            for position, exit_index, net_return in zip(
                filled_positions, exit_indices[filled], net_returns, strict=True
            ):
                if dates[exit_index] > split_end:
                    continue
                signal_date = dates[position]
                regime = regime_lookup.get(signal_date)
                trend_regime, volatility_regime = "unknown", "unknown"
                if regime is not None:
                    trend_regime = str(regime["trend_regime"])
                    volatility_regime = str(regime["volatility_regime"])
                    if trend_regime in {"<NA>", "nan"}:
                        trend_regime = "unknown"
                    if volatility_regime in {"<NA>", "nan"}:
                        volatility_regime = "unknown"
                common = {
                    "symbol": str(symbol),
                    "signal_date": signal_date,
                    "horizon": horizon,
                    "trend_regime": trend_regime,
                    "volatility_regime": volatility_regime,
                }
                candidate_rows.append(
                    {
                        **common,
                        "net_return": float(net_return),
                    }
                )
                result = signal_results.get(int(position))
                if result is not None and result["exit_date"] <= split_end:
                    signal_rows.append({**common, **result})
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
