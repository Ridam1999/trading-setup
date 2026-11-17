"""Deterministic markdown and JSON outputs for daily paper runs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DISCLAIMER = "Research output, not advice; no guarantee of profit."


@dataclass(frozen=True)
class PlanRow:
    """One order-plan line shown in the daily report."""

    order_id: str
    symbol: str
    setup: str
    limit_price: float
    quantity: int
    stop_price: float
    target_price: float | None
    reason: str
    status: str


@dataclass(frozen=True)
class PositionRow:
    """An open paper position marked at the latest available close."""

    symbol: str
    setup: str
    open_date: date
    quantity: int
    entry_price: float
    mark_price: float
    unrealised_pnl: float
    unrealised_return: float


@dataclass(frozen=True)
class DecayRow:
    """A setup's live-versus-validated comparison."""

    setup: str
    status: str
    live_hit_rate: float
    live_adverse_return: float
    validated_hit_rate: float
    validated_adverse_return: float
    reason: str


@dataclass(frozen=True)
class PaperRunSummary:
    """Everything the daily report and JSON plan need for one run date."""

    run_date: date
    signals_count: int
    orders_count: int
    fills_count: int
    approved_setups: int
    suspended_setups: list[str]
    plans: list[PlanRow]
    positions: list[PositionRow]
    realised_pnl: float
    costs_paid: float
    cash: float
    equity: float
    decay_rows: list[DecayRow]


def _cell(value: object) -> str:
    """Render a value safely inside a markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Build a markdown table with a fixed header, even when rows are empty."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def write_order_plan(plans: list[PlanRow], path: Path) -> Path:
    """Write the next-session order plan as stable, diff-friendly JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "order_id": plan.order_id,
            "symbol": plan.symbol,
            "setup": plan.setup,
            "side": "buy",
            "limit_price": round(plan.limit_price, 6),
            "quantity": plan.quantity,
            "stop_price": round(plan.stop_price, 6),
            "target_price": round(plan.target_price, 6) if plan.target_price is not None else None,
            "reason": plan.reason,
            "status": plan.status,
        }
        for plan in plans
    ]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def write_daily_report(summary: PaperRunSummary, path: Path, data_bias_status: str) -> Path:
    """Write one self-contained daily markdown report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    plan_rows = [
        [
            plan.symbol,
            plan.setup,
            f"{plan.limit_price:.4f}",
            plan.quantity,
            f"{plan.stop_price:.4f}",
            f"{plan.target_price:.4f}" if plan.target_price is not None else "none",
            plan.reason,
            plan.status,
        ]
        for plan in summary.plans
    ]
    position_rows = [
        [
            row.symbol,
            row.setup,
            row.open_date.isoformat(),
            row.quantity,
            f"{row.entry_price:.4f}",
            f"{row.mark_price:.4f}",
            f"{row.unrealised_pnl:.2f}",
            f"{row.unrealised_return:.4%}",
        ]
        for row in summary.positions
    ]
    decay_rows = [
        [
            row.setup,
            row.status,
            f"{row.live_hit_rate:.4f}",
            f"{row.live_adverse_return:.4f}",
            f"{row.validated_hit_rate:.4f}",
            f"{row.validated_adverse_return:.4f}",
            row.reason,
        ]
        for row in summary.decay_rows
    ]
    if summary.approved_setups == 0:
        trade_note = "No approved setups are configured; there is nothing to trade."
    elif not summary.plans:
        trade_note = "No setups fired today. No trades is a normal result."
    else:
        trade_note = f"{len(summary.plans)} limit order(s) planned for the next session."
    sections = [
        f"# Paper trading report — {summary.run_date.isoformat()}",
        "",
        DISCLAIMER,
        "",
        f"Data bias status: {data_bias_status}",
        "",
        "## Run summary",
        "",
        f"- Approved setups: {summary.approved_setups}",
        f"- Signals evaluated: {summary.signals_count}",
        f"- Orders planned: {summary.orders_count}",
        f"- Fills simulated: {summary.fills_count}",
        f"- Suspended setups: {', '.join(summary.suspended_setups) or 'none'}",
        f"- {trade_note}",
        "",
        "## Order plan for the next session",
        "",
        _table(
            ["Symbol", "Setup", "Limit", "Qty", "Stop", "Target", "Reason", "Status"],
            plan_rows,
        ),
        "",
        "## Open positions and unrealised PnL",
        "",
        _table(
            ["Symbol", "Setup", "Opened", "Qty", "Entry", "Mark", "Unrealised", "Return"],
            position_rows,
        ),
        "",
        "## Realised performance and costs",
        "",
        f"- Realised PnL: {summary.realised_pnl:.2f}",
        f"- Costs paid: {summary.costs_paid:.2f}",
        f"- Cash: {summary.cash:.2f}",
        f"- Marked equity: {summary.equity:.2f}",
        "",
        "## Decay monitor versus validated band",
        "",
        _table(
            [
                "Setup",
                "Status",
                "Live hit rate",
                "Live adverse",
                "Validated hit rate",
                "Validated adverse",
                "Reason",
            ],
            decay_rows,
        ),
        "",
        DISCLAIMER,
        "",
    ]
    path.write_text("\n".join(sections), encoding="utf-8")
    return path
