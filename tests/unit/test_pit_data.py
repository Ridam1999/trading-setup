"""Tests for point-in-time bars, corporate actions, and source priority."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from nse_engine.data.corporate_actions import (
    apply_corporate_actions,
    load_corporate_actions,
    validate_adjustments,
)
from nse_engine.data.sources.base import OHLCV_COLUMNS
from nse_engine.data.sources.eod2_import import EOD2Source
from nse_engine.data.sources.pit_history import PitHistorySource
from nse_engine.data.store import MarketDataStore
from nse_engine.data.universe import load_pit_membership

FIXTURES = Path("tests/fixtures")


def _pit_bars() -> pd.DataFrame:
    """Load the small PIT sample containing a consolidation and a delisted symbol."""
    return PitHistorySource().load(FIXTURES / "pit_raw_sample.csv")


def _actions() -> pd.DataFrame:
    """Load the sample corporate action for the consolidation."""
    return load_corporate_actions(FIXTURES / "corporate_actions_sample.csv")


def test_load_corporate_actions_compounds_factors_on_same_ex_date(tmp_path: Path) -> None:
    """Actions effective on the same day combine into one multiplicative factor."""
    path = tmp_path / "actions.csv"
    path.write_text(
        "symbol,ex_date,action_type,price_factor\n"
        "ccavenue,2026-10-13,split,0.5\n"
        "ccavenue,2026-10-13,dividend,0.99\n",
        encoding="utf-8",
    )

    actions = load_corporate_actions(path)

    assert len(actions) == 1
    assert actions.loc[0, "symbol"] == "CCAVENUE"
    assert actions.loc[0, "ex_date"] == date(2026, 10, 13)
    assert actions.loc[0, "price_factor"] == pytest.approx(0.495)


def test_load_corporate_actions_rejects_nonpositive_factors(tmp_path: Path) -> None:
    """A zero, negative, or missing factor cannot silently corrupt adjusted prices."""
    path = tmp_path / "actions.csv"
    path.write_text(
        "symbol,ex_date,action_type,price_factor\nAAA,2024-01-03,split,0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid"):
        load_corporate_actions(path)


def test_adjustment_changes_only_bars_before_ex_date() -> None:
    """Prices scale and volume divides before the ex-date; raw close is untouched."""
    adjusted = apply_corporate_actions(_pit_bars(), _actions())

    ccavenue = adjusted.loc[adjusted["symbol"].eq("CCAVENUE")].reset_index(drop=True)
    assert ccavenue["close_raw"].tolist() == [101.0, 102.0, 1020.0]
    assert ccavenue["close"].tolist() == pytest.approx([1010.0, 1020.0, 1020.0])
    assert ccavenue["volume"].tolist() == pytest.approx([10000.0, 11000.0, 11000.0])
    assert ccavenue["source"].eq("pit_adjusted").all()
    delisted = adjusted.loc[adjusted["symbol"].eq("DELISTED")]
    assert delisted["close"].tolist() == [51.0, 52.0]
    assert delisted["volume"].tolist() == [5000.0, 6000.0]


def test_adjustment_preserves_supplied_raw_close() -> None:
    """An explicit raw close from the source wins over the current close."""
    raw = _pit_bars()
    raw.loc[raw["symbol"].eq("CCAVENUE"), "close_raw"] = 999.0

    adjusted = apply_corporate_actions(raw, _actions())

    ccavenue = adjusted.loc[adjusted["symbol"].eq("CCAVENUE")]
    assert ccavenue["close_raw"].tolist() == [999.0, 999.0, 999.0]


def test_adjustment_compounds_factors_across_ex_dates(tmp_path: Path) -> None:
    """Bars before two actions carry the product of both factors."""
    path = tmp_path / "actions.csv"
    path.write_text(
        "symbol,ex_date,action_type,price_factor\n"
        "AAA,2024-01-03,split,0.5\n"
        "AAA,2024-01-05,bonus,0.5\n",
        encoding="utf-8",
    )
    bars = pd.DataFrame(
        {
            "symbol": ["AAA"] * 4,
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)],
            "open": [100.0] * 4,
            "high": [102.0] * 4,
            "low": [99.0] * 4,
            "close": [101.0] * 4,
            "volume": [1000.0] * 4,
        }
    )

    adjusted = apply_corporate_actions(bars, load_corporate_actions(path))

    assert adjusted["close"].tolist() == pytest.approx([25.25, 50.5, 50.5, 101.0])
    assert adjusted["volume"].tolist() == pytest.approx([4000.0, 2000.0, 2000.0, 1000.0])


def test_validation_flags_unadjusted_return_across_ex_date() -> None:
    """A large raw gap at the ex-date disappears after applying the action factor."""
    actions = _actions()

    unadjusted = validate_adjustments(_pit_bars(), actions, 100.0)
    assert len(unadjusted) == 1
    assert unadjusted.loc[0, "symbol"] == "CCAVENUE"
    assert unadjusted.loc[0, "date"] == date(2026, 10, 19)
    assert validate_adjustments(apply_corporate_actions(_pit_bars(), actions), actions, 100.0).empty


def test_pit_history_source_keeps_raw_close_and_delisted_symbols() -> None:
    """The raw importer retains every row and stamps the canonical schema."""
    frame = _pit_bars()

    assert list(frame.columns) == list(OHLCV_COLUMNS)
    assert frame["source"].eq("pit_raw").all()
    assert frame["close_raw"].tolist() == frame["close"].tolist()
    assert set(frame["symbol"].astype(str)) == {"CCAVENUE", "DELISTED"}


def test_load_pit_membership_keeps_effective_dates() -> None:
    """Dated membership records delisted symbols without deleting their bars."""
    membership = load_pit_membership(FIXTURES / "pit_membership_sample.csv")

    assert membership["symbol"].tolist() == ["CCAVENUE", "DELISTED"]
    assert membership.loc[0, "start_date"] == date(2020, 1, 1)
    assert pd.isna(membership.loc[0, "end_date"])
    assert membership.loc[1, "end_date"] == date(2024, 6, 28)
    assert membership["membership_type"].tolist() == ["point_in_time", "delisted_inclusive"]


def test_load_pit_membership_rejects_bad_date_range(tmp_path: Path) -> None:
    """An end date before the start date cannot enter the membership table."""
    path = tmp_path / "membership.csv"
    path.write_text(
        "symbol,start_date,end_date\nAAA,2024-06-30,2024-06-01\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid"):
        load_pit_membership(path)


def test_pit_bars_override_eod2_bars_on_same_symbol_date(tmp_path: Path) -> None:
    """Higher-priority PIT bars replace overlapping EOD2 bars in the shared store."""
    store = MarketDataStore(tmp_path / "adjusted", tmp_path / "duckdb" / "prices.duckdb")
    store.write_ohlcv(EOD2Source().load(FIXTURES / "eod2_sample.csv"))
    pit_path = tmp_path / "pit_overlap.csv"
    pit_path.write_text(
        "symbol,date,open,high,low,close,volume\n"
        "AAA,2024-01-02,110,115,109,114,13000\n"
        "AAA,2024-01-03,114,116,112,115,14000\n",
        encoding="utf-8",
    )
    incoming = apply_corporate_actions(PitHistorySource().load(pit_path), pd.DataFrame())

    store.write_ohlcv(incoming)

    stored = store.read_ohlcv()
    aaa = stored.loc[stored["symbol"].eq("AAA")].sort_values("date").reset_index(drop=True)
    assert aaa["close"].tolist() == [114.0, 115.0]
    assert aaa["close_raw"].tolist() == [114.0, 115.0]
    assert aaa["source"].tolist() == ["pit_adjusted", "pit_adjusted"]
    assert stored.loc[stored["symbol"].eq("BBB"), "source"].eq("eod2_adjusted").all()


def test_store_does_not_expose_future_pit_bars(tmp_path: Path) -> None:
    """A point-in-time read cannot see bars after the requested date."""
    store = MarketDataStore(tmp_path / "adjusted", tmp_path / "duckdb" / "prices.duckdb")
    store.write_ohlcv(apply_corporate_actions(_pit_bars(), _actions()))

    through_2024 = store.read_ohlcv_through(date(2024, 12, 31))

    assert set(through_2024["symbol"].astype(str)) == {"DELISTED"}
    assert len(through_2024) == 2
