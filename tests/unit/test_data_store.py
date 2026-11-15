"""Tests for Parquet/DuckDB storage and data-health checks."""

from pathlib import Path

import duckdb
import pandas as pd
import pytest

from nse_engine.calendar import build_calendar
from nse_engine.data.quality import check_ohlcv, write_health_report
from nse_engine.data.sources.eod2_import import EOD2Source
from nse_engine.data.sources.nselib_source import BhavcopySource
from nse_engine.data.store import MarketDataStore


def _load_fixture() -> pd.DataFrame:
    """Load a known-good tiny EOD2 sample."""
    return EOD2Source().load(Path("tests/fixtures/eod2_sample.csv"))


def test_store_persists_parquet_and_duckdb(tmp_path: Path) -> None:
    """Imported rows are queryable and an empty update cannot erase them."""
    store = MarketDataStore(tmp_path / "adjusted", tmp_path / "duckdb" / "prices.duckdb")
    original = _load_fixture()
    stored = store.write_ohlcv(original)
    assert len(stored) == 4
    assert store.read_ohlcv().shape[0] == 4
    assert (tmp_path / "adjusted" / "year=2024" / "ohlcv.parquet").is_file()

    with duckdb.connect(str(store.duckdb_path), read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM ohlcv_daily").fetchone() == (4,)

    with pytest.raises(ValueError, match="empty import"):
        store.write_ohlcv(original.iloc[0:0])
    assert store.read_ohlcv().shape[0] == 4


def test_store_replaces_duplicate_key_with_latest_ingestion(tmp_path: Path) -> None:
    """A newer import replaces only its matching symbol/date key."""
    store = MarketDataStore(tmp_path / "adjusted", tmp_path / "duckdb" / "prices.duckdb")
    initial = _load_fixture()
    store.write_ohlcv(initial)
    update = initial.iloc[[0]].copy()
    update["close"] = 103
    update["ingested_at"] = pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=1)
    merged = store.write_ohlcv(update)

    assert len(merged) == 4
    assert (
        merged.loc[
            (merged["symbol"] == "AAA") & (merged["date"] == initial.loc[0, "date"]), "close"
        ].item()
        == 103
    )


def test_store_keeps_adjusted_close_and_separate_raw_close(tmp_path: Path) -> None:
    """A raw bhavcopy cross-check does not replace the adjusted research close."""
    store = MarketDataStore(tmp_path / "adjusted", tmp_path / "duckdb" / "prices.duckdb")
    initial = _load_fixture()
    store.write_ohlcv(initial)
    raw = BhavcopySource().load(Path("tests/fixtures/bhavcopy_legacy_sample.csv")).iloc[[0]].copy()
    raw["close"] = 110
    merged = store.write_ohlcv(raw)
    match = merged.loc[
        (merged["symbol"] == "AAA") & (merged["date"] == initial.loc[0, "date"])
    ].iloc[0]

    assert match["close"] == 104
    assert match["close_raw"] == 110


def test_quality_report_flags_missing_days_and_critical_bars(tmp_path: Path) -> None:
    """Quality checks report missing observations and reject invalid OHLC."""
    frame = _load_fixture()
    extra_day = frame.loc[
        (frame["symbol"] == "AAA") & (frame["date"].astype(str) == "2024-01-02")
    ].copy()
    extra_day["date"] = pd.Timestamp("2024-01-04").date()
    extra_day[["open", "high", "low", "close"]] = [105, 107, 104, 106]
    frame = pd.concat([frame, extra_day], ignore_index=True)
    frame = frame.loc[
        ~((frame["symbol"] == "AAA") & (frame["date"].astype(str) == "2024-01-03"))
    ].copy()
    frame.loc[frame["symbol"] == "BBB", "high"] = 1
    calendar = build_calendar(frame)
    report = check_ohlcv(frame, calendar, maximum_return_pct=50, stale_close_days=3)

    assert report.critical_failures == 2
    assert report.findings["check"].eq("missing_trading_day").any()
    assert report.findings["check"].eq("invalid_ohlc_range").any()

    output = tmp_path / "reports" / "data_health.html"
    write_health_report(report, output, survivorship_biased=True)
    html = output.read_text(encoding="utf-8")
    assert "Research output, not advice" in html
    assert "survivorship bias" in html


def test_calendar_excludes_configured_holidays() -> None:
    """Configured exchange holidays are never marked as trading sessions."""
    frame = _load_fixture()
    holiday = frame.loc[0, "date"]
    calendar = build_calendar(frame, [holiday])

    assert not calendar.loc[calendar["date"] == holiday, "is_trading_day"].item()
