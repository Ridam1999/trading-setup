"""Validated-edge decay monitor for live paper results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nse_engine.paper.config import DecaySettings

VALIDATION_SPLITS = ("holdout", "validation", "train")
ACTIVE = "active"
SUSPENDED = "suspended"


@dataclass(frozen=True)
class ValidatedBand:
    """Per-trade reference values recorded when a setup was approved."""

    setup: str
    horizon: int
    hit_rate: float
    adverse_return: float

    def tolerated_adverse_return(self, tolerance: float) -> float:
        """Return the most negative single-trade return still inside the band."""
        return self.adverse_return * tolerance


@dataclass(frozen=True)
class DecayDecision:
    """Result of comparing live closed trades against a validated band."""

    setup: str
    status: str
    reason: str
    breach_count: int
    live_hit_rate: float
    live_adverse_return: float


def validated_band(entry: dict[str, Any]) -> ValidatedBand:
    """Extract the most recent validated per-trade band from an approval entry."""
    metrics = entry.get("validated_metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise ValueError(f"No validated_metrics recorded for setup {entry.get('setup')!r}")
    for split in VALIDATION_SPLITS:
        summary = metrics.get(split)
        if not isinstance(summary, dict):
            continue
        net = summary.get("net_return")
        if not isinstance(net, dict):
            continue
        hit_rate = net.get("hit_rate")
        worst_loss = net.get("worst_loss")
        if not isinstance(hit_rate, int | float) or not isinstance(worst_loss, int | float):
            continue
        if float(hit_rate) <= 0 or float(worst_loss) >= 0:
            continue
        horizon = entry.get("horizon", 1)
        if not isinstance(horizon, int):
            horizon = 1
        return ValidatedBand(
            setup=str(entry.get("setup", "")),
            horizon=horizon,
            hit_rate=float(hit_rate),
            adverse_return=float(worst_loss),
        )
    raise ValueError(f"No usable hit-rate and worst-loss metrics for setup {entry.get('setup')!r}")


def evaluate_decay(
    setup: str,
    returns: list[float],
    band: ValidatedBand,
    settings: DecaySettings,
    previous_status: str = ACTIVE,
    previous_breach_count: int = 0,
) -> DecayDecision:
    """Return a suspension decision from live closed-trade returns only.

    The live worst single-trade return is used as the drawdown proxy because
    approved metrics store per-trade summaries rather than an equity curve.
    A breach persists only when it repeats in consecutive evaluations.
    """
    if len(returns) < settings.minimum_trades:
        return DecayDecision(
            setup=setup,
            status=previous_status,
            reason=f"insufficient_trades:{len(returns)}/{settings.minimum_trades}",
            breach_count=previous_breach_count,
            live_hit_rate=0.0,
            live_adverse_return=0.0,
        )

    live_hit_rate = float(sum(value > 0 for value in returns) / len(returns))
    live_adverse_return = float(min(returns))
    minimum_hit_rate = band.hit_rate * settings.hit_rate_tolerance
    tolerated_loss = band.tolerated_adverse_return(settings.drawdown_tolerance)
    hit_breach = live_hit_rate < minimum_hit_rate
    drawdown_breach = live_adverse_return < tolerated_loss

    reasons: list[str] = []
    if hit_breach:
        reasons.append(f"hit_rate {live_hit_rate:.4f} < {minimum_hit_rate:.4f}")
    if drawdown_breach:
        reasons.append(f"adverse_return {live_adverse_return:.4f} < {tolerated_loss:.4f}")
    breach_count = previous_breach_count + 1 if reasons else 0
    status = (
        SUSPENDED
        if previous_status == SUSPENDED or breach_count >= settings.consecutive_breaches
        else ACTIVE
    )
    reason = ", ".join(reasons) if reasons else "within_validated_band"
    return DecayDecision(
        setup=setup,
        status=status,
        reason=reason,
        breach_count=breach_count,
        live_hit_rate=live_hit_rate,
        live_adverse_return=live_adverse_return,
    )
