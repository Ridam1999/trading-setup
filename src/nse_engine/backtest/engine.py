"""Long-only event-driven daily portfolio simulation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from nse_engine.backtest.config import BacktestSettings
from nse_engine.backtest.costs import CostsConfig, calculate_order_cost
from nse_engine.backtest.fills import buy_limit_fill, sell_exit_fill, time_exit_fill
from nse_engine.features.indicators import atr
from nse_engine.setups.base import Setup, eligible_rows


@dataclass
class Position:
    """Open delivery position with its fixed initial stop and target."""

    symbol: str
    setup_name: str
    sector: str
    quantity: int
    entry_date: date
    entry_price: float
    stop_price: float
    target_price: float | None
    entry_charges: float
    entry_signal_date: date
    entry_notional: float


def _number(value: Any, default: float) -> float:
    """Return a finite numeric config value or the fallback."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return default
    return float(value)


def _sector(symbol: str, settings: BacktestSettings) -> str:
    """Resolve the configured sector for an instrument."""
    if symbol in settings.symbol_sectors:
        return settings.symbol_sectors[symbol]
    for sector_name, symbols in settings.sectors.items():
        if symbol in symbols:
            return sector_name
    return f"unclassified:{symbol}"


def _marked_equity(cash: float, positions: dict[str, Position], closes: dict[str, float]) -> float:
    """Return cash plus the close-marked value of open positions."""
    return cash + sum(
        position.quantity * closes.get(symbol, position.entry_price)
        for symbol, position in positions.items()
    )


