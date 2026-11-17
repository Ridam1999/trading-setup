"""Offline tests for the public point-in-time acquisition bot."""

from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import date
from pathlib import Path

import httpx
import pandas as pd  # type: ignore[import-untyped]
from typer.testing import CliRunner

from nse_engine.cli import app
from nse_engine.factor.acquire import (
    bhavcopy_destination,
    bhavcopy_url,
    build_membership,
    download_announcements,
    download_bhavcopies,
    download_filing_pdfs,
)
from nse_engine.factor.config import AcquisitionConfig, load_factor_config

CONFIG_DIR = Path("config")


def _settings(tmp_path: Path, start: date, end: date, udiff_start: date) -> AcquisitionConfig:
    """Build an isolated public acquisition config with no network delay."""
    return AcquisitionConfig(
        start_date=start,
        end_date=end,
        udiff_start_date=udiff_start,
        session_url="https://session.test",
        legacy_url_template="https://archive.test/{filename}",
        udiff_url_template="https://archive.test/{compact_date}.csv",
        announcement_url_template="https://api.test?from_date={start}&to_date={end}",
        announcement_raw_dir=tmp_path / "announcement_json",
        filing_dir=tmp_path / "filings",
        weekdays=[0, 1, 2, 3, 4],
        announcement_chunk_days=2,
        request_delay_seconds=0,
        timeout_seconds=1,
        retry_attempts=1,
        exclude_symbol_pattern="(?:ETF|INDEX)$",
        exclude_symbols=["NIFTYBEES"],
    )


def _bars_csv(day: date, rows: list[tuple[str, float, float]]) -> bytes:
    """Create a minimal legacy/UDiFF bhavcopy CSV for one date."""
    frame = pd.DataFrame(
        [
            {
                "SYMBOL": symbol,
                "DATE": day.strftime("%d-%b-%Y"),
                "OPEN": close,
                "HIGH": close,
                "LOW": close,
                "CLOSE": close,
                "VOLUME": volume,
                "SERIES": "EQ",
            }
            for symbol, close, volume in rows
        ]
    )
    return frame.to_csv(index=False).encode("utf-8")


