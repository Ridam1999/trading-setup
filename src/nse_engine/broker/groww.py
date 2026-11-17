"""Disabled Groww broker skeleton.

No method in this adapter performs network I/O. Order methods always raise
:class:`LiveTradingDisabled`: even when an owner sets the environment flags,
live submission is a separate, explicitly authorised future change.
"""

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
from nse_engine.broker.ratelimit import TokenBucket

_DISABLED = "GrowwBroker is a disabled skeleton; no live order path is implemented."


class GrowwBroker:
    """Configuration-only Groww adapter that can never reach a live endpoint."""

    def __init__(self, config: BrokerConfig) -> None:
        """Wire configured rate buckets without opening any connection."""
        self.config = config
        self.order_bucket = TokenBucket(
            config.order_rate.burst, config.order_rate.refill_per_second
        )
        self.data_bucket = TokenBucket(config.data_rate.burst, config.data_rate.refill_per_second)

    def can_call_order_endpoint(self) -> bool:
        """Consume an order token; this proves config wiring without calling Groww."""
        return self.order_bucket.allow()

    def can_call_data_endpoint(self) -> bool:
        """Consume a data token; this proves config wiring without calling Groww."""
        return self.data_bucket.allow()

    def place_limit_order(
        self, symbol: str, side: Side, qty: int, price: float, client_ref: str
    ) -> OrderAck:
        """Refuse to place an order; live submission is not implemented."""
        raise LiveTradingDisabled(_DISABLED)

    def cancel_order(self, order_id: str) -> None:
        """Refuse to cancel an order; live submission is not implemented."""
        raise LiveTradingDisabled(_DISABLED)

    def get_order(self, order_id: str) -> Order:
        """Refuse to query an order; the skeleton holds no live order state."""
        raise LiveTradingDisabled(_DISABLED)

    def positions(self) -> list[Position]:
        """Refuse to query positions; the skeleton holds no broker state."""
        raise LiveTradingDisabled(_DISABLED)

    def holdings(self) -> list[Holding]:
        """Refuse to query holdings; the skeleton holds no broker state."""
        raise LiveTradingDisabled(_DISABLED)

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        """Refuse to fetch prices; no market-data endpoint is implemented."""
        raise LiveTradingDisabled(_DISABLED)