def run_portfolio_backtest(
    frame: pd.DataFrame,
    setups: Sequence[Setup],
    settings: BacktestSettings,
    costs: CostsConfig,
    start_date: date,
    end_date: date,
    cost_multiplier: float = 1.0,
) -> dict[str, pd.DataFrame]:
    """Run a causal daily simulation and return equity, trades, and orders tables."""
    required_columns = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required_columns.difference(frame.columns)
    if missing:
        raise ValueError(f"Backtest data is missing columns: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("No OHLCV data is available for the portfolio backtest")
    if not setups:
        raise ValueError("At least one approved setup is required")
    if cost_multiplier <= 0:
        raise ValueError("Cost multiplier must be positive")

    bars = frame.sort_values(["date", "symbol"]).reset_index(drop=True).copy()
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    by_symbol = {
        symbol: group.sort_values("date").reset_index(drop=True)
        for symbol, group in bars.groupby("symbol")
    }
    symbols_by_date: dict[date, dict[str, pd.Series]] = {}
    for row in bars.itertuples(index=False):
        symbols_by_date.setdefault(row.date, {})[row.symbol] = pd.Series(row._asdict())
    dates = sorted(day for day in symbols_by_date if start_date <= day <= end_date)
    if len(dates) < 2:
        raise ValueError("Backtest range must contain at least two market dates")
    session_positions = {day: index for index, day in enumerate(dates)}

    signals_by_symbol: dict[tuple[int, str], pd.Series] = {}
    for setup in setups:
        setup_key = id(setup)
        for symbol, symbol_frame in by_symbol.items():
            eligible = eligible_rows(symbol_frame, setup.config)
            signal = setup.signals(symbol_frame).fillna(False).astype(bool) & eligible
            signals_by_symbol[(setup_key, str(symbol))] = signal

    cash = settings.initial_capital
    positions: dict[str, Position] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    stressed_costs = costs.model_copy(update={"slippage_bps": costs.slippage_bps * cost_multiplier})

    for date_index, current_date in enumerate(dates):
        todays_bars = symbols_by_date[current_date]
        # Exits are processed before new entries, releasing capital deterministically.
        for symbol in sorted(list(positions)):
            position = positions[symbol]
            bar = todays_bars.get(symbol)
            if bar is None:
                continue
            held_days = session_positions[current_date] - session_positions[position.entry_date] + 1
            exit_price, exit_reason = sell_exit_fill(
                bar, position.stop_price, position.target_price, stressed_costs.slippage_bps
            )
            terminal_session = date_index == len(dates) - 1
            if exit_reason is None and (
                held_days >= settings.maximum_holding_days or terminal_session
            ):
                exit_price = time_exit_fill(bar, stressed_costs.slippage_bps)
                if exit_price is not None:
                    exit_reason = "end_of_test" if terminal_session else "time"
            if exit_price is None or exit_reason is None:
                continue
            sell_charges = calculate_order_cost(
                "sell", exit_price, position.quantity, stressed_costs, cost_multiplier
            ).total
            proceeds = exit_price * position.quantity - sell_charges
            cash += proceeds
            gross_pnl = (exit_price - position.entry_price) * position.quantity
            net_pnl = gross_pnl - position.entry_charges - sell_charges
            trades.append(
                {
                    "symbol": symbol,
                    "setup": position.setup_name,
                    "sector": position.sector,
                    "signal_date": position.entry_signal_date,
                    "entry_date": position.entry_date,
                    "entry_price": position.entry_price,
                    "exit_date": current_date,
                    "exit_price": exit_price,
                    "quantity": position.quantity,
                    "gross_pnl": gross_pnl,
                    "net_pnl": net_pnl,
                    "net_return": net_pnl / position.entry_notional,
                    "costs_paid": position.entry_charges + sell_charges,
                    "holding_days": held_days,
                    "exit_reason": exit_reason,
                }
            )
            del positions[symbol]

        equity_before_entries = _marked_equity(
            cash,
            positions,
            {symbol: float(bar["close"]) for symbol, bar in todays_bars.items()},
        )
        for pending_order in sorted(
            pending, key=lambda item: (-item["rank_score"], item["setup"], item["symbol"])
        ):
            symbol = str(pending_order["symbol"])
            if symbol in positions or len(positions) >= settings.maximum_positions:
                orders.append(
                    {**pending_order, "entry_date": current_date, "status": "position_limit"}
                )
                continue
            bar = todays_bars.get(symbol)
            if bar is None:
                orders.append({**pending_order, "entry_date": current_date, "status": "no_bar"})
                continue
            fill = buy_limit_fill(
                float(pending_order["signal_close"]),
                bar,
                settings.entry_limit_buffer_bps,
                stressed_costs.slippage_bps,
            )
            if fill is None:
                orders.append({**pending_order, "entry_date": current_date, "status": "unfilled"})
                continue

            setup = pending_order["setup_object"]
            setup_exit = setup.config.exit_rules
            stop_pct = _number(setup_exit.get("stop_loss_pct"), settings.default_stop_loss_pct)
            symbol_bars = by_symbol[symbol]
            prior_rows = symbol_bars.loc[symbol_bars["date"] <= pending_order["signal_date"]]
            atr_value = float("nan")
            if len(prior_rows) >= 2:
                atr_value = float(
                    atr(
                        prior_rows["high"],
                        prior_rows["low"],
                        prior_rows["close"],
                        min(settings.atr_lookback, len(prior_rows)),
                    ).iloc[-1]
                )
            stop_distance = (
                atr_value * settings.stop_atr_multiple
                if math.isfinite(atr_value) and atr_value > 0
                else fill * stop_pct / 100
            )
            stop_price = max(fill - stop_distance, 0.0)
            target_pct = _number(
                setup_exit.get("target_pct"),
                settings.default_target_pct if settings.default_target_pct is not None else 0.0,
            )
            target_price = fill * (1 + target_pct / 100) if target_pct > 0 else None
            sector = _sector(symbol, settings)
            sector_value = 0.0
            for open_symbol, open_position in positions.items():
                if open_position.sector != sector:
                    continue
                current_bar = todays_bars.get(open_symbol)
                mark = (
                    float(current_bar["close"])
                    if current_bar is not None
                    else open_position.entry_price
                )
                sector_value += open_position.quantity * mark
            symbol_cap = math.floor(equity_before_entries * settings.maximum_symbol_weight / fill)
            sector_cap = math.floor(
                max(equity_before_entries * settings.maximum_sector_weight - sector_value, 0) / fill
            )
            risk_quantity = math.floor(
                equity_before_entries * settings.risk_per_trade_fraction / stop_distance
            )
            quantity = min(symbol_cap, sector_cap, risk_quantity)
            buy_charge = 0.0
            while quantity > 0:
                buy_charge = calculate_order_cost(
                    "buy", fill, quantity, stressed_costs, cost_multiplier
                ).total
                if fill * quantity + buy_charge <= cash:
                    break
                quantity -= 1
            if quantity <= 0:
                orders.append(
                    {**pending_order, "entry_date": current_date, "status": "cash_or_size_limit"}
                )
                continue

            entry_notional = fill * quantity
            cash -= entry_notional + buy_charge
            positions[symbol] = Position(
                symbol=symbol,
                setup_name=str(pending_order["setup"]),
                sector=sector,
                quantity=quantity,
                entry_date=current_date,
                entry_price=fill,
                stop_price=stop_price,
                target_price=target_price,
                entry_charges=buy_charge,
                entry_signal_date=pending_order["signal_date"],
                entry_notional=entry_notional,
            )
            orders.append(
                {
                    **{key: value for key, value in pending_order.items() if key != "setup_object"},
                    "entry_date": current_date,
                    "fill_price": fill,
                    "quantity": quantity,
                    "status": "filled",
                }
            )
        pending = []

        closes = {symbol: float(bar["close"]) for symbol, bar in todays_bars.items()}
        equity = _marked_equity(cash, positions, closes)
        benchmark = todays_bars.get(settings.benchmark_symbol)
        equity_rows.append(
            {
                "date": current_date,
                "cash": cash,
                "equity": equity,
                "exposure": equity - cash,
                "exposure_fraction": (equity - cash) / equity if equity else 0.0,
                "benchmark_close": float(benchmark["close"]) if benchmark is not None else np.nan,
                "open_positions": len(positions),
            }
        )

        if date_index + 1 >= len(dates):
            continue
        candidates: list[dict[str, Any]] = []
        for setup in setups:
            for symbol in sorted(by_symbol):
                if symbol in positions:
                    continue
                symbol_frame = by_symbol[symbol]
                locations = symbol_frame.index[symbol_frame["date"] == current_date]
                if len(locations) == 0:
                    continue
                signal_index = int(locations[0])
                signal = signals_by_symbol[(id(setup), str(symbol))]
                if not bool(signal.iloc[signal_index]):
                    continue
                candidates.append(
                    {
                        "signal_date": current_date,
                        "symbol": str(symbol),
                        "setup": setup.config.name,
                        "signal_close": float(symbol_frame.iloc[signal_index]["close"]),
                        "rank_score": float(
                            symbol_frame.iloc[signal_index]["close"]
                            * symbol_frame.iloc[signal_index]["volume"]
                        ),
                        "setup_object": setup,
                    }
                )
        pending = candidates

    return {
        "equity": pd.DataFrame(equity_rows),
        "trades": pd.DataFrame(trades),
        "orders": pd.DataFrame(orders),
    }
