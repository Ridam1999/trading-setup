"""Append-only SQLite ledger for paper runs, orders, fills, and positions."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

Connection = sqlite3.Connection | None


@dataclass(frozen=True)
class OrderRecord:
    """A pending or resolved paper order created from a close-of-day signal."""

    order_id: str
    run_date: date
    symbol: str
    setup: str
    side: str
    limit_price: float
    signal_close: float
    quantity: int
    stop_price: float
    target_price: float | None
    reason: str
    status: str
    client_ref: str


@dataclass(frozen=True)
class PaperPosition:
    """An open delivery position recorded in the paper ledger."""

    position_id: str
    symbol: str
    setup: str
    open_date: date
    quantity: int
    entry_price: float
    stop_price: float
    target_price: float | None
    entry_charges: float


@dataclass(frozen=True)
class RunRecord:
    """Summary counts recorded atomically with one idempotent paper run."""

    run_date: date
    status: str
    signals_count: int
    orders_count: int
    fills_count: int


class PaperLedger:
    """Durable paper-trading state with one row per run date."""

    def __init__(self, path: Path) -> None:
        """Create the schema without writing any results."""
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_date TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    signals_count INTEGER NOT NULL,
                    orders_count INTEGER NOT NULL,
                    fills_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS signals (
                    run_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    setup TEXT NOT NULL,
                    signal_date TEXT NOT NULL,
                    close REAL NOT NULL,
                    accepted INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    PRIMARY KEY (run_date, symbol, setup)
                );
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    run_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    setup TEXT NOT NULL,
                    side TEXT NOT NULL,
                    limit_price REAL NOT NULL,
                    signal_close REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    stop_price REAL NOT NULL,
                    target_price REAL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    client_ref TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL UNIQUE,
                    fill_date TEXT NOT NULL,
                    fill_price REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    charges REAL NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders(order_id)
                );
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    setup TEXT NOT NULL,
                    open_date TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_price REAL NOT NULL,
                    target_price REAL,
                    entry_charges REAL NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_date TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    setup TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    charges REAL NOT NULL,
                    cash_delta REAL NOT NULL,
                    equity REAL,
                    net_return REAL,
                    net_pnl REAL,
                    note TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS setup_state (
                    setup TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    breach_count INTEGER NOT NULL,
                    updated_at_utc TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cash (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    balance REAL NOT NULL,
                    initial_capital REAL NOT NULL
                );
                """
            )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield one connection for a whole run, committing only on success."""
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def has_run(self, run_date: date, connection: Connection = None) -> bool:
        """Return whether this date already has an idempotent run."""
        active = connection or self._connect()
        try:
            row = active.execute(
                "SELECT 1 FROM runs WHERE run_date = ?", (run_date.isoformat(),)
            ).fetchone()
        finally:
            if connection is None:
                active.close()
        return row is not None

    def run_record(self, run_date: date, connection: Connection = None) -> RunRecord | None:
        """Return the stored summary for a previously completed run."""
        active = connection or self._connect()
        try:
            active.row_factory = sqlite3.Row
            row = active.execute(
                "SELECT * FROM runs WHERE run_date = ?", (run_date.isoformat(),)
            ).fetchone()
        finally:
            if connection is None:
                active.close()
        if row is None:
            return None
        return RunRecord(
            run_date=date.fromisoformat(str(row["run_date"])),
            status=str(row["status"]),
            signals_count=int(row["signals_count"]),
            orders_count=int(row["orders_count"]),
            fills_count=int(row["fills_count"]),
        )

    def initialize_capital(self, initial_capital: float, connection: Connection = None) -> None:
        """Seed the cash account once; later calls are no-ops."""
        active = connection or self._connect()
        try:
            active.execute(
                "INSERT OR IGNORE INTO cash (id, balance, initial_capital) VALUES (1, ?, ?)",
                (initial_capital, initial_capital),
            )
        finally:
            if connection is None:
                active.close()

    def cash_balance(self, connection: Connection = None) -> float:
        """Return current uninvested cash."""
        active = connection or self._connect()
        try:
            row = active.execute("SELECT balance FROM cash WHERE id = 1").fetchone()
        finally:
            if connection is None:
                active.close()
        if row is None:
            raise RuntimeError("Paper ledger capital has not been initialized")
        return float(row[0])

    def open_positions(self, connection: Connection = None) -> list[PaperPosition]:
        """Return all positions that are still open."""
        active = connection or self._connect()
        try:
            active.row_factory = sqlite3.Row
            rows = active.execute(
                "SELECT * FROM positions WHERE status = 'open' ORDER BY open_date, symbol"
            ).fetchall()
        finally:
            if connection is None:
                active.close()
        return [self._position_from_row(row) for row in rows]

    @staticmethod
    def _position_from_row(row: sqlite3.Row) -> PaperPosition:
        """Convert a position row into its typed representation."""
        return PaperPosition(
            position_id=str(row["position_id"]),
            symbol=str(row["symbol"]),
            setup=str(row["setup"]),
            open_date=date.fromisoformat(str(row["open_date"])),
            quantity=int(row["quantity"]),
            entry_price=float(row["entry_price"]),
            stop_price=float(row["stop_price"]),
            target_price=(float(row["target_price"]) if row["target_price"] is not None else None),
            entry_charges=float(row["entry_charges"]),
        )

    def pending_orders(self, connection: Connection = None) -> list[OrderRecord]:
        """Return orders awaiting the next session's limit-fill simulation."""
        active = connection or self._connect()
        try:
            active.row_factory = sqlite3.Row
            rows = active.execute(
                "SELECT * FROM orders WHERE status = 'pending' ORDER BY run_date, symbol"
            ).fetchall()
        finally:
            if connection is None:
                active.close()
        return [self._order_from_row(row) for row in rows]

    @staticmethod
    def _order_from_row(row: sqlite3.Row) -> OrderRecord:
        """Convert an order row into its typed representation."""
        return OrderRecord(
            order_id=str(row["order_id"]),
            run_date=date.fromisoformat(str(row["run_date"])),
            symbol=str(row["symbol"]),
            setup=str(row["setup"]),
            side=str(row["side"]),
            limit_price=float(row["limit_price"]),
            signal_close=float(row["signal_close"]),
            quantity=int(row["quantity"]),
            stop_price=float(row["stop_price"]),
            target_price=(float(row["target_price"]) if row["target_price"] is not None else None),
            reason=str(row["reason"]),
            status=str(row["status"]),
            client_ref=str(row["client_ref"]),
        )

    def record_signal(
        self,
        run_date: date,
        symbol: str,
        setup: str,
        close: float,
        accepted: bool,
        reason: str,
        connection: Connection = None,
    ) -> None:
        """Record one evaluated close-of-day signal and its acceptance decision."""
        self._execute(
            connection,
            """INSERT OR REPLACE INTO signals
               (run_date, symbol, setup, signal_date, close, accepted, reason)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run_date.isoformat(),
                symbol,
                setup,
                run_date.isoformat(),
                close,
                int(accepted),
                reason,
            ),
        )

    def insert_order(self, order: OrderRecord, connection: Connection = None) -> None:
        """Append one limit order; idempotent run checks prevent duplicates."""
        self._execute(
            connection,
            """INSERT INTO orders (
                order_id, run_date, symbol, setup, side, limit_price, signal_close,
                quantity, stop_price, target_price, reason, status, client_ref
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order.order_id,
                order.run_date.isoformat(),
                order.symbol,
                order.setup,
                order.side,
                order.limit_price,
                order.signal_close,
                order.quantity,
                order.stop_price,
                order.target_price,
                order.reason,
                order.status,
                order.client_ref,
            ),
        )

    def update_order_status(
        self, order_id: str, status: str, connection: Connection = None
    ) -> None:
        """Move an order to filled, unfilled, or cancelled."""
        self._execute(
            connection,
            "UPDATE orders SET status = ? WHERE order_id = ?",
            (status, order_id),
        )

    def orders_for_run(self, run_date: date, connection: Connection = None) -> list[OrderRecord]:
        """Return every order created on one run date, in stable order."""
        active = connection or self._connect()
        try:
            active.row_factory = sqlite3.Row
            rows = active.execute(
                "SELECT * FROM orders WHERE run_date = ? ORDER BY setup, symbol",
                (run_date.isoformat(),),
            ).fetchall()
        finally:
            if connection is None:
                active.close()
        return [self._order_from_row(row) for row in rows]

    def record_fill(
        self,
        order_id: str,
        fill_date: date,
        fill_price: float,
        quantity: int,
        status: str,
        charges: float,
        connection: Connection = None,
    ) -> None:
        """Record the simulated next-session outcome of one order."""
        self._execute(
            connection,
            """INSERT INTO fills
               (fill_id, order_id, fill_date, fill_price, quantity, status, charges)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                f"fill-{order_id}",
                order_id,
                fill_date.isoformat(),
                fill_price,
                quantity,
                status,
                charges,
            ),
        )

    def open_position(self, position: PaperPosition, connection: Connection = None) -> None:
        """Append a new open position after a simulated buy fill."""
        self._execute(
            connection,
            """INSERT INTO positions (
                position_id, symbol, setup, open_date, quantity, entry_price,
                stop_price, target_price, entry_charges, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')""",
            (
                position.position_id,
                position.symbol,
                position.setup,
                position.open_date.isoformat(),
                position.quantity,
                position.entry_price,
                position.stop_price,
                position.target_price,
                position.entry_charges,
            ),
        )

    def close_position(self, position_id: str, connection: Connection = None) -> None:
        """Mark a position closed after an exit fill."""
        self._execute(
            connection,
            "UPDATE positions SET status = 'closed' WHERE position_id = ?",
            (position_id,),
        )

    def append_ledger(
        self,
        entry_date: date,
        event_type: str,
        symbol: str,
        setup: str,
        quantity: int,
        price: float,
        charges: float,
        cash_delta: float,
        note: str,
        equity: float | None = None,
        net_return: float | None = None,
        net_pnl: float | None = None,
        connection: Connection = None,
    ) -> None:
        """Append one accounting event, optionally inside an existing transaction."""
        self._execute(
            connection,
            """INSERT INTO ledger (
                entry_date, event_type, symbol, setup, quantity, price, charges,
                cash_delta, equity, net_return, net_pnl, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry_date.isoformat(),
                event_type,
                symbol,
                setup,
                quantity,
                price,
                charges,
                cash_delta,
                equity,
                net_return,
                net_pnl,
                note,
            ),
        )

    def set_cash(self, balance: float, connection: Connection = None) -> None:
        """Replace the single cash balance row."""
        self._execute(connection, "UPDATE cash SET balance = ? WHERE id = 1", (balance,))

    def record_run(self, record: RunRecord, connection: Connection = None) -> None:
        """Finalize one date's run atomically with its other rows."""
        self._execute(
            connection,
            """INSERT INTO runs (
                run_date, status, created_at_utc, signals_count, orders_count, fills_count
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                record.run_date.isoformat(),
                record.status,
                datetime.now(UTC).isoformat(),
                record.signals_count,
                record.orders_count,
                record.fills_count,
            ),
        )

    def setup_state(self, setup: str, connection: Connection = None) -> tuple[str, int]:
        """Return a setup's status and consecutive decay-breach count."""
        active = connection or self._connect()
        try:
            row = active.execute(
                "SELECT status, breach_count FROM setup_state WHERE setup = ?", (setup,)
            ).fetchone()
        finally:
            if connection is None:
                active.close()
        if row is None:
            return ("active", 0)
        return (str(row[0]), int(row[1]))

    def setup_decay_detail(self, setup: str, connection: Connection = None) -> tuple[str, int, str]:
        """Return a setup's status, breach count, and last evaluation reason."""
        active = connection or self._connect()
        try:
            row = active.execute(
                "SELECT status, breach_count, reason FROM setup_state WHERE setup = ?", (setup,)
            ).fetchone()
        finally:
            if connection is None:
                active.close()
        if row is None:
            return ("active", 0, "not_evaluated")
        return (str(row[0]), int(row[1]), str(row[2]))

    def performance_totals(
        self, run_date: date, connection: Connection = None
    ) -> tuple[float, float]:
        """Return cumulative realised PnL and charges paid through one date."""
        active = connection or self._connect()
        try:
            row = active.execute(
                """SELECT COALESCE(SUM(net_pnl), 0), COALESCE(SUM(charges), 0)
                   FROM ledger WHERE entry_date <= ?""",
                (run_date.isoformat(),),
            ).fetchone()
        finally:
            if connection is None:
                active.close()
        return (float(row[0]), float(row[1]))

    def suspended_setups(self, connection: Connection = None) -> list[str]:
        """Return setup names that must no longer generate orders."""
        active = connection or self._connect()
        try:
            rows = active.execute(
                "SELECT setup FROM setup_state WHERE status = 'suspended' ORDER BY setup"
            ).fetchall()
        finally:
            if connection is None:
                active.close()
        return [str(row[0]) for row in rows]

    def update_setup_state(
        self,
        setup: str,
        status: str,
        reason: str,
        breach_count: int,
        connection: Connection = None,
    ) -> None:
        """Persist a setup's decay evaluation or suspension."""
        self._execute(
            connection,
            """INSERT INTO setup_state (setup, status, reason, breach_count, updated_at_utc)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(setup) DO UPDATE SET
                   status = excluded.status,
                   reason = excluded.reason,
                   breach_count = excluded.breach_count,
                   updated_at_utc = excluded.updated_at_utc""",
            (setup, status, reason, breach_count, datetime.now(UTC).isoformat()),
        )

    def setup_trade_returns(self, setup: str, connection: Connection = None) -> list[float]:
        """Return closed-trade returns for one setup, in ledger order."""
        active = connection or self._connect()
        try:
            rows = active.execute(
                """SELECT net_return FROM ledger
                   WHERE event_type = 'exit' AND setup = ? AND net_return IS NOT NULL
                   ORDER BY entry_id""",
                (setup,),
            ).fetchall()
        finally:
            if connection is None:
                active.close()
        return [float(row[0]) for row in rows]

    def report_rows(self) -> dict[str, list[dict[str, Any]]]:
        """Return report-friendly copies of the latest ledger tables."""
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            tables = {
                "signals": "SELECT * FROM signals ORDER BY run_date, symbol, setup",
                "orders": "SELECT * FROM orders ORDER BY run_date, symbol",
                "fills": "SELECT * FROM fills ORDER BY fill_date, order_id",
                "positions": "SELECT * FROM positions ORDER BY open_date, symbol",
                "ledger": "SELECT * FROM ledger ORDER BY entry_id",
            }
            return {
                name: [dict(row) for row in connection.execute(query).fetchall()]
                for name, query in tables.items()
            }

    def _execute(self, connection: Connection, statement: str, parameters: tuple[Any, ...]) -> None:
        """Run one write either on the supplied transaction or a short-lived connection."""
        if connection is not None:
            connection.execute(statement, parameters)
            return
        with self._connect() as own_connection:
            own_connection.execute(statement, parameters)

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with foreign keys enabled."""
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
