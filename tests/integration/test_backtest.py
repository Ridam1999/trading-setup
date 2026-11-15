"""Hand-checked portfolio and deterministic walk-forward integration tests."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nse_engine.backtest.config import BacktestSettings
from nse_engine.backtest.costs import CostsConfig, DeliveryCostRates
from nse_engine.backtest.engine import run_portfolio_backtest
from nse_engine.backtest.metrics import portfolio_metrics
from nse_engine.backtest.portfolio import monte_carlo_drawdowns
from nse_engine.backtest.report import DISCLAIMER, write_backtest_report
from nse_engine.backtest.walkforward import walk_forward_backtest
from nse_engine.setups.base import SetupConfig


def _costs() -> CostsConfig:
    """Provide zero-cost settings so the fixture reconciliation is exact."""
    return CostsConfig(
        delivery_equity=DeliveryCostRates(
            brokerage_pct=0,
            brokerage_flat_per_order=0,
            stt_pct_buy=0,
            stt_pct_sell=0,
            exchange_txn_pct=0,
            sebi_fee_pct=0,
            stamp_duty_pct_buy=0,
            gst_pct=0,
            dp_charge_per_sell_scrip=0,
        ),
        slippage_bps=0,
        stress_multipliers=[1.0, 1.5, 2.0],
    )


def _settings(capital: float = 10000) -> BacktestSettings:
    """Return deterministic limits sized for small synthetic bars."""
    return BacktestSettings(
        initial_capital=capital,
        risk_per_trade_fraction=0.1,
        atr_lookback=14,
        stop_atr_multiple=2,
        default_stop_loss_pct=10,
        default_target_pct=20,
        maximum_holding_days=10,
        maximum_positions=1,
        maximum_symbol_weight=1,
        maximum_sector_weight=1,
        entry_limit_buffer_bps=0,
        annual_risk_free_rate=0,
        benchmark_symbol="BENCH",
        monte_carlo_replicates=50,
        walk_forward_train_years=2,
        walk_forward_test_years=1,
        walk_forward_step_years=1,
        reports_dir=Path("reports"),
    )


class TriggerSetup:
    """Setup with explicit causal trigger marks from a synthetic fixture."""

    def __init__(self, config: SetupConfig) -> None:
        self.config = config

    def required_features(self) -> list[str]:
        return ["symbol", "date", "open", "high", "low", "close", "volume", "trigger"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        return frame["trigger"].fillna(False).astype(bool)


def _config() -> SetupConfig:
    """Build a configured synthetic setup with a 10 percent stop and 20 percent target."""
    return SetupConfig(
        name="trigger",
        universe="synthetic",
        rationale="A fixed signal used to reconcile portfolio accounting.",
        params={"traded_value_window": 1},
        filters={"minimum_price": 1, "minimum_average_traded_value": 0},
        exit_rules={"stop_loss_pct": 10, "target_pct": 20},
    )


def _mini_market() -> pd.DataFrame:
    """Create one risk-sized trade that exits at its configured target."""
    days = [date(2024, 1, day) for day in range(2, 6)]
    prices = [
        (10.0, 10.0, 10.0, 10.0, False),
        (10.0, 10.0, 10.0, 10.0, True),
        (10.0, 11.0, 9.5, 11.0, False),
        (11.0, 12.1, 10.8, 12.0, False),
    ]
    rows = []
    for symbol in ("EDGE", "BENCH"):
        for day, (open_price, high, low, close, trigger) in zip(days, prices, strict=True):
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 10000,
                    "trigger": trigger if symbol == "EDGE" else False,
                }
            )
    return pd.DataFrame(rows)


def test_mini_backtest_reconciles_fill_sizing_costs_and_target() -> None:
    """One signal creates 1,000 shares and a target exit with exactly 2,000 rupees profit."""
    result = run_portfolio_backtest(
        _mini_market(),
        [TriggerSetup(_config())],
        _settings(),
        _costs(),
        date(2024, 1, 2),
        date(2024, 1, 5),
    )

    assert result["orders"].iloc[0]["signal_date"] == date(2024, 1, 3)
    assert result["orders"].iloc[0]["entry_date"] == date(2024, 1, 4)
    assert result["orders"].iloc[0]["quantity"] == 1000
    trade = result["trades"].iloc[0]
    assert trade["exit_date"] == date(2024, 1, 5)
    assert trade["exit_reason"] == "target"
    assert trade["net_pnl"] == pytest.approx(2000)
    assert result["equity"].iloc[-1]["equity"] == pytest.approx(12000)
    metrics = portfolio_metrics(result["equity"], result["trades"], 0)
    assert metrics["n_trades"] == 1
    assert metrics["total_return"] == pytest.approx(0.2)
    assert metrics["total_costs"] == 0


def _annual_market() -> pd.DataFrame:
    """Create six years of repeatable daily data for rolling window tests."""
    dates = pd.bdate_range("2010-01-01", "2016-12-31")
    rows: list[dict[str, object]] = []
    for symbol in ("EDGE", "BENCH"):
        for position, timestamp in enumerate(dates):
            close = 100 + position * 0.01
            rows.append(
                {
                    "symbol": symbol,
                    "date": timestamp.date(),
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 10000,
                    "trigger": symbol == "EDGE" and position % 20 == 1,
                }
            )
    return pd.DataFrame(rows)


def test_walk_forward_is_deterministic_and_uses_train_only_selection() -> None:
    """Rolling train/test windows stitch identical equity and record selected rules."""
    market = _annual_market()
    arguments = (
        market,
        [TriggerSetup(_config())],
        _settings(),
        _costs(),
        date(2010, 1, 1),
        date(2016, 12, 31),
        2,
        1,
        1,
    )
    first = walk_forward_backtest(*arguments)
    second = walk_forward_backtest(*arguments)

    pd.testing.assert_frame_equal(first["equity"], second["equity"])
    assert len(first["windows"]) == 5
    assert first["windows"].iloc[0]["train_end"] < first["windows"].iloc[0]["test_start"]
    assert first["windows"].iloc[0]["selected_setups"] == ["trigger"]
    assert first["equity"]["date"].is_unique


def test_monte_carlo_is_seeded() -> None:
    """Trade-sequence resampling produces reproducible drawdown percentiles."""
    trades = pd.DataFrame({"net_return": [0.05, -0.03, 0.02, -0.01]})
    assert monte_carlo_drawdowns(trades, 100, 91) == monte_carlo_drawdowns(trades, 100, 91)
    assert np.isfinite(monte_carlo_drawdowns(trades, 100, 91)["drawdown_median"])


def test_plotly_html_report_includes_disclaimer_and_universe_bias(tmp_path: Path) -> None:
    """The deliverable HTML report contains the required warning and embedded charts."""
    simulation = run_portfolio_backtest(
        _mini_market(),
        [TriggerSetup(_config())],
        _settings(),
        _costs(),
        date(2024, 1, 2),
        date(2024, 1, 5),
    )
    metrics = portfolio_metrics(simulation["equity"], simulation["trades"], 0)
    path = write_backtest_report(
        simulation["equity"],
        simulation["trades"],
        metrics,
        {"1x": metrics},
        monte_carlo_drawdowns(simulation["trades"], 20, 7),
        tmp_path / "report.html",
        True,
    )
    report = path.read_text(encoding="utf-8")
    assert DISCLAIMER in report
    assert "survivorship bias likely" in report
    assert "Plotly" in report
