"""Readiness gate for the frozen factor study; never runs the study itself."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from nse_engine.factor.config import (
    FactorConfig,
    FactorDataConfig,
    HypothesisId,
    requires_fundamentals,
    resolve_hypotheses,
)
from nse_engine.study.config import load_study_settings
from nse_engine.study.trials import TrialRegistry

DISCLAIMER = "Research output, not advice; no guarantee of profit."


@dataclass(frozen=True)
class ReadinessCheck:
    """One named readiness condition and its outcome."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ReadinessReport:
    """Collection of readiness conditions for the factor study."""

    checks: tuple[ReadinessCheck, ...]

    @property
    def ready(self) -> bool:
        """Return whether every condition passes."""
        return all(check.ok for check in self.checks)

    @property
    def blockers(self) -> tuple[str, ...]:
        """Return the names of failed conditions."""
        return tuple(check.name for check in self.checks if not check.ok)


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_preregistration(path: Path, expected_sha256: str) -> bool:
    """Return whether the frozen document still matches its configured hash."""
    if not path.is_file():
        return False
    return sha256_file(path) == expected_sha256


def _holdout_locked(registry: TrialRegistry) -> bool:
    """Return whether the registry contains no holdout trials or unlocks."""
    rows = registry.rows(include_unlocks=True)
    return not any(
        row["split"] == "holdout" or row["setup_name"] == "__holdout_unlock__" for row in rows
    )


def assess_readiness(
    config: FactorConfig,
    data: FactorDataConfig,
    prereg_path: Path,
    registry: TrialRegistry | None = None,
    hypothesis_ids: Sequence[HypothesisId] | None = None,
) -> ReadinessReport:
    """Check design integrity, required inputs, causal lags, and holdout state."""
    selected_ids = tuple(item.id for item in resolve_hypotheses(config, hypothesis_ids))
    fundamentals_required = requires_fundamentals(selected_ids)
    prices = data.pit_prices_dir
    fundamentals = data.fundamentals_path
    membership = data.membership_path
    trial_registry = registry or TrialRegistry(load_study_settings().trial_registry_path)

    checks: list[ReadinessCheck] = [
        ReadinessCheck(
            "preregistration_frozen",
            verify_preregistration(prereg_path, config.prereg_sha256),
            f"Expected SHA-256 {config.prereg_sha256}",
        ),
        ReadinessCheck(
            "pit_prices_dir",
            prices is not None and prices.is_dir(),
            f"Missing point-in-time price directory: {prices}",
        ),
    ]
    if fundamentals_required:
        checks.append(
            ReadinessCheck(
                "fundamentals_path",
                fundamentals is not None and fundamentals.is_file(),
                f"Missing announcement-dated fundamentals file: {fundamentals}",
            )
        )
    checks.extend(
        [
            ReadinessCheck(
                "membership_path",
                membership is not None and membership.is_file(),
                f"Missing point-in-time membership file: {membership}",
            ),
            ReadinessCheck(
                "delisted_inclusive_universe",
                config.universe.delisted_inclusive,
                "Universe must retain names that later delist",
            ),
            ReadinessCheck(
                "signal_to_order_lag",
                config.construction.signal_to_order_lag_sessions >= 1,
                "Signals may only create orders for a later session",
            ),
            ReadinessCheck(
                "announcement_lag",
                config.fundamentals.announcement_lag_sessions >= 1,
                "Fundamental values require a disclosure lag",
            ),
            ReadinessCheck(
                "restatement_leak_test",
                config.fundamentals.restatement_leak_test_required,
                "A restatement must not affect earlier rebalances",
            ),
            ReadinessCheck(
                "pit_price_requirement",
                config.price.requires_pit_delisted_inclusive_prices,
                "Survivor-biased EOD2 prices are inadmissible",
            ),
            ReadinessCheck(
                "holdout_locked",
                _holdout_locked(trial_registry),
                "No holdout trials or unlock audit rows may exist",
            ),
            ReadinessCheck(
                "budget_declared",
                config.tuition_budget_inr >= 0,
                f"Tuition budget is INR {config.tuition_budget_inr}; zero forbids vendor spend",
            ),
        ]
    )
    return ReadinessReport(tuple(checks))