def _zip_csv(content: bytes, name: str) -> bytes:
    """Wrap a CSV in a one-file ZIP archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


def _write_snapshot(
    directory: Path, day: date, rows: list[tuple[str, float, float]], settings: AcquisitionConfig
) -> Path:
    """Write a synthetic dated snapshot to the configured PIT layout."""
    path = bhavcopy_destination(directory, day, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_bars_csv(day, rows))
    return path


def _membership_at(frame: pd.DataFrame, day: date) -> pd.DataFrame:
    """Return one rebalance snapshot in a stable comparison shape."""
    mask = pd.to_datetime(frame["rebalance_date"]).dt.date == day
    return (
        frame.loc[mask]
        .sort_values("symbol")[["symbol", "rank", "traded_value"]]
        .reset_index(drop=True)
    )


def test_bhavcopy_paths_follow_configured_format_boundary(tmp_path: Path) -> None:
    """Legacy dates stay ZIP files and UDiFF dates stay CSV files."""
    settings = _settings(tmp_path, date(2024, 6, 28), date(2024, 7, 1), date(2024, 7, 1))
    legacy_day = date(2024, 6, 28)
    udiff_day = date(2024, 7, 1)

    assert bhavcopy_destination(tmp_path, legacy_day, settings).name == "bhavcopy.zip"
    assert bhavcopy_destination(tmp_path, udiff_day, settings).name == "bhavcopy.csv"
    assert "cm28JUN2024bhav.csv.zip" in bhavcopy_url(legacy_day, settings)
    assert "01072024.csv" in bhavcopy_url(udiff_day, settings)


def test_download_bhavcopies_writes_snapshots_and_preserves_existing(tmp_path: Path) -> None:
    """Dated downloads are immutable across repeated runs."""
    settings = _settings(tmp_path, date(2024, 6, 28), date(2024, 7, 1), date(2024, 7, 1))
    destination = tmp_path / "prices"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "cm28JUN2024bhav.csv.zip" in url:
            content = _zip_csv(_bars_csv(date(2024, 6, 28), [("AAA", 100, 1000)]), "cm.csv")
            return httpx.Response(200, content=content)
        if "01072024.csv" in url:
            return httpx.Response(200, content=_bars_csv(date(2024, 7, 1), [("AAA", 101, 1100)]))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        first = download_bhavcopies(settings, destination, client)
        second = download_bhavcopies(settings, destination, client)

    assert first.downloaded == (date(2024, 6, 28), date(2024, 7, 1))
    assert first.skipped == ()
    assert second.downloaded == ()
    assert second.preserved == (date(2024, 6, 28), date(2024, 7, 1))
    assert bhavcopy_destination(destination, date(2024, 6, 28), settings).is_file()
    assert bhavcopy_destination(destination, date(2024, 7, 1), settings).is_file()


def test_membership_cannot_use_later_liquidity_spikes(tmp_path: Path) -> None:
    """A later file cannot change an earlier membership snapshot."""
    settings = _settings(tmp_path, date(2024, 1, 1), date(2024, 2, 29), date(2024, 1, 1))
    factor = load_factor_config(CONFIG_DIR)
    january = date(2024, 1, 31)
    february = date(2024, 2, 29)
    price_dir = tmp_path / "prices"
    _write_snapshot(
        price_dir,
        january,
        [("AAA", 100, 1_000_000), ("BBB", 50, 100_000), ("NIFTYBEES", 100, 10_000_000)],
        settings,
    )
    _write_snapshot(
        price_dir,
        february,
        [("AAA", 100, 1_000_000), ("BBB", 500, 10_000_000), ("CCC", 10, 1_000_000)],
        settings,
    )

    both_output = tmp_path / "both.parquet"
    build_membership(factor, settings, price_dir, both_output)
    both = pd.read_parquet(both_output)

    january_only = tmp_path / "january_only"
    source = bhavcopy_destination(price_dir, january, settings)
    target = bhavcopy_destination(january_only, january, settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    january_output = tmp_path / "january.parquet"
    build_membership(factor, settings, january_only, january_output)
    expected = pd.read_parquet(january_output)

    pd.testing.assert_frame_equal(_membership_at(both, january), _membership_at(expected, january))
    assert "CCC" not in set(_membership_at(both, january)["symbol"])
    assert "NIFTYBEES" not in set(_membership_at(both, january)["symbol"])
    february_rows = _membership_at(both, february)
    assert "CCC" in set(february_rows["symbol"])
    top_row = february_rows.loc[february_rows["rank"] == 1].iloc[0]
    assert top_row["symbol"] == "BBB"


def test_download_announcements_writes_chunked_metadata(tmp_path: Path) -> None:
    """Announcement metadata is chunked, stored raw, and indexed in Parquet."""
    settings = _settings(tmp_path, date(2024, 1, 1), date(2024, 1, 4), date(2024, 1, 1))
    output = tmp_path / "announcements.parquet"

    def handler(request: httpx.Request) -> httpx.Response:
        if "api.test" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "symbol": "AAA",
                            "announcementDate": "2024-01-02",
                            "subject": "Results",
                            "pdfUrl": "https://pdf.test/a.pdf",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        summary = download_announcements(settings, output, client)

    assert summary.chunks == 2
    assert summary.rows == 2
    assert sorted(path.name for path in settings.announcement_raw_dir.glob("*.json")) == [
        "2024-01-01_2024-01-02.json",
        "2024-01-03_2024-01-04.json",
    ]
    frame = pd.read_parquet(output)
    assert list(frame["symbol"]) == ["AAA", "AAA"]
    assert set(frame["announcement_date"].astype(str)) == {"2024-01-02"}


def test_download_filing_pdfs_uses_deterministic_paths(tmp_path: Path) -> None:
    """Repeated filing downloads preserve one deterministic local copy."""
    settings = _settings(tmp_path, date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 1))
    announcements = tmp_path / "announcements.parquet"
    url = "https://pdf.test/filing.pdf"
    pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "announcement_date": date(2024, 1, 2),
                "headline": "Results",
                "pdf_url": url,
                "source_url": "https://api.test",
                "ingested_at": "2024-01-02T00:00:00+00:00",
            }
        ]
    ).to_parquet(announcements, index=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == url:
            return httpx.Response(200, content=b"%PDF-1.4")
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        first = download_filing_pdfs(announcements, settings, client)
        second = download_filing_pdfs(announcements, settings, client)

    files = list(settings.filing_dir.glob("AAA/*.pdf"))
    assert first == 1
    assert second == 0
    assert len(files) == 1
    assert hashlib.sha256(url.encode("utf-8")).hexdigest()[:16] in files[0].name


def test_factor_acquire_cli_help_lists_all_commands() -> None:
    """The CLI exposes every acquisition step without invoking the network."""
    result = CliRunner().invoke(app, ["factor", "acquire", "--help"])
    assert result.exit_code == 0
    for command in ("prices", "membership", "announcements", "filings", "fundamentals"):
        assert command in result.output
