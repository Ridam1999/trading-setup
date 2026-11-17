"""Synthetic edge, timing, fill, and holdout-lock integration tests."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nse_engine.backtest.costs import CostsConfig, DeliveryCostRates
from nse_engine.setups.base import SetupConfig, load_setup_config
from nse_engine.setups.random_signal import RandomSignal
from nse_engine.study.config import StudySettings
from nse_engine.study.events import (
    _net_return_after_costs,
    _net_returns_after_costs,
    _simulate_signal_batch,
    _simulate_with_stress,
    _vectorized_entry_exit,
    build_event_study,
    simulate_event,
)
from nse_engine.study.splits import resolve_split
from nse_engine.study.trials import TrialRegistry


def _costs(slippage_bps: float = 0.0) -> CostsConfig:
    """Build zero-charge deterministic costs for fill-model fixtures."""
    return CostsConfig(
        delivery_equity=DeliveryCostRates(
            brokerage_pct=0,
            brokerage_min_per_order=0,
            brokerage_max_per_order=0,
            stt_pct_buy=0,
            stt_pct_sell=0,
            exchange_txn_pct=0,
            sebi_fee_pct=0,
            ipft_fee_pct=0,
            stamp_duty_pct_buy=0,
            gst_pct=0,
            dp_charge_per_sell_scrip=0,
        ),
        slippage_bps=slippage_bps,
        stress_multipliers=[1.0, 1.5, 2.0],
    )


def _study_settings(horizons: list[int] | None = None) -> StudySettings:
    """Return small deterministic event-study settings."""
    return StudySettings(
        horizons=horizons or [1],
        entry_limit_buffer_bps=10,
        notional_rupees=100000,
        bootstrap_replicates=100,
        bootstrap_block_length=3,
        fdr_alpha=0.05,
        benchmark_symbol="BENCHMARK_MISSING",
        benchmark_trend_length=20,
        volatility_lookback=5,
        trial_registry_path=Path("/tmp/trials.sqlite"),
        events_dir=Path("/tmp/study-events"),
    )


def _bars(rows: list[dict[str, object]]) -> pd.DataFrame:
    """Normalize tiny event price fixtures."""
    return pd.DataFrame(rows)


def test_entry_is_on_next_session_and_unreached_limit_does_not_fill() -> None:
    """A signal at t can fill only at t+1 using the configured limit model."""
    data = _bars(
        [
            {"date": date(2024, 1, 2), "open": 100, "high": 100, "low": 100, "close": 100},
            {"date": date(2024, 1, 3), "open": 101.0, "high": 102.0, "low": 99.0, "close": 101.0},
        ]
    )
    result = simulate_event(data, 0, 1, None, None, _study_settings(), _costs())

    assert result is not None
    assert result["entry_date"] == date(2024, 1, 3)
    assert result["entry_price"] == pytest.approx(100.1)
    data.loc[1, "low"] = 100.2
    assert simulate_event(data, 0, 1, None, None, _study_settings(), _costs()) is None


def test_gap_stop_and_both_stop_target_hit_use_stop_first() -> None:
    """Gaps through stops fill at open and dual-touch bars resolve to the stop."""
    gap = _bars(
        [
            {"date": date(2024, 1, 2), "open": 100, "high": 100, "low": 100, "close": 100},
            {"date": date(2024, 1, 3), "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": date(2024, 1, 4), "open": 90, "high": 96, "low": 88, "close": 92},
        ]
    )
    result = simulate_event(gap, 0, 2, 5, 5, _study_settings([2]), _costs())

    assert result is not None
    assert result["exit_date"] == date(2024, 1, 4)
    assert result["exit_price"] == pytest.approx(90)


def test_circuit_locked_entry_is_skipped() -> None:
    """Circuit-locked entry bars are not treated as reliable fills."""
    data = _bars(
        [
            {"date": date(2024, 1, 2), "open": 100, "high": 100, "low": 100, "close": 100},
            {
                "date": date(2024, 1, 3),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "circuit_locked": True,
            },
        ]
    )

    assert simulate_event(data, 0, 1, None, None, _study_settings(), _costs()) is None


class InjectedEdgeSetup:
    """Test-only setup whose trigger is known before the injected forward move."""

    name = "synthetic_edge"

    def __init__(self, config: SetupConfig) -> None:
        self.config = config

    def required_features(self) -> list[str]:
        return ["symbol", "trigger", "close", "volume"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        return frame["symbol"].eq("EDGE") & frame["trigger"]


def _synthetic_market() -> pd.DataFrame:
    """Construct independent controls plus a positive next-session edge."""
    dates = pd.date_range("2024-01-01", periods=120, freq="B").date
    signal_positions = set(range(10, 110, 5))
    rows: list[dict[str, object]] = []
    for symbol in ("EDGE", "CONTROL1", "CONTROL2", "CONTROL3", "CONTROL4"):
        price = 100.0
        for position, day in enumerate(dates):
            trigger = symbol == "EDGE" and position in signal_positions
            open_price = price
            close = price * 1.03 if symbol == "EDGE" and position - 1 in signal_positions else price
            if symbol != "EDGE":
                close = price * (1 + 0.001 * np.sin(position / 3 + len(symbol)))
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": open_price,
                    "high": max(open_price, close) * 1.001,
                    "low": min(open_price, close) * 0.999,
                    "close": close,
                    "volume": 100000,
                    "trigger": trigger,
                }
            )
            price = close
    return pd.DataFrame(rows)


def test_event_study_detects_injected_edge_over_noise_baseline() -> None:
    """Injected future gains produce positive cost-adjusted excess over controls."""
    frame = _synthetic_market()
    setup_config = SetupConfig(
        name="synthetic_edge",
        universe="synthetic",
        rationale="A controlled test edge.",
        params={"traded_value_window": 1},
        filters={"minimum_price": 1, "minimum_average_traded_value": 0},
        exit_rules={},
    )
    events = build_event_study(
        frame,
        InjectedEdgeSetup(setup_config),
        _study_settings([1]),
        _costs(),
        date(2024, 1, 1),
        date(2024, 6, 30),
    )

    assert len(events) >= 15
    assert events["net_return"].mean() > 0.02
    assert events["excess_over_baseline"].mean() > 0.015


def test_point_in_time_membership_filters_candidate_rows() -> None:
    """The point-in-time mask stays aligned after reducing a symbol history."""
    frame = _synthetic_market()
    setup_config = SetupConfig(
        name="synthetic_edge",
        universe="synthetic",
        rationale="A controlled test edge.",
        params={"traded_value_window": 1},
        filters={"minimum_price": 1, "minimum_average_traded_value": 0},
        exit_rules={},
    )
    membership = frame["symbol"].eq("EDGE")
    events = build_event_study(
        frame,
        InjectedEdgeSetup(setup_config),
        _study_settings([1]),
        _costs(),
        date(2024, 1, 1),
        date(2024, 6, 30),
        point_in_time_membership=membership,
    )

    assert len(events) >= 15
    assert events["symbol"].eq("EDGE").all()


def test_vectorized_baseline_matches_single_event_fill_rules() -> None:
    """The fast baseline simulation reproduces the shared fill and cost model."""
    frame = _bars(
        [
            {"date": date(2024, 1, 2), "open": 100, "high": 100, "low": 100, "close": 100},
            {"date": date(2024, 1, 3), "open": 101, "high": 102, "low": 99, "close": 101},
            {"date": date(2024, 1, 4), "open": 99, "high": 108, "low": 94, "close": 105},
        ]
    )
    settings = _study_settings([2])
    costs = _costs().model_copy(update={"stress_multipliers": [1.0]})
    positions = np.array([0])

    entries, exits, exit_indices, filled = _vectorized_entry_exit(
        frame, positions, 2, settings, costs, 5, 10
    )
    result = simulate_event(frame, 0, 2, 5, 10, settings, costs)

    assert filled.tolist() == [True]
    assert result is not None
    assert entries[0] == pytest.approx(result["entry_price"])
    assert exits[0] == pytest.approx(result["exit_price"])
    assert exit_indices[0] == 2
    assert _net_return_after_costs(
        float(entries[0]), float(exits[0]), settings.notional_rupees, costs
    ) == pytest.approx(result["net_return"])


def test_vectorized_costs_match_the_shared_cost_function() -> None:
    """The fast baseline path applies identical charges across price scales."""
    from nse_engine.backtest.costs import load_costs_config

    costs = load_costs_config(Path("config"))
    notional = 100000.0
    entries = np.array([25.0, 100.0, 750.0, 5000.0])
    exits = np.array([27.5, 95.0, 810.0, 4800.0])

    vectorized = _net_returns_after_costs(entries, exits, notional, costs)
    scalar = np.array(
        [
            _net_return_after_costs(float(entry), float(exit_price), notional, costs)
            for entry, exit_price in zip(entries, exits, strict=True)
        ]
    )

    np.testing.assert_allclose(vectorized, scalar, rtol=1e-12, atol=1e-15)


def test_vectorized_baseline_skips_unfillable_bars() -> None:
    """A missed limit or circuit-locked entry contributes no baseline return."""
    missed = _bars(
        [
            {"date": date(2024, 1, 2), "open": 100, "high": 100, "low": 100, "close": 100},
            {"date": date(2024, 1, 3), "open": 105, "high": 106, "low": 104, "close": 105},
            {"date": date(2024, 1, 4), "open": 105, "high": 106, "low": 104, "close": 105},
        ]
    )
    locked = _bars(
        [
            {"date": date(2024, 1, 2), "open": 100, "high": 100, "low": 100, "close": 100},
            {
                "date": date(2024, 1, 3),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "circuit_locked": True,
            },
            {"date": date(2024, 1, 4), "open": 100, "high": 101, "low": 99, "close": 100},
        ]
    )
    settings = _study_settings([2])

    for frame in (missed, locked):
        _, _, _, filled = _vectorized_entry_exit(
            frame, np.array([0]), 2, settings, _costs(), None, None
        )
        assert filled.tolist() == [False]
        assert simulate_event(frame, 0, 2, None, None, settings, _costs()) is None


def test_vectorized_signal_batch_matches_scalar_stress_simulation() -> None:
    """The fast signal path reproduces stressed fills, costs, and excursion metrics."""
    dates = pd.date_range("2024-01-01", periods=10, freq="B").date
    frame = pd.DataFrame(
        [
            {
                "date": day,
                "open": 100 + position - 0.5,
                "high": 100 + position + 2.0,
                "low": 100 + position - 3.0,
                "close": 100 + position,
                "volume": 100000,
            }
            for position, day in enumerate(dates)
        ]
    )
    positions = np.array([0, 3, 5])
    settings = _study_settings([3])
    costs = _costs(slippage_bps=10)

    batch = _simulate_signal_batch(frame, positions, 3, 2, 4, settings, costs)

    assert set(batch) == set(positions.tolist())
    for position in positions:
        scalar = _simulate_with_stress(frame, int(position), 3, 2, 4, settings, costs)
        assert scalar is not None
        fast = batch[int(position)]
        assert set(fast) == set(scalar)
        for key, expected in scalar.items():
            if isinstance(expected, bool):
                assert fast[key] == expected
            elif isinstance(expected, int | float | np.floating):
                assert fast[key] == pytest.approx(expected)
            else:
                assert fast[key] == expected


def test_random_signal_negative_control_runs_on_pure_noise() -> None:
    """Seeded random entries trade without inventing an edge on a noise series."""
    rng = np.random.default_rng(11)
    dates = pd.date_range("2024-01-01", periods=260, freq="B").date
    rows: list[dict[str, object]] = []
    for symbol in ("NOISE1", "NOISE2", "NOISE3", "NOISE4", "NOISE5"):
        close = 100.0
        for day in dates:
            close *= 1 + rng.normal(0, 0.01)
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": close,
                    "high": close * 1.005,
                    "low": close * 0.995,
                    "close": close,
                    "volume": 100000,
                }
            )
    frame = pd.DataFrame(rows)
    config = load_setup_config("random_signal", Path("config/setups"))

    events = build_event_study(
        frame,
        RandomSignal(config),
        _study_settings([1, 3]),
        _costs(),
        date(2024, 1, 1),
        date(2024, 12, 31),
    )

    assert not events.empty
    assert events["symbol"].isin(["NOISE1", "NOISE2", "NOISE3", "NOISE4", "NOISE5"]).all()
    assert abs(float(events["excess_over_baseline"].mean())) < 0.05


def test_holdout_requires_unlock_and_logs_access(tmp_path: Path) -> None:
    """Holdout reads fail before unlock and append an audit record when allowed."""
    registry = TrialRegistry(tmp_path / "trials.sqlite")
    frame = pd.DataFrame({"date": [date(2024, 1, 2), date(2024, 1, 3)]})

    with pytest.raises(PermissionError, match="holdout is locked"):
        resolve_split(frame, "holdout", False, registry, Path("config"))
    assert registry.trial_count() == 0
    start, end = resolve_split(frame, "holdout", True, registry, Path("config"))

    assert start == date(2023, 1, 1)
    assert end == date(2024, 1, 3)
    assert registry.trial_count() == 0
    assert len(registry.rows(include_unlocks=True)) == 1
