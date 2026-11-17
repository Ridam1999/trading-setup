"""Offline tests for restatement-safe fundamentals parsing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import pytest
from pydantic import ValidationError
from pypdf import PdfWriter  # type: ignore[import-untyped]

import nse_engine.factor.fundamentals as fundamentals_module
from nse_engine.factor.acquire import AcquisitionConfig, filing_path
from nse_engine.factor.config import FundamentalExtractionConfig, load_factor_data_config
from nse_engine.factor.fundamentals import (
    extract_numeric_field,
    extract_text_from_pdf,
    parse_filings,
    select_fundamentals_as_of,
)

CONFIG_DIR = Path("config")


def _settings(tmp_path: Path) -> AcquisitionConfig:
    """Build an isolated acquisition config for filing-path lookup."""
    return AcquisitionConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        udiff_start_date=date(2024, 1, 1),
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
        exclude_symbol_pattern="(?:BEES|ETF|INDEX)$",
    )


def _fundamentals() -> FundamentalExtractionConfig:
    """Return the checked-in extraction rules."""
    config = load_factor_data_config(CONFIG_DIR)
    assert config.fundamentals is not None
    return config.fundamentals


def _write_filing(settings: AcquisitionConfig, symbol: str, announced: date, url: str) -> Path:
    """Create a placeholder at the deterministic filing path."""
    path = filing_path(settings, symbol, announced, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-placeholder")
    return path


def test_extract_numeric_field_uses_configured_labels_and_units() -> None:
    """Comma-separated values follow configured labels and crore scaling."""
    config = _fundamentals()
    text = "Diluted EPS (Rs.) 12.50\nNet Profit 1,234.50\nAverage net worth 2,000"

    eps = extract_numeric_field(
        text,
        config.fields["trailing_12m_diluted_eps"].labels,
        1.0,
        config.label_value_max_chars,
    )
    profit = extract_numeric_field(
        text,
        config.fields["trailing_12m_net_profit"].labels,
        config.crore_multiplier,
        config.label_value_max_chars,
    )
    equity = extract_numeric_field(
        text,
        config.fields["average_shareholders_equity"].labels,
        config.crore_multiplier,
        config.label_value_max_chars,
    )

    assert eps == pytest.approx(12.5)
    assert profit == pytest.approx(12_345_000_000.0)
    assert equity == pytest.approx(20_000_000_000.0)


def test_fundamental_field_set_is_frozen() -> None:
    """The parser cannot silently add or drop a pre-registered field."""
    payload = _fundamentals().model_dump()
    payload["fields"].pop("average_shareholders_equity")
    with pytest.raises(ValidationError):
        FundamentalExtractionConfig.model_validate(payload)


def test_parse_filings_is_idempotent_and_restatement_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restatements append later rows and never change earlier cutoffs."""
    settings = _settings(tmp_path)
    config = _fundamentals()
    index_path = tmp_path / "announcement_index.parquet"
    pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "announcement_date": date(2024, 1, 2),
                "headline": "Q3 results",
                "pdf_url": "https://pdf.test/q3.pdf",
                "source_url": "https://api.test/q3",
                "ingested_at": "2024-01-02T00:00:00+00:00",
            },
            {
                "symbol": "AAA",
                "announcement_date": date(2024, 6, 1),
                "headline": "Restated Q3",
                "pdf_url": "https://pdf.test/restated.pdf",
                "source_url": "https://api.test/restated",
                "ingested_at": "2024-06-01T00:00:00+00:00",
            },
        ]
    ).to_parquet(index_path, index=False)
    _write_filing(settings, "AAA", date(2024, 1, 2), "https://pdf.test/q3.pdf")
    _write_filing(settings, "AAA", date(2024, 6, 1), "https://pdf.test/restated.pdf")

    def fake_text(path: Path) -> str:
        if "2024-06-01" in path.name:
            return "Diluted EPS 20.00\nNet Profit 2.00\nAverage net worth 300.00"
        return "Diluted EPS 10.00\nNet Profit 1.00\nAverage net worth 200.00"

    monkeypatch.setattr(fundamentals_module, "extract_text_from_pdf", fake_text)
    output_path = tmp_path / "fundamentals.parquet"

    first = parse_filings(index_path, settings, config, output_path)
    second = parse_filings(index_path, settings, config, output_path)
    frame = pd.read_parquet(output_path)

    assert first.files_read == 2
    assert first.rows_added == 6
    assert first.missing_files == 0
    assert first.unparsed_files == 0
    assert second.rows_added == 0
    assert second.rows_already_present == 6
    assert len(frame) == 6

    before = select_fundamentals_as_of(frame, "AAA", date(2024, 3, 1)).set_index("field")["value"]
    after = select_fundamentals_as_of(frame, "AAA", date(2024, 7, 1)).set_index("field")["value"]
    assert before["trailing_12m_diluted_eps"] == pytest.approx(10.0)
    assert after["trailing_12m_diluted_eps"] == pytest.approx(20.0)
    assert before["trailing_12m_net_profit"] == pytest.approx(10_000_000.0)
    assert after["trailing_12m_net_profit"] == pytest.approx(20_000_000.0)
    assert all(
        pd.to_datetime(
            select_fundamentals_as_of(frame, "AAA", date(2024, 3, 1))["announcement_date"]
        ).dt.date
        <= date(2024, 3, 1)
    )


def test_parse_filings_reports_missing_and_unparsed_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unavailable and unparseable filings are reported, not silently dropped."""
    settings = _settings(tmp_path)
    index_path = tmp_path / "announcement_index.parquet"
    pd.DataFrame(
        [
            {
                "symbol": "AAA",
                "announcement_date": date(2024, 1, 2),
                "headline": "Missing",
                "pdf_url": "https://pdf.test/missing.pdf",
                "source_url": "https://api.test/missing",
                "ingested_at": "2024-01-02T00:00:00+00:00",
            },
            {
                "symbol": "BBB",
                "announcement_date": date(2024, 1, 3),
                "headline": "No fields",
                "pdf_url": "https://pdf.test/blank.pdf",
                "source_url": "https://api.test/blank",
                "ingested_at": "2024-01-03T00:00:00+00:00",
            },
        ]
    ).to_parquet(index_path, index=False)
    _write_filing(settings, "BBB", date(2024, 1, 3), "https://pdf.test/blank.pdf")
    monkeypatch.setattr(fundamentals_module, "extract_text_from_pdf", lambda path: "no fields here")

    summary = parse_filings(
        index_path,
        settings,
        _fundamentals(),
        tmp_path / "fundamentals.parquet",
    )

    assert summary.files_read == 1
    assert summary.rows_added == 0
    assert summary.missing_files == 1
    assert summary.unparsed_files == 1


def test_extract_text_from_pdf_accepts_a_real_pdf(tmp_path: Path) -> None:
    """The parser can open a valid PDF even when it contains no text."""
    path = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with path.open("wb") as handle:
        writer.write(handle)

    assert extract_text_from_pdf(path) == ""
