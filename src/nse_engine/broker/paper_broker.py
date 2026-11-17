"""Paper broker adapter that reconciles simulated orders against the paper ledger."""

from __future__ import annotations

from dataclasses import dataclass

from nse_engine.broker.base import (
    Holding,
    Order,
    OrderAck,
    Position,
    Side,
)
from nse_engine.broker.config import BrokerConfig
from nse_engine.broker.fake import FakeBroker
from nse_engine.paper.ledger import PaperLedger


@dataclass(frozen=True)
class Reconciliation:
    """Difference between the simulated broker's state and the durable ledger."""

    broker_symbols: list[str]
    ledger_symbols: list[str]
    quantity_mismatches: list[str]

    @property
    def consistent(self) -> bool:
        """Return whether broker and ledger agree on symbols and quantities."""
        return not self.quantity_mismatches and self.broker_symbols == self.ledger_symbols


class PaperBroker:
    """A broker facade over the fake adapter and the durable paper ledger."""

    def __init__(
        self,
        config: BrokerConfig,
        ledger: PaperLedger,
        last_prices: dict[str, float] | None = None,
    ) -> None:
        """Create a paper broker that never opens a network connection."""
        self.config = config
        self.ledger = ledger
        self.simulator = FakeBroker(config, last_prices)

    def place_limit_order(
        self, symbol: str, side: Side, qty: int, price: float, client_ref: str
    ) -> OrderAck:
        """Place a simulated order using the configured risk guards."""
        return self.simulator.place_limit_order(symbol, side, qty, price, client_ref)

    def cancel_order(self, order_id: str) -> None:
        """Cancel a simulated order."""
        self.simulator.cancel_order(order_id)

    def get_order(self, order_id: str) -> Order:
        """Return a simulated order by ID."""
        return self.simulator.get_order(order_id)

    def positions(self) -> list[Position]:
        """Return open positions sourced from the durable paper ledger."""
        return [
            Position(item.symbol, item.quantity, item.entry_price)
            for item in self.ledger.open_positions()
        ]

    def holdings(self) -> list[Holding]:
        """Return delivered holdings sourced from the durable paper ledger."""
        return [Holding(item.symbol, item.quantity) for item in self.ledger.open_positions()]

    def ltp(self, symbols: list[str]) -> dict[str, float]:
        """Return locally supplied last prices; no market-data endpoint is used."""
        return self.simulator.ltp(symbols)

    def reconcile(self) -> Reconciliation:
        """Compare simulated broker positions with the ledger's open positions."""
        simulated = {item.symbol: item.qty for item in self.simulator.positions()}
        recorded = {item.symbol: item.quantity for item in self.ledger.open_positions()}
        mismatches = sorted(
            symbol
            for symbol in set(simulated) & set(recorded)
            if simulated[symbol] != recorded[symbol]
        )
        return Reconciliation(
            broker_symbols=sorted(simulated),
            ledger_symbols=sorted(recorded),
            quantity_mismatches=mismatches,
        )
