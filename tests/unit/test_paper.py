"""Paper-trading scanner, fill, ledger, report, and decay tests."""

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from nse_engine.backtest.config import BacktestSettings
from nse_engine.backtest.costs import CostsConfig, DeliveryCostRates
from nse_engine.paper.config import (
    DecaySettings,
    PaperSettings,
    load_approved_setups,
    load_paper_settings,
)
from nse_engine.paper.decay import evaluate_decay, validated_band
from nse_engine.paper.ledger import PaperLedger
from nse_engine.paper.report import DISCLAIMER
from nse_engine.paper.runner import run_paper_day
from nse_engine.paper.scanner import scan_close
from nse_engine.setups.base import SetupConfig


class TriggerSetup:
    """Setup that fires on an explicit causal trigger column."""

    def __init__(self, config: SetupConfig) -> None:
        self.config = config

    def required_features(self) -> list[str]:
        """Return the columns this rule consumes."""
        return ["symbol", "date", "open", "high", "low", "close", "volume", "trigger"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Return the trigger column as a boolean series."""
        return frame["trigger"].fillna(False).astype(bool)


def _setup_config() -> SetupConfig:
    """Return a synthetic setup with a 10 percent stop and 20 percent target."""
    return SetupConfig(
        name="trigger",
        universe="synthetic",
        rationale="Fixed trigger used to verify paper-trading mechanics.",
        params={"traded_value_window": 1},
        filters={"minimum_price": 1, "minimum_average_traded_value": 0},
        exit_rules={"stop_loss_pct": 10, "target_pct": 20},
    )


def _settings() -> BacktestSettings:
    """Return small, deterministic portfolio limits."""
    return BacktestSettings(
        initial_capital=10000,
        risk_per_trade_fraction=0.1,
        atr_lookback=2,
        stop_atr_multiple=2,
        default_stop_loss_pct=10,
        default_target_pct=20,
        maximum_holding_days=10,
        maximum_positions=2,
        maximum_symbol_weight=1,
        maximum_sector_weight=1,
        entry_limit_buffer_bps=10,
        annual_risk_free_rate=0,
        benchmark_symbol="BENCH",
        monte_carlo_replicates=20,
        walk_forward_train_years=2,
        walk_forward_test_years=1,
        walk_forward_step_years=1,
        reports_dir=Path("reports"),
    )


def _costs() -> CostsConfig:
    """Return zero-charge settings so fill arithmetic is exact."""
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
        slippage_bps=0,
        stress_multipliers=[1.0],
    )


def _paper(tmp_path: Path) -> PaperSettings:
    """Return paper paths inside a temporary directory."""
    return PaperSettings(
        reports_dir=tmp_path / "reports",
        ledger_path=tmp_path / "paper.sqlite",
        decay=DecaySettings(
            minimum_trades=2,
            drawdown_tolerance=1.5,
            hit_rate_tolerance=0.7,
            consecutive_breaches=2,
        ),
    )


def _market() -> pd.DataFrame:
    """Create four sessions with one signal and a following-day entry bar."""
    rows: list[dict[str, Any]] = [
        {
            "date": date(2024, 1, 2),
            "open": 99,
            "high": 101,
            "low": 99,
            "close": 100,
            "trigger": False,
        },
        {
            "date": date(2024, 1, 3),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "trigger": True,
        },
        {
            "date": date(2024, 1, 4),
            "open": 100,
            "high": 102,
            "low": 99.5,
            "close": 101,
            "trigger": False,
        },
        {
            "date": date(2024, 1, 5),
            "open": 101,
            "high": 120,
            "low": 100,
            "close": 119,
            "trigger": False,
        },
    ]
    return pd.DataFrame([{"symbol": "EDGE", **row, "volume": 10000} for row in rows])


def _approval(returns: tuple[float, ...] = (0.02, -0.01)) -> dict[str, Any]:
    """Return one approval entry with a validated per-trade metric band."""
    hits = sum(value > 0 for value in returns) / len(returns)
    return {
        "setup": "trigger",
        "horizon": 1,
        "parameters": _setup_config().model_dump(mode="json"),
        "validated_metrics": {
            "validation": {
                "net_return": {"hit_rate": hits, "worst_loss": min(returns)},
            }
        },
    }


def _write_config_files(directory: Path) -> None:
    """Write minimal paper and approval YAML files for loader tests."""
    (directory / "paper.yaml").write_text(
        "reports_dir: reports/paper\n"
        "ledger_path: data/duckdb/paper.sqlite\n"
        "decay:\n"
        "  minimum_trades: 5\n"
        "  drawdown_tolerance: 1.5\n"
        "  hit_rate_tolerance: 0.7\n"
        "  consecutive_breaches: 2\n",
        encoding="utf-8",
    )
    (directory / "approved_setups.yaml").write_text("approved_setups: []\n", encoding="utf-8")


def test_paper_yaml_loaders_accept_an_empty_approval_file(tmp_path: Path) -> None:
    """Paper settings validate and an empty approval file is a valid no-trade state."""
    _write_config_files(tmp_path)
    paper = load_paper_settings(tmp_path)
    assert paper.decay.minimum_trades == 5
    assert load_approved_setups(tmp_path) == []


def test_scanner_ignores_bars_after_the_signal_date() -> None:
    """Changing later bars never changes a signal evaluated at an earlier close."""
    market = _market()
    setup = TriggerSetup(_setup_config())
    signal_date = date(2024, 1, 3)
    original = scan_close(market, [setup], signal_date)
    altered = market.copy()
    later = altered["date"] > signal_date
    altered.loc[later, ["open", "high", "low", "close"]] *= 3
    altered.loc[later, "trigger"] = True
    assert original == scan_close(altered, [setup], signal_date)
    assert [candidate.symbol for candidate in original] == ["EDGE"]


def test_runner_fills_orders_only_from_the_next_session(tmp_path: Path) -> None:
    """An order created at Tuesday's close fills from Wednesday's bar, not later bars."""
    market = _market()
    paper = _paper(tmp_path)
    approved = [_approval()]
    setups = [TriggerSetup(_setup_config())]
    first_ledger = PaperLedger(paper.ledger_path)
    run_paper_day(
        date(2024, 1, 3),
        market,
        approved,
        _settings(),
        _costs(),
        paper,
        first_ledger,
        "SURVIVOR_BIASED",
        setups=setups,
    )
    run_paper_day(
        date(2024, 1, 4),
        market,
        approved,
        _settings(),
        _costs(),
        paper,
        first_ledger,
        "SURVIVOR_BIASED",
        setups=setups,
    )
    fills = first_ledger.report_rows()["fills"]
    assert len(fills) == 1
    assert fills[0]["fill_date"] == "2024-01-04"
    assert fills[0]["fill_price"] == pytest.approx(100.0)

    future = market.copy()
    future.loc[future["date"] > date(2024, 1, 4), ["open", "high", "low", "close"]] *= 5
    other_path = tmp_path / "future" / "paper.sqlite"
    other_paper = PaperSettings(
        reports_dir=tmp_path / "future_reports",
        ledger_path=other_path,
        decay=paper.decay,
    )
    other_ledger = PaperLedger(other_path)
    run_paper_day(
        date(2024, 1, 3),
        future,
        approved,
        _settings(),
        _costs(),
        other_paper,
        other_ledger,
        "SURVIVOR_BIASED",
        setups=setups,
    )
    run_paper_day(
        date(2024, 1, 4),
        future,
        approved,
        _settings(),
        _costs(),
        other_paper,
        other_ledger,
        "SURVIVOR_BIASED",
        setups=setups,
    )
    assert other_ledger.report_rows()["fills"] == fills


def test_delayed_run_still_fills_from_the_first_session_after_the_order(tmp_path: Path) -> None:
    """A skipped session does not move a pending order's fill to the run date."""
    paper = _paper(tmp_path)
    ledger = PaperLedger(paper.ledger_path)
    approved = [_approval()]
    setups = [TriggerSetup(_setup_config())]
    arguments = (
        approved,
        _settings(),
        _costs(),
        paper,
        ledger,
        "SURVIVOR_BIASED",
    )
    run_paper_day(date(2024, 1, 3), _market(), *arguments, setups=setups)
    run_paper_day(date(2024, 1, 5), _market(), *arguments, setups=setups)
    fills = ledger.report_rows()["fills"]
    assert len(fills) == 1
    assert fills[0]["fill_date"] == "2024-01-04"
    assert fills[0]["fill_price"] == pytest.approx(100.0)


def test_no_signal_day_writes_a_valid_report(tmp_path: Path) -> None:
    """An empty approval set still produces a report with disclaimer and bias status."""
    paper = _paper(tmp_path)
    ledger = PaperLedger(paper.ledger_path)
    summary = run_paper_day(
        date(2024, 1, 4),
        _market(),
        [],
        _settings(),
        _costs(),
        paper,
        ledger,
        "SURVIVOR_BIASED",
    )
    assert summary.orders_count == 0
    report = (paper.reports_dir / "paper_2024-01-04.md").read_text(encoding="utf-8")
    assert "No approved setups are configured" in report
    assert DISCLAIMER in report
    assert "SURVIVOR_BIASED" in report
    assert (paper.reports_dir / "orders_2024-01-04.json").read_text(encoding="utf-8") == "[]\n"


def test_same_date_run_is_idempotent(tmp_path: Path) -> None:
    """Repeating a completed date changes no ledger row and no report byte."""
    paper = _paper(tmp_path)
    ledger = PaperLedger(paper.ledger_path)
    market = _market()
    approved = [_approval()]
    setups = [TriggerSetup(_setup_config())]
    arguments = (
        date(2024, 1, 3),
        market,
        approved,
        _settings(),
        _costs(),
        paper,
        ledger,
        "SURVIVOR_BIASED",
    )
    first = run_paper_day(*arguments, setups=setups)
    first_rows = ledger.report_rows()
    report_path = paper.reports_dir / "paper_2024-01-03.md"
    first_report = report_path.read_text(encoding="utf-8")
    second = run_paper_day(*arguments, setups=setups)
    assert first == second
    assert ledger.report_rows() == first_rows
    assert report_path.read_text(encoding="utf-8") == first_report


def test_decay_suspends_after_consecutive_breaches() -> None:
    """Repeated out-of-band results mark a setup suspended after the configured count."""
    band = validated_band(_approval())
    decay = DecaySettings(
        minimum_trades=2,
        drawdown_tolerance=1.5,
        hit_rate_tolerance=0.7,
        consecutive_breaches=2,
    )
    returns = [-0.25, -0.30]
    first = evaluate_decay("trigger", returns, band, decay)
    assert first.status == "active"
    assert first.breach_count == 1
    second = evaluate_decay(
        "trigger",
        returns,
        band,
        decay,
        previous_status=first.status,
        previous_breach_count=first.breach_count,
    )
    assert second.status == "suspended"
    assert second.breach_count == 2
    assert "hit_rate" in second.reason

    setup = TriggerSetup(_setup_config())
    market = _market()
    assert scan_close(market, [setup], date(2024, 1, 3), suspended=["trigger"]) == []


def test_membership_stays_aligned_to_original_rows() -> None:
    """Point-in-time membership follows its bar even when the frame is unsorted."""
    setup = TriggerSetup(_setup_config())
    unsorted = _market().iloc[[2, 0, 3, 1]].reset_index(drop=True)
    # The signal bar is the last row of this deliberately unsorted frame.
    membership = pd.Series([False, False, False, True])
    candidates = scan_close(unsorted, [setup], date(2024, 1, 3), membership=membership)
    assert [candidate.symbol for candidate in candidates] == ["EDGE"]
