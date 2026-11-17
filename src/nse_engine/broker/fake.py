"""Deterministic in-memory broker used by tests and paper simulations."""

from __future__ import annotations

from nse_engine.broker.base import (
    Holding,
    LiveTradingDisabled,
    Order,
    OrderAck,
    Position,
    Side,
)
from nse_engine.broker.config import BrokerConfig
from nse_engine.broker.guards import RiskGuards


class FakeBroker:
    """An in-memory broker with no network access and deterministic order IDs."""

    def __init__(self, config: BrokerConfig, last_prices: dict[str, float] | None = None) -> None:
        """Create a fake broker using configured guards and supplied prices."""
        self.config = config
        self.guards = RiskGuards(config)
        self._prices = dict(last_prices or {})
        self._orders: dict[str, Order] = {}
        self._acks: dict[str, OrderAck] = {}
        self._positions: dict[str, Position] = {}

    def place_limit_order(
        self, symbol: str, side: Side, qty: int, price: float, client_ref: str
    ) -> OrderAck:
        """Accept a simulated limit order after all configured guards pass."""
        cached = self._acks.get(client_ref)
        if cached is not None:
            return cached
        self.guards.check_order(symbol, side, qty, price, client_ref)
        order_id = f"fake-{client_ref}"
        ack = OrderAck.now(order_id, client_ref, "ACCEPTED")
        self._orders[order_id] = Order(
            order_id, client_ref, symbol, side, qty, price, "FILLED", filled_qty=qty
        )
        self._acks[client_ref] = ack
        delta = qty if side == Side.BUY else -qty
        self.guards.record_accepted(client_ref, 1 if side == Side.BUY else -1)
        prior = self._positions.get(symbol)
        if prior is None:
            if delta > 0:
                self._positions[symbol] = Position(symbol, delta, price)
        else:
            remaining = prior.qty + delta
            if remaining <= 0:
                self._positions.pop(symbol, None)
                if delta < 0:
                    self.guards.record_loss(abs(delta) * max(0.0, price - prior.avg_price))
            else:
                self._positions[symbol] = Position(symbol, remaining, prior.avg_price)
        return ack

    def cancel_order(self, order_id: str) -> None:
        """Cancel a simulated order that has not filled."""
        order = self._orders.get(order_id)
        if order is None:
            raise KeyError(f"Unknown order: {order_id}")
        if order.status == "FILLED":
            raise LiveTradingDisabled("Filled orders cannot be cancelled")
        self._orders[order_id] = Order(
            order.order_id,
            order.client_ref,
            order.symbol,
            order.side,
            order.qty,
            order.price,
            "CANCELLED",
            order.filled_qty,
        )

    def get_order(self, order_id: str) -> Order:
        """Return a simulated order by ID."""
        if order_id not in self._orders:
            raise KeyError(f"Unknown order: {order_id}")
        return self._orders[order_id]

    def positions(self) -> list[Position]:
        """Return current simulated positions, sorted by symbol."""
        return [self._positions[key] for key in sorted(self._positions)]

    def holdings(self) -> list[Holding]:
        """Return delivered holdings; the fake broker holds no demat inventory."""
        return []

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        """Return the configured last-traded prices for the requested symbols."""
        return {symbol: self._prices[symbol] for symbol in symbols if symbol in self._prices}
