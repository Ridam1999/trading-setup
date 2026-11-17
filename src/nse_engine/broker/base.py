"""Broker protocol and value types shared by every adapter.

Strategy code imports only these types. No adapter here may place a real order:
live trading is disabled by design and every live path raises
:class:`LiveTradingDisabled`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class Side(StrEnum):
    """Order side; the engine is long-only, but sells are needed to exit."""

    BUY = "BUY"
    SELL = "SELL"


class LiveTradingDisabled(RuntimeError):
    """Raised when a live order path is requested while live trading is disabled."""


class BrokerConfigError(ValueError):
    """Raised when broker configuration is missing or invalid."""


@dataclass(frozen=True)
class OrderAck:
    """Acknowledgement returned when an order is accepted or simulated."""

    order_id: str
    client_ref: str
    status: str
    accepted_at: datetime

    @classmethod
    def now(cls, order_id: str, client_ref: str, status: str) -> OrderAck:
        """Build an acknowledgement stamped with the current UTC time."""
        return cls(order_id, client_ref, status, datetime.now(UTC))


@dataclass(frozen=True)
class Order:
    """Current state of one limit order."""

    order_id: str
    client_ref: str
    symbol: str
    side: Side
    qty: int
    price: float
    status: str
    filled_qty: int = 0


@dataclass(frozen=True)
class Position:
    """An open position held by the broker."""

    symbol: str
    qty: int
    avg_price: float


@dataclass(frozen=True)
class Holding:
    """A delivered holding in the demat account."""

    symbol: str
    qty: int


class Broker(Protocol):
    """Minimal broker contract used by the research engine."""

    def place_limit_order(
        self, symbol: str, side: Side, qty: int, price: float, client_ref: str
    ) -> OrderAck:
        """Place a limit order, or refuse when live trading is disabled."""
        ...

    def cancel_order(self, order_id: str) -> None:
        """Cancel an open order, or refuse when live trading is disabled."""
        ...

    def get_order(self, order_id: str) -> Order:
        """Return the current state of one order."""
        ...

    def positions(self) -> list[Position]:
        """Return currently open positions."""
        ...

    def holdings(self) -> list[Holding]:
        """Return delivered holdings."""
        ...

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        """Return last-traded prices for the requested symbols."""
        ...
