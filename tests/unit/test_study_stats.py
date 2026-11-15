"""Reference tests for event-study statistics and append-only trial records."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from statsmodels.stats.multitest import multipletests

from nse_engine.setups.base import SetupConfig
from nse_engine.study.stats import (
    benjamini_hochberg,
    block_bootstrap_expectancy,
    date_clustered_t_statistic,
)
from nse_engine.study.trials import TrialRegistry


def test_benjamini_hochberg_matches_statsmodels_reference() -> None:
    """Our FDR implementation agrees with statsmodels on a reference vector."""
    p_values = [0.01, 0.04, 0.03, 0.8]
    adjusted = benjamini_hochberg(p_values)
    reference = multipletests(p_values, method="fdr_bh")[1]

    np.testing.assert_allclose(adjusted, reference)


def test_date_clustered_t_averages_within_signal_date() -> None:
    """Repeated symbols on a date count as one independent date cluster."""
    frame = pd.DataFrame(
        {
            "signal_date": ["d1", "d1", "d2", "d2", "d3", "d3"],
            "excess_return": [0.08, 0.12, 0.18, 0.22, 0.28, 0.32],
        }
    )

    statistic, p_value, count = date_clustered_t_statistic(frame)

    assert count == 3
    assert statistic > 0
    assert 0 < p_value < 1


def test_block_bootstrap_is_deterministic_and_date_clustered() -> None:
    """Bootstrap results repeat for a seed and resample per-date means."""
    frame = pd.DataFrame(
        {
            "signal_date": ["d1", "d1", "d2", "d2", "d3", "d3"],
            "net_return": [0.0, 0.2, 0.1, 0.3, -0.1, 0.1],
        }
    )
    first = block_bootstrap_expectancy(frame, "net_return", 200, 2, 17, 0.95)
    second = block_bootstrap_expectancy(frame, "net_return", 200, 2, 17, 0.95)

    assert first == second
    assert first[0] <= frame.groupby("signal_date")["net_return"].mean().mean() <= first[1]


def _setup_config() -> SetupConfig:
    """Return a minimal valid setup for registry tests."""
    return SetupConfig(
        name="test",
        universe="test_universe",
        rationale="A test rationale.",
        params={"window": 5},
        filters={"minimum_price": 1, "minimum_average_traded_value": 0},
        exit_rules={},
    )


def test_registry_is_append_only_and_audits_unlock(tmp_path: Path) -> None:
    """Trials append, unlocks are auditable, and update/delete are blocked."""
    registry = TrialRegistry(tmp_path / "trials.sqlite")
    trial_id = registry.append_trial(_setup_config(), "train", 5, {"expectancy": 0.01})
    unlock_id = registry.record_holdout_unlock()

    assert registry.trial_count() == 1
    assert [row["trial_id"] for row in registry.rows()] == [trial_id]
    assert registry.rows(include_unlocks=True)[1]["trial_id"] == unlock_id
    with pytest.raises(Exception, match="append-only"):
        with registry._connect() as connection:
            connection.execute("UPDATE trials SET split='validation' WHERE trial_id=?", (trial_id,))
    with pytest.raises(Exception, match="append-only"):
        with registry._connect() as connection:
            connection.execute("DELETE FROM trials WHERE trial_id=?", (trial_id,))
    assert json.loads(registry.rows()[0]["metrics_json"])["expectancy"] == 0.01
