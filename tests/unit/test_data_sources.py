"""Tests for local EOD2 and NSE bhavcopy adapters."""

from pathlib import Path

import httpx
import pytest

from nse_engine.data.sources.eod2_import import EOD2Source
from nse_engine.data.sources.nselib_source import BhavcopySource, download_bhavcopy

FIXTURES = Path("tests/fixtures")


def test_eod2_source_normalizes_adjusted_prices() -> None:
    """EOD2 columns produce the canonical adjusted-price schema."""
    frame = EOD2Source().load(FIXTURES / "eod2_sample.csv")

    assert list(frame.columns) == [
        "symbol",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_raw",
        "delivery_qty",
        "delivery_pct",
        "source",
        "ingested_at",
    ]
    assert frame.loc[0, "symbol"] == "AAA"
    assert frame.loc[0, "close"] == 104
    assert frame.loc[0, "source"] == "eod2_adjusted"
    assert frame["close_raw"].isna().all()


@pytest.mark.parametrize(
    ("fixture", "expected_date"),
    [("bhavcopy_legacy_sample.csv", "2024-01-02"), ("bhavcopy_udiff_sample.csv", "2024-01-03")],
)
def test_bhavcopy_source_supports_both_formats(fixture: str, expected_date: str) -> None:
    """Legacy and UDiFF headers normalize to raw daily bars."""
    frame = BhavcopySource().load(FIXTURES / fixture)

    assert len(frame) == 2
    assert frame.loc[0, "date"].isoformat() == expected_date
    assert frame["source"].eq("nse_bhavcopy_raw").all()


def test_empty_source_fails_loudly(tmp_path: Path) -> None:
    """An empty file is rejected instead of being treated as a valid update."""
    empty = tmp_path / "empty.csv"
    empty.write_text("SYMBOL,DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n", encoding="utf-8")

    with pytest.raises(ValueError, match="empty market-data"):
        EOD2Source().load(empty)


def test_empty_download_does_not_overwrite_existing_file(tmp_path: Path, monkeypatch) -> None:
    """A successful but empty HTTP response never replaces good source data."""
    destination = tmp_path / "bhavcopy.zip"
    destination.write_bytes(b"existing good data")

    def get_empty_response(*_args, **_kwargs) -> httpx.Response:
        """Return an empty success response without network access."""
        request = httpx.Request("GET", "https://example.invalid/file.zip")
        return httpx.Response(200, request=request, content=b"")

    monkeypatch.setattr("nse_engine.data.sources.nselib_source.httpx.get", get_empty_response)
    with pytest.raises(ValueError, match="response was empty"):
        download_bhavcopy("https://example.invalid/file.zip", destination, 1, 0.1, 1)

    assert destination.read_bytes() == b"existing good data"
