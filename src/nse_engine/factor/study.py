"""Frozen cross-sectional factor study; no approvals and no live orders.

This module consumes only point-in-time files created by the acquisition and
fundamentals commands. Signals use data available on or before the rebalance
date, and the existing limit-order fill model creates orders for the next
session only.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from nse_engine.backtest.costs import CostsConfig, round_trip_cost
from nse_engine.backtest.fills import buy_limit_fill, time_exit_fill
from nse_engine.factor.acquire import load_pit_bars
from nse_engine.factor.config import (
    FULL_HYPOTHESES,
    FUNDAMENTAL_AVERAGE_EQUITY,
    FUNDAMENTAL_DILUTED_EPS,
    FUNDAMENTAL_NET_PROFIT,
    FactorConfig,
    FactorDataConfig,
    Hypothesis,
    HypothesisId,
    requires_fundamentals,
    resolve_hypotheses,
)
from nse_engine.factor.readiness import verify_preregistration
from nse_engine.setups.base import SetupConfig
from nse_engine.study.config import GateSettings, StudySettings
from nse_engine.study.splits import SplitName, configured_split
from nse_engine.study.stats import (
    benjamini_hochberg,
    date_clustered_t_statistic,
    return_summary,
    symbol_breadth,
    yearly_returns,
)
from nse_engine.study.trials import TrialRegistry

DISCLAIMER = "Research output, not advice; no guarantee of profit."
CANDIDATE_BANNER = "SURVIVOR_BIASED: FACTOR RESULTS ARE CANDIDATES ONLY; NOT APPROVAL EVIDENCE"
RETURN_COLUMNS: tuple[str, ...] = (
    "signal_date",
    "hypothesis",
    "symbol",
    "horizon",
    "net_return",
    "excess_equal_weight",
    "excess_niftybees",
    "entry_date",
    "exit_date",
)


class _Bar(NamedTuple):
    """One synthetic next-session fill bar derived from point-in-time prices."""

    open: float
    low: float
    close: float
    circuit_locked: bool = False


@dataclass(frozen=True)
class FactorStudyResult:
    """One hypothesis/horizon/split result with its locked gate outcomes."""

    split: str
    hypothesis_id: str
    hypothesis_name: str
    horizon_months: int
    summary: dict[str, Any]
    excess_summary: dict[str, Any]
    niftybees_excess: dict[str, Any]
    adjusted_p_value: float
    gates: dict[str, bool]
    yearly: tuple[dict[str, Any], ...]
    candidate: bool


@dataclass(frozen=True)
class FactorStudyOutput:
    """All results and the report path from one execution."""

    results: tuple[FactorStudyResult, ...]
    report_path: Path
    trials_appended: int
    hypotheses: tuple[HypothesisId, ...] = FULL_HYPOTHESES
    complete_design: bool = True


def _verify_design(config: FactorConfig, prereg_path: Path) -> None:
    """Refuse to read PIT data if the frozen design changed after commit."""
    if not verify_preregistration(prereg_path, config.prereg_sha256):
        raise PermissionError("Frozen factor design hash no longer matches the committed document")


def _empty_returns() -> pd.DataFrame:
    """Return the stable empty schema for holding-period returns."""
    return pd.DataFrame(columns=list(RETURN_COLUMNS))


def _load_inputs(
    data: FactorDataConfig, hypothesis_ids: Sequence[HypothesisId] | None = None
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame | None]:
    """Load only the point-in-time inputs required by the selected hypotheses."""
    if data.pit_prices_dir is None or data.membership_path is None:
        raise ValueError("Point-in-time price and membership inputs are not configured")
    prices = load_pit_bars(data.pit_prices_dir)
    membership = pd.read_parquet(data.membership_path)
    if prices.empty:
        raise ValueError("Point-in-time price store is empty")
    missing_membership = {"rebalance_date", "symbol"}.difference(membership.columns)
    if missing_membership:
        raise ValueError(f"membership is missing columns: {', '.join(sorted(missing_membership))}")
    prices = prices.assign(_date=pd.to_datetime(prices["date"]).dt.date)
    membership = membership.assign(
        _rebalance_date=pd.to_datetime(membership["rebalance_date"]).dt.date
    )

    selected_ids = tuple(hypothesis_ids) if hypothesis_ids is not None else FULL_HYPOTHESES
    if not requires_fundamentals(selected_ids):
        return prices, membership, None
    if data.fundamentals_path is None:
        raise ValueError("Announcement-dated fundamentals are required for the selected hypotheses")
    fundamentals = pd.read_parquet(data.fundamentals_path)
    missing_fundamentals = {"symbol", "announcement_date", "field", "value"}.difference(
        fundamentals.columns
    )
    if missing_fundamentals:
        raise ValueError(
            f"fundamentals is missing columns: {', '.join(sorted(missing_fundamentals))}"
        )
    fundamentals = fundamentals.assign(
        _announced=pd.to_datetime(fundamentals["announcement_date"]).dt.date
    )
    return prices, membership, fundamentals


def _winsorized_zscore(values: pd.Series, fraction: float) -> pd.Series:
    """Winsorize a cross-section, then return a unit-variance z-score."""
    finite = pd.to_numeric(values, errors="coerce")
    lower = finite.quantile(fraction)
    upper = finite.quantile(1 - fraction)
    clipped = finite.clip(lower=lower, upper=upper)
    deviation = float(clipped.std(ddof=0))
    if deviation == 0 or not np.isfinite(deviation):
        return pd.Series(0.0, index=values.index)
    return (clipped - clipped.mean()) / deviation


def _apply_turnover_cap(
    target: list[str], previous: tuple[str, ...], maximum_fraction: float, size: int
) -> tuple[str, ...]:
    """Keep enough prior holdings to respect the configured one-way cap."""
    if not previous:
        return tuple(target[:size])
    allowed = int(np.floor(maximum_fraction * size))
    target_set = set(target)
    kept = [symbol for symbol in previous if symbol in target_set][:size]
    additions = size - len(kept)
    if additions > allowed:
        extra = [symbol for symbol in previous if symbol not in target_set][: additions - allowed]
        kept.extend(extra)
        additions = size - len(kept)
    kept_set = set(kept)
    kept.extend(symbol for symbol in target if symbol not in kept_set)
    return tuple(kept[:size])


def _eligible_cutoff(rebalance_date: date, sessions: list[date], lag: int) -> date | None:
    """Return the latest session date eligible under the configured lag."""
    index = bisect_right(sessions, rebalance_date) - 1
    target = index - lag
    return sessions[target] if 0 <= target < len(sessions) else None


def _latest_close(
    histories: dict[str, list[tuple[date, float]]], symbol: str, as_of: date
) -> float | None:
    """Return the last close at or before a date, never a future price."""
    closes = [close for day, close in histories.get(symbol, []) if day <= as_of]
    return closes[-1] if closes else None


def _fundamental_values(
    symbol: str,
    cutoff: date | None,
    price: float | None,
    fundamentals: pd.DataFrame,
) -> tuple[float, float]:
    """Return earnings yield and ROE from the latest announcement as of cutoff."""
    if cutoff is None or price is None or price <= 0:
        return float("nan"), float("nan")
    rows = fundamentals.loc[
        (fundamentals["symbol"] == symbol) & (fundamentals["_announced"] <= cutoff)
    ]
    if rows.empty:
        return float("nan"), float("nan")
    latest = rows.loc[rows["_announced"] == rows["_announced"].max()]
    eps = pd.to_numeric(
        latest.loc[latest["field"] == FUNDAMENTAL_DILUTED_EPS, "value"], errors="coerce"
    )
    profit = pd.to_numeric(
        latest.loc[latest["field"] == FUNDAMENTAL_NET_PROFIT, "value"], errors="coerce"
    )
    equity = pd.to_numeric(
        latest.loc[latest["field"] == FUNDAMENTAL_AVERAGE_EQUITY, "value"], errors="coerce"
    )
    if eps.empty or profit.empty or equity.empty:
        return float("nan"), float("nan")
    average_equity = float(equity.iloc[0])
    earnings_yield = float(eps.iloc[0]) / price
    roe = float(profit.iloc[0]) / average_equity if average_equity > 0 else float("nan")
    return earnings_yield, roe


def _momentum_value(
    symbol: str,
    rebalance_date: date,
    histories: dict[str, list[tuple[date, float]]],
    skip: int,
    lookback: int,
) -> float:
    """Return close[t-skip] / close[t-lookback] - 1 using only past rows."""
    closes = [close for day, close in histories.get(symbol, []) if day <= rebalance_date]
    if len(closes) <= lookback:
        return float("nan")
    recent = closes[-skip - 1]
    distant = closes[-lookback - 1]
    if distant <= 0:
        return float("nan")
    return float(recent / distant - 1)


def compute_factor_signals(
    prices: pd.DataFrame,
    membership: pd.DataFrame,
    fundamentals: pd.DataFrame | None,
    factor: FactorConfig,
    rebalance_dates: list[date],
    hypothesis_ids: Sequence[HypothesisId] | None = None,
) -> pd.DataFrame:
    """Compute causal ranked baskets for the selected frozen hypotheses."""
    selected = resolve_hypotheses(factor, hypothesis_ids)
    selected_ids = tuple(item.id for item in selected)
    needs_fundamentals = requires_fundamentals(selected_ids)
    if needs_fundamentals and fundamentals is None:
        raise ValueError("Fundamental values are required for the selected hypotheses")
    value_columns: tuple[HypothesisId, ...] = ("H1", "H2", "H3") if needs_fundamentals else ("H3",)

    sessions = sorted(set(prices["_date"]))
    histories: dict[str, list[tuple[date, float]]] = {}
    for symbol, group in prices.groupby("symbol", sort=True):
        clean = group.dropna(subset=["_date", "close"])
        histories[str(symbol).strip().upper()] = [
            (day, float(close))
            for day, close in clean.sort_values("_date")[["_date", "close"]].itertuples(
                index=False, name=None
            )
        ]
    membership_by_date: dict[date, list[str]] = {}
    for rebalance_date, group in membership.groupby("_rebalance_date", sort=True):
        membership_by_date[rebalance_date] = sorted(
            str(symbol).strip().upper() for symbol in group["symbol"]
        )

    rows: list[dict[str, Any]] = []
    previous_baskets: dict[str, tuple[str, ...]] = {}
    basket_size = max(1, int(np.ceil(factor.universe.size * factor.construction.basket_fraction)))
    lag = factor.fundamentals.announcement_lag_sessions

    for rebalance_date in rebalance_dates:
        universe = membership_by_date.get(rebalance_date, [])
        if not universe:
            continue
        cutoff = _eligible_cutoff(rebalance_date, sessions, lag)
        values: dict[str, list[float]] = {}
        for symbol in universe:
            by_column: dict[HypothesisId, float] = {}
            if needs_fundamentals and fundamentals is not None:
                price = _latest_close(histories, symbol, rebalance_date)
                earnings_yield, roe = _fundamental_values(symbol, cutoff, price, fundamentals)
                by_column["H1"] = earnings_yield
                by_column["H2"] = roe
            if "H3" in value_columns:
                by_column["H3"] = _momentum_value(
                    symbol,
                    rebalance_date,
                    histories,
                    factor.price.momentum_skip_recent_sessions,
                    factor.price.momentum_lookback_sessions,
                )
            values[symbol] = [by_column[column] for column in value_columns]

        frame = pd.DataFrame.from_dict(values, orient="index", columns=list(value_columns))
        zscore_data = {
            column: _winsorized_zscore(frame[column], factor.winsorize_fraction)
            for column in value_columns
        }
        zscores = pd.DataFrame(zscore_data)
        if "H4" in selected_ids:
            zscores["H4"] = zscores[["H1", "H2", "H3"]].mean(axis=1)

        for hypothesis in selected:
            identifier = hypothesis.id
            if identifier == "H4":
                scores = zscores["H4"]
                display = scores
            else:
                scores = zscores[identifier]
                display = frame[identifier]
            ranked = scores.dropna().sort_values(
                ascending=hypothesis.expected_sign == "negative", kind="mergesort"
            )
            if ranked.empty:
                continue
            target = [str(symbol) for symbol in ranked.index[:basket_size]]
            previous = previous_baskets.get(identifier, ())
            basket = _apply_turnover_cap(
                target,
                previous,
                factor.construction.max_one_way_turnover_fraction,
                basket_size,
            )
            previous_baskets[identifier] = basket
            for rank, symbol in enumerate(basket, start=1):
                rows.append(
                    {
                        "signal_date": rebalance_date,
                        "hypothesis": identifier,
                        "symbol": symbol,
                        "rank": rank,
                        "factor_value": float(display.get(symbol, float("nan"))),
                    }
                )
    return pd.DataFrame.from_records(
        rows, columns=["signal_date", "hypothesis", "symbol", "rank", "factor_value"]
    )


def _cost_fraction(
    entry_price: float,
    exit_price: float,
    quantity: float,
    costs: CostsConfig,
    multiplier: float,
) -> float:
    """Return round-trip charges as a fraction of entry notional."""
    if entry_price <= 0 or exit_price <= 0 or quantity <= 0:
        return float("nan")
    charges = round_trip_cost(entry_price, exit_price, quantity, costs, multiplier)
    return float(charges / (entry_price * quantity))


def _latest_bar(
    bars: dict[tuple[str, date], _Bar], sessions: list[date], symbol: str, as_of: date
) -> tuple[date, _Bar] | None:
    """Return the latest bar at or before a date for one symbol."""
    index = bisect_right(sessions, as_of) - 1
    while index >= 0:
        day = sessions[index]
        bar = bars.get((symbol, day))
        if bar is not None:
            return day, bar
        index -= 1
    return None


def build_period_returns(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    membership: pd.DataFrame,
    study: StudySettings,
    costs: CostsConfig,
    multiplier: float,
    horizons: Sequence[int],
    signal_lag: int,
) -> pd.DataFrame:
    """Build net, benchmark, and excess returns for each selected basket holding."""
    if signal_lag < 1:
        raise ValueError("Signal-to-order lag must be at least one session")
    if signals.empty:
        return _empty_returns()
    sessions = sorted(set(prices["_date"]))
    bars: dict[tuple[str, date], _Bar] = {}
    for symbol, day, open_price, low_price, close in prices[
        ["symbol", "_date", "open", "low", "close"]
    ].itertuples(index=False, name=None):
        bars[(str(symbol).strip().upper(), day)] = _Bar(
            float(open_price), float(low_price), float(close)
        )
    membership_by_date: dict[date, list[str]] = {}
    for day, group in membership.groupby("_rebalance_date", sort=True):
        membership_by_date[day] = sorted(str(symbol).strip().upper() for symbol in group["symbol"])
    basket_sizes = signals.groupby(["signal_date", "hypothesis"])["symbol"].size().to_dict()

    benchmark_cache: dict[tuple[date, int, str], float] = {}

    def benchmark_return(signal_date: date, horizon: int, kind: str) -> float:
        """Return an equally weighted or reference benchmark net return."""
        key = (signal_date, horizon, kind)
        if key in benchmark_cache:
            return benchmark_cache[key]
        entry_position = bisect_right(sessions, signal_date) + signal_lag - 1
        if entry_position >= len(sessions):
            benchmark_cache[key] = float("nan")
            return float("nan")
        entry = sessions[entry_position]
        target = pd.Timestamp(signal_date) + pd.DateOffset(months=horizon)
        exit_index = bisect_left(sessions, target.date())
        if exit_index >= len(sessions):
            benchmark_cache[key] = float("nan")
            return float("nan")
        exit_day = sessions[exit_index]
        symbols = (
            membership_by_date.get(signal_date, [])
            if kind == "equal_weight_pit_universe"
            else [study.benchmark_symbol.strip().upper()]
        )
        if not symbols:
            benchmark_cache[key] = float("nan")
            return float("nan")
        per_name_notional = study.notional_rupees / len(symbols)
        values: list[float] = []
        for symbol in symbols:
            signal_bar = _latest_bar(bars, sessions, symbol, signal_date)
            entry_bar = bars.get((symbol, entry))
            exit_bar = bars.get((symbol, exit_day))
            if signal_bar is None or entry_bar is None or exit_bar is None:
                continue
            entry_fill = buy_limit_fill(
                signal_bar[1].close,
                pd.Series(entry_bar._asdict()),
                study.entry_limit_buffer_bps,
                costs.slippage_bps * multiplier,
            )
            exit_fill = time_exit_fill(
                pd.Series(exit_bar._asdict()), costs.slippage_bps * multiplier
            )
            if entry_fill is None or exit_fill is None:
                continue
            charge = _cost_fraction(entry_fill, exit_fill, per_name_notional, costs, multiplier)
            values.append(exit_fill / entry_fill - 1 - charge)
        result = float(np.mean(values)) if values else float("nan")
        benchmark_cache[key] = result
        return result

    rows: list[dict[str, Any]] = []
    for row in signals.to_dict("records"):
        signal_date = row["signal_date"]
        symbol = str(row["symbol"]).strip().upper()
        entry_position = bisect_right(sessions, signal_date) + signal_lag - 1
        if entry_position >= len(sessions):
            continue
        entry = sessions[entry_position]
        signal_bar = _latest_bar(bars, sessions, symbol, signal_date)
        entry_bar = bars.get((symbol, entry))
        if signal_bar is None or entry_bar is None:
            continue
        entry_fill = buy_limit_fill(
            signal_bar[1].close,
            pd.Series(entry_bar._asdict()),
            study.entry_limit_buffer_bps,
            costs.slippage_bps * multiplier,
        )
        if entry_fill is None:
            continue
        count = basket_sizes.get((signal_date, row["hypothesis"]), 0)
        if count < 1:
            continue
        notional = study.notional_rupees / count
        for horizon in horizons:
            target = pd.Timestamp(signal_date) + pd.DateOffset(months=horizon)
            exit_index = bisect_left(sessions, target.date())
            if exit_index >= len(sessions):
                continue
            exit_day = sessions[exit_index]
            exit_bar = bars.get((symbol, exit_day))
            if exit_bar is None:
                continue
            exit_fill = time_exit_fill(
                pd.Series(exit_bar._asdict()), costs.slippage_bps * multiplier
            )
            if exit_fill is None:
                continue
            charge = _cost_fraction(entry_fill, exit_fill, notional, costs, multiplier)
            net = exit_fill / entry_fill - 1 - charge
            rows.append(
                {
                    "signal_date": signal_date,
                    "hypothesis": row["hypothesis"],
                    "symbol": symbol,
                    "horizon": horizon,
                    "net_return": net,
                    "excess_equal_weight": net
                    - benchmark_return(signal_date, horizon, "equal_weight_pit_universe"),
                    "excess_niftybees": net - benchmark_return(signal_date, horizon, "niftybees"),
                    "entry_date": entry,
                    "exit_date": exit_day,
                }
            )
    return pd.DataFrame.from_records(rows, columns=list(RETURN_COLUMNS))


def _yearly_stats(yearly: pd.DataFrame) -> tuple[float, float]:
    """Return positive-year fraction and maximum single-year profit share."""
    if yearly.empty:
        return 0.0, 1.0
    positive_year_fraction = float(yearly["expectancy"].gt(0).mean())
    positive_profit = yearly["total_profit"].clip(lower=0)
    profit_total = float(positive_profit.sum())
    maximum_profit_fraction = (
        float(positive_profit.max() / profit_total) if profit_total > 0 else 1.0
    )
    return positive_year_fraction, maximum_profit_fraction


def evaluate_factor_gates(
    summary: dict[str, Any],
    excess_summary: dict[str, Any],
    nifty_summary: dict[str, Any],
    adjusted_p_value: float,
    yearly: pd.DataFrame,
    breadth: float,
    factor: FactorConfig,
    gate_settings: GateSettings,
    stress_key: str,
) -> dict[str, bool]:
    """Apply the frozen factor gates; parameter-neighbour stability is not applicable."""
    minimum_dates = max(
        factor.minimum_independent_rebalance_dates, gate_settings.minimum_independent_dates
    )
    stress_expectancy = summary.get(stress_key, {}).get("expectancy")
    positive_year_fraction, maximum_profit_fraction = _yearly_stats(yearly)
    adjusted_threshold = min(factor.fdr_alpha, gate_settings.maximum_adjusted_p_value)
    return {
        "minimum_independent_rebalance_dates": int(summary.get("n_independent_dates", 0))
        >= minimum_dates,
        stress_key: isinstance(stress_expectancy, int | float) and stress_expectancy > 0,
        "positive_excess_equal_weight_universe": bool(
            excess_summary.get("expectancy", float("nan")) > 0
        ),
        "positive_excess_niftybees": bool(nifty_summary.get("expectancy", float("nan")) > 0),
        "multiple_testing_adjusted_significance": bool(adjusted_p_value <= adjusted_threshold),
        "positive_calendar_year_fraction": positive_year_fraction
        >= gate_settings.minimum_positive_year_fraction,
        "positive_symbol_breadth": breadth >= gate_settings.minimum_positive_symbol_fraction,
        "single_year_profit_concentration": maximum_profit_fraction
        <= gate_settings.maximum_single_year_profit_fraction,
    }


def _trial_config(
    hypothesis: Hypothesis,
    horizon: int,
    factor: FactorConfig,
    hypothesis_ids: tuple[HypothesisId, ...],
    complete_design: bool,
) -> SetupConfig:
    """Represent one scoped hypothesis/horizon pair in the append-only registry."""
    return SetupConfig(
        name=f"factor_{hypothesis.id.lower()}_{hypothesis.name.lower()}",
        universe=f"pit_traded_value_top_{factor.universe.size}_delisted_inclusive",
        rationale=hypothesis.definition,
        params={
            "hypothesis_id": hypothesis.id,
            "hypothesis_scope": ",".join(hypothesis_ids),
            "complete_frozen_design": complete_design,
            "horizon_months": horizon,
            "expected_sign": hypothesis.expected_sign,
            "universe_size": factor.universe.size,
            "liquidity_lookback_sessions": factor.universe.liquidity_lookback_sessions,
            "basket_fraction": factor.construction.basket_fraction,
            "rebalance_frequency": factor.construction.rebalance_frequency,
            "max_one_way_turnover_fraction": factor.construction.max_one_way_turnover_fraction,
            "equal_weight": factor.construction.equal_weight,
            "signal_to_order_lag_sessions": factor.construction.signal_to_order_lag_sessions,
            "announcement_lag_sessions": factor.fundamentals.announcement_lag_sessions,
            "momentum_skip_recent_sessions": factor.price.momentum_skip_recent_sessions,
            "momentum_lookback_sessions": factor.price.momentum_lookback_sessions,
            "winsorize_fraction": factor.winsorize_fraction,
            "cost_stress_multiplier": factor.cost_stress_multiplier,
            "fdr_alpha": factor.fdr_alpha,
        },
        filters={},
        exit_rules={},
        sweep_grid={},
    )


def run_factor_study(
    factor: FactorConfig,
    data: FactorDataConfig,
    study: StudySettings,
    gate_settings: GateSettings,
    costs: CostsConfig,
    registry: TrialRegistry,
    prereg_path: Path,
    config_directory: Path | None = None,
    include_holdout: bool = False,
    hypothesis_ids: Sequence[HypothesisId] | None = None,
) -> FactorStudyOutput:
    """Run the selected hypotheses for train/validation, optionally audited holdout."""
    _verify_design(factor, prereg_path)
    selected = resolve_hypotheses(factor, hypothesis_ids)
    selected_ids = tuple(item.id for item in selected)
    complete_design = selected_ids == FULL_HYPOTHESES
    if factor.cost_stress_multiplier < gate_settings.minimum_cost_stress_multiplier:
        raise ValueError(
            "Factor cost stress is below the locked gate floor; no trials may be appended"
        )
    splits: tuple[SplitName, ...] = ("train", "validation")
    if include_holdout:
        registry.record_holdout_unlock()
        splits += ("holdout",)
    bounds = {name: configured_split(name, config_directory) for name in splits}

    prices, membership, fundamentals = _load_inputs(data, selected_ids)
    observed_dates = sorted(set(prices["_date"]))
    if not observed_dates:
        raise ValueError("Point-in-time price store has no valid dates")
    for name in splits:
        if bounds[name].end == "latest":
            if not include_holdout or name != "holdout":
                raise ValueError(f"{name} end must be a fixed date unless holdout is unlocked")
            bounds[name].end = observed_dates[-1]
    max_used_date = max(bounds[name].end for name in splits)
    if not isinstance(max_used_date, date):
        raise ValueError("Factor split end dates must resolve to concrete dates")

    prices = prices.loc[prices["_date"] <= max_used_date].copy()
    membership = membership.loc[membership["_rebalance_date"] <= max_used_date].copy()
    used_sessions = sorted(set(prices["_date"]))
    cutoff = _eligible_cutoff(
        max_used_date, used_sessions, factor.fundamentals.announcement_lag_sessions
    )
    if fundamentals is not None:
        fundamentals = (
            fundamentals.loc[fundamentals["_announced"] <= cutoff].copy()
            if cutoff is not None
            else fundamentals.iloc[0:0].copy()
        )

    pending: list[dict[str, Any]] = []
    stress_key = f"{factor.cost_stress_multiplier:g}x_cost_stress"
    max_horizon = max(factor.horizons_months)
    for split_name in splits:
        start = bounds[split_name].start
        end_value = bounds[split_name].end
        if not isinstance(end_value, date):
            raise ValueError(f"{split_name} end must resolve to a concrete date")
        end = end_value
        last_signal_date = (pd.Timestamp(end) - pd.DateOffset(months=max_horizon)).date()
        rebalance_dates = sorted(
            day
            for day in membership["_rebalance_date"].unique()
            if start <= day <= min(end, last_signal_date)
        )
        signals = compute_factor_signals(
            prices,
            membership,
            fundamentals,
            factor,
            rebalance_dates,
            hypothesis_ids=selected_ids,
        )
        returns = build_period_returns(
            signals,
            prices,
            membership,
            study,
            costs,
            factor.cost_stress_multiplier,
            factor.horizons_months,
            factor.construction.signal_to_order_lag_sessions,
        )
        if returns.empty:
            continue
        for hypothesis in selected:
            for horizon in factor.horizons_months:
                frame = returns.loc[
                    (returns["hypothesis"] == hypothesis.id) & (returns["horizon"] == horizon)
                ].copy()
                if frame.empty:
                    continue
                _, p_value, _ = date_clustered_t_statistic(frame, "excess_equal_weight")
                summary = return_summary(frame, "net_return")
                summary[stress_key] = {"expectancy": summary.get("expectancy", float("nan"))}
                pending.append(
                    {
                        "split": split_name,
                        "hypothesis": hypothesis,
                        "horizon": horizon,
                        "p_value": p_value,
                        "summary": summary,
                        "excess_summary": return_summary(frame, "excess_equal_weight"),
                        "nifty_summary": return_summary(frame, "excess_niftybees"),
                        "yearly": yearly_returns(frame, "net_return"),
                        "breadth": symbol_breadth(frame, "net_return"),
                    }
                )

    results: list[FactorStudyResult] = []
    for split_name in splits:
        indexes = [index for index, item in enumerate(pending) if item["split"] == split_name]
        adjusted_values = benjamini_hochberg([pending[index]["p_value"] for index in indexes])
        for index, adjusted_p_value in zip(indexes, adjusted_values, strict=True):
            item = pending[index]
            item_hypothesis: Hypothesis = item["hypothesis"]
            gates = evaluate_factor_gates(
                item["summary"],
                item["excess_summary"],
                item["nifty_summary"],
                adjusted_p_value,
                item["yearly"],
                item["breadth"],
                factor,
                gate_settings,
                stress_key,
            )
            result = FactorStudyResult(
                split=item["split"],
                hypothesis_id=item_hypothesis.id,
                hypothesis_name=item_hypothesis.name,
                horizon_months=item["horizon"],
                summary=item["summary"],
                excess_summary=item["excess_summary"],
                niftybees_excess=item["nifty_summary"],
                adjusted_p_value=float(adjusted_p_value),
                gates=gates,
                yearly=tuple(item["yearly"].to_dict("records")),
                candidate=all(gates.values()) and complete_design,
            )
            results.append(result)
            registry.append_trial(
                _trial_config(
                    item_hypothesis, item["horizon"], factor, selected_ids, complete_design
                ),
                result.split,
                item["horizon"],
                {
                    "summary": result.summary,
                    "excess_equal_weight": result.excess_summary,
                    "excess_niftybees": result.niftybees_excess,
                    "adjusted_p_value": result.adjusted_p_value,
                    "gates": result.gates,
                    "candidate": result.candidate,
                    "yearly": list(result.yearly),
                    "study_scope": {
                        "hypotheses": list(selected_ids),
                        "complete_frozen_design": complete_design,
                    },
                },
            )

    return FactorStudyOutput(
        results=tuple(results),
        report_path=data.report_path,
        trials_appended=len(results),
        hypotheses=selected_ids,
        complete_design=complete_design,
    )
