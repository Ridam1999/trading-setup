"""Pure delivery-equity transaction-cost functions driven by YAML configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nse_engine.config import config_directory


class DeliveryCostRates(BaseModel):
    """Validated percentage and flat charge rates from the delivery cost YAML."""

    model_config = ConfigDict(extra="forbid")

    brokerage_pct: float = Field(ge=0)
    brokerage_flat_per_order: float = Field(ge=0)
    stt_pct_buy: float = Field(ge=0)
    stt_pct_sell: float = Field(ge=0)
    exchange_txn_pct: float = Field(ge=0)
    sebi_fee_pct: float = Field(ge=0)
    stamp_duty_pct_buy: float = Field(ge=0)
    gst_pct: float = Field(ge=0)
    dp_charge_per_sell_scrip: float = Field(ge=0)


class CostsConfig(BaseModel):
    """Top-level costs and slippage settings."""

    model_config = ConfigDict(extra="forbid")

    delivery_equity: DeliveryCostRates
    slippage_bps: float = Field(ge=0)
    stress_multipliers: list[float] = Field(min_length=1)

    @field_validator("stress_multipliers")
    @classmethod
    def validate_stress_multipliers(cls, values: list[float]) -> list[float]:
        """Require positive stresses and a baseline multiplier."""
        if any(value <= 0 for value in values) or 1.0 not in values:
            raise ValueError("Cost stress multipliers must be positive and include 1.0")
        return values


@dataclass(frozen=True)
class CostBreakdown:
    """Per-order rupee charges for one side of a delivery equity trade."""

    brokerage: float
    stt: float
    exchange_transaction: float
    sebi_fee: float
    stamp_duty: float
    gst: float
    dp_charge: float

    @property
    def total(self) -> float:
        """Return the total rupee charge for this order."""
        return sum(
            (
                self.brokerage,
                self.stt,
                self.exchange_transaction,
                self.sebi_fee,
                self.stamp_duty,
                self.gst,
                self.dp_charge,
            )
        )


def load_costs_config(path: Path | None = None) -> CostsConfig:
    """Load and validate costs.yaml from the configured project directory."""
    config_path = (path or config_directory()) / "costs.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        document = yaml.safe_load(config_file)
    return CostsConfig.model_validate(document)


def calculate_order_cost(
    side: Literal["buy", "sell"],
    price: float,
    quantity: float,
    config: CostsConfig,
    multiplier: float = 1.0,
) -> CostBreakdown:
    """Calculate configured charges for one buy or sell order."""
    if price <= 0 or quantity <= 0 or multiplier <= 0:
        raise ValueError("Price, quantity, and cost multiplier must be positive")
    rates = config.delivery_equity
    turnover = price * quantity
    brokerage = turnover * rates.brokerage_pct / 100 + rates.brokerage_flat_per_order
    stt_rate = rates.stt_pct_buy if side == "buy" else rates.stt_pct_sell
    stt = turnover * stt_rate / 100
    exchange = turnover * rates.exchange_txn_pct / 100
    sebi = turnover * rates.sebi_fee_pct / 100
    stamp = turnover * rates.stamp_duty_pct_buy / 100 if side == "buy" else 0.0
    gst = (brokerage + exchange + sebi) * rates.gst_pct / 100
    dp = rates.dp_charge_per_sell_scrip if side == "sell" else 0.0
    return CostBreakdown(
        brokerage * multiplier,
        stt * multiplier,
        exchange * multiplier,
        sebi * multiplier,
        stamp * multiplier,
        gst * multiplier,
        dp * multiplier,
    )


def round_trip_cost(
    entry_price: float,
    exit_price: float,
    quantity: float,
    config: CostsConfig,
    multiplier: float = 1.0,
) -> float:
    """Return total buy and sell charges for a completed delivery trade."""
    buy = calculate_order_cost("buy", entry_price, quantity, config, multiplier)
    sell = calculate_order_cost("sell", exit_price, quantity, config, multiplier)
    return buy.total + sell.total
