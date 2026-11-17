"""Idempotent daily orchestration of scanning, fills, exits, and reporting."""

from __future__ import annotations

import math
import sqlite3
from bisect import bisect_right
from collections.abc import Sequence
from datetime import date
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.backtest.config import BacktestSettings
from nse_engine.backtest.costs import CostsConfig, calculate_order_cost
from nse_engine.backtest.fills import buy_limit_fill, sell_exit_fill, time_exit_fill
from nse_engine.features.indicators import atr
from nse_engine.paper.config import PaperSettings
from nse_engine.paper.decay import evaluate_decay, validated_band
from nse_engine.paper.ledger import OrderRecord, PaperLedger, PaperPosition, RunRecord
from nse_engine.paper.report import (
    DecayRow,
    PaperRunSummary,
    PlanRow,
    PositionRow,
    write_daily_report,
    write_order_plan,
)
from nse_engine.paper.scanner import SignalCandidate, scan_close
from nse_engine.setups.base import Setup, SetupConfig
from nse_engine.setups.registry import create_setup

REQUIRED_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume")


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


def _prepared_bars(frame: pd.DataFrame, run_date: date) -> tuple[pd.DataFrame, list[date]]:
    """Return causal data through ``run_date`` and its sorted observed sessions."""
    missing = set(REQUIRED_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"Paper data is missing columns: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("No OHLCV data is available for the paper run")
    bars = frame.copy().reset_index(drop=True)
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    history = bars.loc[bars["date"] <= run_date].reset_index(drop=True)
    dates = sorted(history["date"].unique())
    if run_date not in dates:
        raise ValueError(f"No market data is available on {run_date.isoformat()}")
    return history, dates


def _session_bars(bars: pd.DataFrame, day: date) -> dict[str, pd.Series]:
    """Return only one session's bars rather than materializing the full history."""
    rows = bars.loc[bars["date"].eq(day)]
    return {str(row["symbol"]): row for _, row in rows.iterrows()}


def _mark_price(bar: pd.Series | None, fallback: float) -> float:
    """Mark a position at the session close, falling back to its entry price."""
    return float(bar["close"]) if bar is not None else fallback


def _evaluate_all_decay(
    connection: sqlite3.Connection,
    ledger: PaperLedger,
    approved: list[dict[str, Any]],
    settings: PaperSettings,
) -> None:
    """Update every approved setup's decay state from closed paper trades."""
    for entry in approved:
        name = str(entry.get("setup", ""))
        try:
            band = validated_band(entry)
        except ValueError:
            continue
        previous_status, previous_count = ledger.setup_state(name, connection)
        returns = ledger.setup_trade_returns(name, connection)
        decision = evaluate_decay(
            name,
            returns,
            band,
            settings.decay,
            previous_status=previous_status,
            previous_breach_count=previous_count,
        )
        ledger.update_setup_state(
            name, decision.status, decision.reason, decision.breach_count, connection
        )


def _process_exits(
    connection: sqlite3.Connection,
    ledger: PaperLedger,
    settings: BacktestSettings,
    costs: CostsConfig,
    run_date: date,
    session_index: dict[date, int],
    todays_bars: dict[str, pd.Series],
    cash: float,
) -> float:
    """Close positions whose stop, target, or holding limit was reached today."""
    for position in ledger.open_positions(connection):
        bar = todays_bars.get(position.symbol)
        if bar is None:
            continue
        entry_index = session_index.get(position.open_date)
        if entry_index is None:
            continue
        held_days = session_index[run_date] - entry_index + 1
        exit_price, exit_reason = sell_exit_fill(
            bar, position.stop_price, position.target_price, costs.slippage_bps
        )
        if exit_reason is None and held_days >= settings.maximum_holding_days:
            exit_price = time_exit_fill(bar, costs.slippage_bps)
            if exit_price is not None:
                exit_reason = "time"
        if exit_price is None or exit_reason is None:
            continue
        charges = calculate_order_cost("sell", exit_price, position.quantity, costs).total
        entry_notional = position.entry_price * position.quantity
        proceeds = exit_price * position.quantity - charges
        net_pnl = proceeds - entry_notional - position.entry_charges
        cash += proceeds
        ledger.append_ledger(
            run_date,
            "exit",
            position.symbol,
            position.setup,
            position.quantity,
            exit_price,
            charges,
            proceeds,
            net_pnl=net_pnl,
            net_return=net_pnl / entry_notional,
            note=exit_reason,
            connection=connection,
        )
        ledger.close_position(position.position_id, connection)
        ledger.set_cash(cash, connection)
    return cash


def _process_pending_fills(
    connection: sqlite3.Connection,
    ledger: PaperLedger,
    settings: BacktestSettings,
    costs: CostsConfig,
    run_date: date,
    dates: list[date],
    bars: pd.DataFrame,
    cash: float,
) -> tuple[float, int]:
    """Simulate next-session limit fills for orders created on earlier runs."""
    fills = 0
    session_cache: dict[date, dict[str, pd.Series]] = {}
    for order in ledger.pending_orders(connection):
        if order.run_date >= run_date:
            continue
        next_session = bisect_right(dates, order.run_date)
        if next_session >= len(dates):
            continue
        fill_date = dates[next_session]
        if fill_date > run_date:
            continue
        if fill_date not in session_cache:
            session_cache[fill_date] = _session_bars(bars, fill_date)
        bar = session_cache[fill_date].get(order.symbol)
        if bar is None:
            ledger.update_order_status(order.order_id, "no_bar", connection)
            continue
        fill_price = buy_limit_fill(
            order.signal_close, bar, settings.entry_limit_buffer_bps, costs.slippage_bps
        )
        if fill_price is None:
            ledger.update_order_status(order.order_id, "unfilled", connection)
            ledger.record_fill(
                order.order_id, fill_date, float(bar["close"]), 0, "unfilled", 0.0, connection
            )
            continue
        charges = calculate_order_cost("buy", fill_price, order.quantity, costs).total
        notional = fill_price * order.quantity + charges
        if notional > cash:
            ledger.update_order_status(order.order_id, "cash_limit", connection)
            ledger.record_fill(
                order.order_id, fill_date, fill_price, 0, "cash_limit", 0.0, connection
            )
            continue
        cash -= notional
        ledger.update_order_status(order.order_id, "filled", connection)
        ledger.record_fill(
            order.order_id, fill_date, fill_price, order.quantity, "filled", charges, connection
        )
        ledger.open_position(
            PaperPosition(
                position_id=f"pos-{order.order_id}",
                symbol=order.symbol,
                setup=order.setup,
                open_date=fill_date,
                quantity=order.quantity,
                entry_price=fill_price,
                stop_price=order.stop_price,
                target_price=order.target_price,
                entry_charges=charges,
            ),
            connection,
        )
        ledger.append_ledger(
            fill_date,
            "buy",
            order.symbol,
            order.setup,
            order.quantity,
            fill_price,
            charges,
            -notional,
            note="paper limit entry",
            connection=connection,
        )
        ledger.set_cash(cash, connection)
        fills += 1
    return cash, fills


def _plan_orders(
    connection: sqlite3.Connection,
    ledger: PaperLedger,
    bars: pd.DataFrame,
    setups: Sequence[Setup],
    candidates: list[SignalCandidate],
    settings: BacktestSettings,
    costs: CostsConfig,
    run_date: date,
    todays_bars: dict[str, pd.Series],
) -> int:
    """Size and append one deterministic limit order per accepted candidate."""
    by_name = {setup.config.name: setup for setup in setups}
    cash = ledger.cash_balance(connection)
    positions = ledger.open_positions(connection)
    mark_prices = {symbol: _mark_price(bar, 0.0) for symbol, bar in todays_bars.items()}
    equity = cash + sum(
        position.quantity * mark_prices.get(position.symbol, position.entry_price)
        for position in positions
    )
    if equity <= 0:
        return 0
    slots = settings.maximum_positions - len(positions)
    created = 0
    for candidate in candidates:
        if slots <= 0:
            ledger.record_signal(
                run_date,
                candidate.symbol,
                candidate.setup,
                candidate.signal_close,
                False,
                "position_limit",
                connection,
            )
            continue
        setup = by_name.get(candidate.setup)
        if setup is None:
            ledger.record_signal(
                run_date,
                candidate.symbol,
                candidate.setup,
                candidate.signal_close,
                False,
                "unknown_setup",
                connection,
            )
            continue
        prior = bars.loc[bars["symbol"].eq(candidate.symbol) & bars["date"].le(run_date)]
        atr_value = float("nan")
        if len(prior) >= 2:
            atr_value = float(
                atr(
                    prior["high"],
                    prior["low"],
                    prior["close"],
                    min(settings.atr_lookback, len(prior)),
                ).iloc[-1]
            )
        exit_rules = setup.config.exit_rules
        stop_pct = _number(exit_rules.get("stop_loss_pct"), settings.default_stop_loss_pct)
        stop_distance = (
            atr_value * settings.stop_atr_multiple
            if math.isfinite(atr_value) and atr_value > 0
            else candidate.signal_close * stop_pct / 100
        )
        if stop_distance <= 0:
            ledger.record_signal(
                run_date,
                candidate.symbol,
                candidate.setup,
                candidate.signal_close,
                False,
                "invalid_stop_distance",
                connection,
            )
            continue
        stop_price = max(candidate.signal_close - stop_distance, 0.0)
        target_pct = _number(
            exit_rules.get("target_pct"),
            settings.default_target_pct if settings.default_target_pct is not None else 0.0,
        )
        target_price = candidate.signal_close * (1 + target_pct / 100) if target_pct > 0 else None
        limit_price = candidate.signal_close * (1 + settings.entry_limit_buffer_bps / 10000)

        sector = _sector(candidate.symbol, settings)
        sector_value = sum(
            position.quantity * mark_prices.get(position.symbol, position.entry_price)
            for position in positions
            if _sector(position.symbol, settings) == sector
        )
        symbol_cap = math.floor(equity * settings.maximum_symbol_weight / limit_price)
        sector_cap = math.floor(
            max(equity * settings.maximum_sector_weight - sector_value, 0) / limit_price
        )
        risk_quantity = math.floor(equity * settings.risk_per_trade_fraction / stop_distance)
        quantity = min(symbol_cap, sector_cap, risk_quantity)
        charges = 0.0
        while quantity > 0:
            charges = calculate_order_cost("buy", limit_price, quantity, costs).total
            if limit_price * quantity + charges <= cash:
                break
            quantity -= 1
        if quantity <= 0:
            ledger.record_signal(
                run_date,
                candidate.symbol,
                candidate.setup,
                candidate.signal_close,
                False,
                "cash_or_size_limit",
                connection,
            )
            continue
        order_id = f"paper-{run_date.isoformat()}-{candidate.setup}-{candidate.symbol}"
        ledger.insert_order(
            OrderRecord(
                order_id=order_id,
                run_date=run_date,
                symbol=candidate.symbol,
                setup=candidate.setup,
                side="buy",
                limit_price=limit_price,
                signal_close=candidate.signal_close,
                quantity=quantity,
                stop_price=stop_price,
                target_price=target_price,
                reason=f"close_of_day_signal:{candidate.setup}",
                status="pending",
                client_ref=order_id,
            ),
            connection,
        )
        ledger.record_signal(
            run_date,
            candidate.symbol,
            candidate.setup,
            candidate.signal_close,
            True,
            "accepted",
            connection,
        )
        cash -= limit_price * quantity + charges
        slots -= 1
        created += 1
    return created


def _build_summary(
    connection: sqlite3.Connection,
    ledger: PaperLedger,
    bars: pd.DataFrame,
    run_date: date,
    approved: list[dict[str, Any]],
    todays_bars: dict[str, pd.Series],
) -> PaperRunSummary:
    """Rebuild a report-ready summary from committed ledger state."""
    record = ledger.run_record(run_date, connection)
    if record is None:
        raise RuntimeError(f"No paper run record found for {run_date.isoformat()}")
    plans = [
        PlanRow(
            order_id=order.order_id,
            symbol=order.symbol,
            setup=order.setup,
            limit_price=order.limit_price,
            quantity=order.quantity,
            stop_price=order.stop_price,
            target_price=order.target_price,
            reason=order.reason,
            status=order.status,
        )
        for order in ledger.orders_for_run(run_date, connection)
    ]
    positions = [
        PositionRow(
            symbol=position.symbol,
            setup=position.setup,
            open_date=position.open_date,
            quantity=position.quantity,
            entry_price=position.entry_price,
            mark_price=_mark_price(todays_bars.get(position.symbol), position.entry_price),
            unrealised_pnl=(
                _mark_price(todays_bars.get(position.symbol), position.entry_price)
                - position.entry_price
            )
            * position.quantity,
            unrealised_return=(
                _mark_price(todays_bars.get(position.symbol), position.entry_price)
                / position.entry_price
                - 1
            ),
        )
        for position in ledger.open_positions(connection)
    ]
    bands = {}
    for entry in approved:
        try:
            bands[str(entry.get("setup", ""))] = validated_band(entry)
        except ValueError:
            continue
    decay_rows: list[DecayRow] = []
    for name in sorted(bands):
        status, _count, reason = ledger.setup_decay_detail(name, connection)
        returns = ledger.setup_trade_returns(name, connection)
        band = bands[name]
        decay_rows.append(
            DecayRow(
                setup=name,
                status=status,
                live_hit_rate=sum(value > 0 for value in returns) / len(returns)
                if returns
                else 0.0,
                live_adverse_return=min(returns) if returns else 0.0,
                validated_hit_rate=band.hit_rate,
                validated_adverse_return=band.adverse_return,
                reason=reason,
            )
        )
    realised_pnl, costs_paid = ledger.performance_totals(run_date, connection)
    cash = ledger.cash_balance(connection)
    equity = cash + sum(row.unrealised_pnl + row.entry_price * row.quantity for row in positions)
    suspended = [row.setup for row in decay_rows if row.status == "suspended"]
    return PaperRunSummary(
        run_date=run_date,
        signals_count=record.signals_count,
        orders_count=record.orders_count,
        fills_count=record.fills_count,
        approved_setups=len(approved),
        suspended_setups=suspended,
        plans=plans,
        positions=positions,
        realised_pnl=realised_pnl,
        costs_paid=costs_paid,
        cash=cash,
        equity=equity,
        decay_rows=decay_rows,
    )


def run_paper_day(
    run_date: date,
    frame: pd.DataFrame,
    approved: list[dict[str, Any]],
    settings: BacktestSettings,
    costs: CostsConfig,
    paper: PaperSettings,
    ledger: PaperLedger,
    data_bias_status: str,
    membership: pd.Series | None = None,
    setups: Sequence[Setup] | None = None,
) -> PaperRunSummary:
    """Run one calendar date once, then emit its report and JSON order plan."""
    bars, dates = _prepared_bars(frame, run_date)
    session_index = {day: index for index, day in enumerate(dates)}
    todays_bars = _session_bars(bars, run_date)
    active_setups = (
        list(setups)
        if setups is not None
        else [create_setup(SetupConfig.model_validate(entry["parameters"])) for entry in approved]
    )

    with ledger.transaction() as connection:
        if ledger.has_run(run_date, connection):
            summary = _build_summary(connection, ledger, bars, run_date, approved, todays_bars)
        else:
            ledger.initialize_capital(settings.initial_capital, connection)
            _evaluate_all_decay(connection, ledger, approved, paper)
            cash = _process_exits(
                connection,
                ledger,
                settings,
                costs,
                run_date,
                session_index,
                todays_bars,
                ledger.cash_balance(connection),
            )
            _cash, fills = _process_pending_fills(
                connection, ledger, settings, costs, run_date, dates, bars, cash
            )
            suspended = ledger.suspended_setups(connection)
            candidates = scan_close(bars, active_setups, run_date, suspended, membership)
            orders = _plan_orders(
                connection,
                ledger,
                bars,
                active_setups,
                candidates,
                settings,
                costs,
                run_date,
                todays_bars,
            )
            ledger.record_run(
                RunRecord(
                    run_date=run_date,
                    status="completed",
                    signals_count=len(candidates),
                    orders_count=orders,
                    fills_count=fills,
                ),
                connection,
            )
            summary = _build_summary(connection, ledger, bars, run_date, approved, todays_bars)

    report_dir = paper.reports_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    write_order_plan(summary.plans, report_dir / f"orders_{run_date.isoformat()}.json")
    write_daily_report(summary, report_dir / f"paper_{run_date.isoformat()}.md", data_bias_status)
    return summary
