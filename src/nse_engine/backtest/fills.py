"""Shared daily limit-order fill rules used by event studies and portfolios."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]


def buy_limit_fill(
    signal_close: float,
    entry_bar: pd.Series,
    limit_buffer_bps: float,
    slippage_bps: float,
) -> float | None:
    """Return a slipped next-session buy fill, or None when the limit is missed."""
    circuit_locked = entry_bar.get("circuit_locked", False)
    if pd.notna(circuit_locked) and bool(circuit_locked):
        return None
    limit_price = signal_close * (1 + limit_buffer_bps / 10000)
    open_price = float(entry_bar["open"])
    low_price = float(entry_bar["low"])
    if open_price <= limit_price:
        base_price = open_price
    elif low_price <= limit_price:
        base_price = limit_price
    else:
        return None
    return base_price * (1 + slippage_bps / 10000)


def sell_exit_fill(
    bar: pd.Series,
    stop_price: float | None,
    target_price: float | None,
    slippage_bps: float,
) -> tuple[float | None, str | None]:
    """Apply stop-first daily exit ambiguity and gap-through-stop handling."""
    circuit_locked = bar.get("circuit_locked", False)
    if pd.notna(circuit_locked) and bool(circuit_locked):
        return None, None
    open_price = float(bar["open"])
    low_price = float(bar["low"])
    high_price = float(bar["high"])
    stop_hit = stop_price is not None and low_price <= stop_price
    target_hit = target_price is not None and high_price >= target_price
    if stop_hit:
        assert stop_price is not None
        base_price = open_price if open_price <= stop_price else stop_price
        return base_price * (1 - slippage_bps / 10000), "stop"
    if target_hit:
        assert target_price is not None
        return target_price * (1 - slippage_bps / 10000), "target"
    return None, None


def time_exit_fill(bar: pd.Series, slippage_bps: float) -> float | None:
    """Return a slipped close fill, or None when a circuit lock prevents execution."""
    circuit_locked = bar.get("circuit_locked", False)
    if pd.notna(circuit_locked) and bool(circuit_locked):
        return None
    return float(bar["close"]) * (1 - slippage_bps / 10000)
