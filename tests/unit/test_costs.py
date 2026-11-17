"""Hand-calculated delivery-equity transaction-cost examples."""

import pytest

from nse_engine.backtest.costs import (
    CostsConfig,
    DeliveryCostRates,
    calculate_order_cost,
    round_trip_cost,
)


def _cost_config() -> CostsConfig:
    """Build charges that yield simple independently checked rupee amounts."""
    return CostsConfig(
        delivery_equity=DeliveryCostRates(
            brokerage_pct=0.1,
            brokerage_min_per_order=5,
            brokerage_max_per_order=20,
            stt_pct_buy=0.1,
            stt_pct_sell=0.1,
            exchange_txn_pct=0.00307,
            sebi_fee_pct=0.0001,
            ipft_fee_pct=0.0001,
            stamp_duty_pct_buy=0.015,
            gst_pct=18,
            dp_charge_per_sell_scrip=20,
        ),
        slippage_bps=10,
        stress_multipliers=[1.0, 1.5, 2.0],
    )


def test_hand_calculated_buy_and_sell_costs() -> None:
    """GST base and buy/sell-specific duties match hand arithmetic."""
    config = _cost_config()
    buy = calculate_order_cost("buy", 100, 10, config)
    sell = calculate_order_cost("sell", 100, 10, config)

    assert buy.total == pytest.approx(7.088586)
    assert sell.total == pytest.approx(30.538586)
    assert round_trip_cost(100, 100, 10, config) == pytest.approx(37.627172)


def test_small_order_hits_brokerage_minimum() -> None:
    """A ₹10 order pays the configured ₹5 minimum brokerage."""
    buy = calculate_order_cost("buy", 10, 1, _cost_config())

    assert buy.brokerage == pytest.approx(5)
    assert buy.total == pytest.approx(5.91188586)


def test_cost_stress_scales_all_charges() -> None:
    """Stress multipliers scale both variable and per-order configured charges."""
    config = _cost_config()

    base = calculate_order_cost("sell", 100, 10, config, 1.0)
    stressed = calculate_order_cost("sell", 100, 10, config, 2.0)

    assert stressed.total == pytest.approx(base.total * 2)
