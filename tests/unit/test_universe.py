"""Point-in-time liquidity universe tests."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from nse_engine.data.universe import liquidity_universe_settings, point_in_time_liquidity_membership


def test_liquidity_membership_uses_trailing_median_and_date_rank() -> None:
    """Membership responds to trailing traded value and excludes short histories."""
    rows: list[dict[str, object]] = []
    values = {"AAA": [10, 10, 100], "BBB": [20, 20, 1], "NEW": [1000]}
    for symbol, volumes in values.items():
        for index, volume in enumerate(volumes):
            rows.append(
                {
                    "symbol": symbol,
                    "date": date(2020, 1, 1) + timedelta(days=index),
                    "close": 10.0,
                    "volume": volume,
                }
            )
    frame = pd.DataFrame(rows)

    selected = point_in_time_liquidity_membership(
        frame, top_n=1, trailing_days=2, minimum_price=5, minimum_history=2
    )

    membership = frame.assign(selected=selected)
    final_day = membership.loc[membership["date"].eq(date(2020, 1, 3))]
    assert set(final_day.loc[final_day["selected"], "symbol"]) == {"AAA"}
    assert not membership.loc[membership["symbol"].eq("NEW"), "selected"].any()


def test_liquidity_settings_follow_configured_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured YAML directory controls liquidity thresholds, not the CWD."""
    config_dir = tmp_path
    (config_dir / "universe.yaml").write_text(
        "point_in_time_liquidity:\n"
        "  top_n: 5\n"
        "  trailing_days: 10\n"
        "  minimum_price: 50\n"
        "  minimum_history: 10\n"
        "  excluded_symbol_pattern: null\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NSE_ENGINE_CONFIG_DIR", str(config_dir))

    settings = liquidity_universe_settings()

    assert settings["top_n"] == 5
    assert settings["minimum_price"] == 50
    assert settings["excluded_symbol_pattern"] is None


def test_provisional_universe_config_uses_top_100() -> None:
    """The checked-in kill-stage universe is tighter than the default stock universe."""
    settings = liquidity_universe_settings(Path("config/universe_provisional.yaml"))

    assert settings["top_n"] == 100
    assert settings["minimum_history"] == 60


def test_liquidity_settings_reject_invalid_counts(tmp_path: Path) -> None:
    """Invalid thresholds fail before any universe scan starts."""
    config_path = tmp_path / "universe.yaml"
    config_path.write_text("point_in_time_liquidity: {top_n: 0}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="positive"):
        liquidity_universe_settings(config_path)
