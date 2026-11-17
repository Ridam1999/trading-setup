"""Close-of-day setup scanner that emits causal next-session order candidates."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.setups.base import Setup, eligible_rows

REQUIRED_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class SignalCandidate:
    """One setup/symbol signal observed at a session close."""

    symbol: str
    setup: str
    signal_date: date
    signal_close: float
    rank_score: float


def scan_close(
    frame: pd.DataFrame,
    setups: Sequence[Setup],
    signal_date: date,
    suspended: Collection[str] = (),
    membership: pd.Series | None = None,
) -> list[SignalCandidate]:
    """Evaluate setups only on bars available through ``signal_date``.

    A setup may fire on the final bar of the supplied history, but nothing after
    that bar is read. Candidates are ranked by that bar's traded value with
    setup and symbol as stable tie-breaks, matching the portfolio simulator.
    """
    missing = set(REQUIRED_COLUMNS).difference(frame.columns)
    if missing:
        raise ValueError(f"Scanner data is missing columns: {', '.join(sorted(missing))}")
    if frame.empty:
        raise ValueError("No OHLCV data is available for the paper scanner")

    bars = frame.copy().reset_index(drop=True)
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    history = bars.loc[bars["date"] <= signal_date].reset_index(drop=True)
    if history.empty or not history["date"].eq(signal_date).any():
        raise ValueError(f"No market data is available on or before {signal_date.isoformat()}")

    if membership is not None:
        if len(membership) != len(frame):
            raise ValueError("membership must be aligned to the supplied frame")
        aligned = pd.Series(membership.to_numpy(dtype=bool), index=bars.index)
        history = history.assign(
            _pit_member=aligned.reindex(history.index).fillna(False).to_numpy(dtype=bool)
        )
    else:
        history = history.assign(_pit_member=True)

    blocked = set(suspended)
    candidates: list[SignalCandidate] = []
    active = [setup for setup in setups if setup.config.name not in blocked]
    for setup in active:
        for symbol, symbol_frame in history.groupby("symbol", sort=True):
            ordered = symbol_frame.sort_values("date").reset_index(drop=True)
            eligible = eligible_rows(ordered, setup.config) & ordered["_pit_member"].astype(bool)
            ordered["_eligible_rows"] = eligible
            signals = setup.signals(ordered).fillna(False).astype(bool) & eligible
            positions = ordered.index[ordered["date"] == signal_date]
            if len(positions) == 0:
                continue
            index = int(positions[0])
            if not bool(signals.iloc[index]):
                continue
            row = ordered.iloc[index]
            candidates.append(
                SignalCandidate(
                    symbol=str(symbol),
                    setup=setup.config.name,
                    signal_date=signal_date,
                    signal_close=float(row["close"]),
                    rank_score=float(row["close"]) * float(row["volume"]),
                )
            )
    return sorted(candidates, key=lambda item: (-item.rank_score, item.setup, item.symbol))
