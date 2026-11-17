"""Config-driven risk guards applied before any simulated order is accepted."""

from __future__ import annotations

from dataclasses import dataclass, field

from nse_engine.broker.base import LiveTradingDisabled, Side
from nse_engine.broker.config import BrokerConfig


@dataclass
class RiskGuards:
    """Enforce the configured kill switch, loss cap, size cap, and position cap."""

    config: BrokerConfig
    realised_loss: float = 0.0
    seen_refs: set[str] = field(default_factory=set)
    open_positions: int = 0

    def check_order(self, symbol: str, side: Side, qty: int, price: float, client_ref: str) -> None:
        """Raise if an order breaches any configured guard."""
        if self.config.global_kill_switch:
            raise LiveTradingDisabled("Global kill switch is enabled")
        if not client_ref:
            raise ValueError("client_ref must be non-empty for idempotency")
        if qty < 1:
            raise ValueError("Order quantity must be at least 1")
        if price <= 0:
            raise ValueError("Order price must be positive")
        if client_ref in self.seen_refs:
            raise ValueError(f"Duplicate client_ref: {client_ref}")
        notional = qty * price
        if notional > self.config.max_order_size_rupees:
            raise ValueError(
                f"Order notional {notional:.2f} exceeds max {self.config.max_order_size_rupees:.2f}"
            )
        if side == Side.BUY and self.open_positions >= self.config.max_open_positions:
            raise ValueError("Maximum open positions reached")
        if self.realised_loss >= self.config.daily_loss_cap_rupees:
            raise LiveTradingDisabled("Daily loss cap reached")

    def record_accepted(self, client_ref: str, delta_positions: int = 0) -> None:
        """Record an accepted order so duplicate references and position counts are caught."""
        self.seen_refs.add(client_ref)
        self.open_positions = max(0, self.open_positions + delta_positions)

    def record_loss(self, amount: float) -> None:
        """Accumulate realised loss for the daily loss cap."""
        if amount > 0:
            self.realised_loss += amount
