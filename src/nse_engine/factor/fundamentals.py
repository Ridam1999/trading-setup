"""Restatement-safe parser for announcement-dated fundamental values.

The parser only reads files already downloaded by the acquisition bot. It
appends new rows, never rewrites prior values, and reports PDFs it cannot
parse. Selection helpers use only rows available at or before a cutoff date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
from pypdf import PdfReader

from nse_engine.factor.acquire import filing_path
from nse_engine.factor.config import AcquisitionConfig, FundamentalExtractionConfig

DISCLAIMER = "Research output, not advice; no guarantee of profit."
FUNDAMENTAL_COLUMNS: tuple[str, ...] = (
    "symbol",
    "announcement_date",
    "fiscal_period",
    "field",
    "value",
    "source_url",
    "filing_path",
    "ingested_at",
)
_NUMBER_PATTERN = r"[-+]?\d[\d,]*\.?\d*"


@dataclass(frozen=True)
class ParsingSummary:
    """Outcome of one fundamentals parsing run."""

    files_read: int
    rows_added: int
    rows_already_present: int
    missing_files: int
    unparsed_files: int
    output_path: Path


def extract_text_from_pdf(path: Path) -> str:
    """Extract all page text from one already downloaded filing PDF."""
    if not path.is_file():
        raise FileNotFoundError(f"Filing PDF does not exist: {path}")
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_numeric_field(
    text: str, labels: list[str], multiplier: float, max_gap: int
) -> float | None:
    """Return the first configured numeric value following a label pattern."""
    for label in labels:
        pattern = re.compile(rf"{label}[^\n\d]{{0,{max_gap}}}({_NUMBER_PATTERN})", re.IGNORECASE)
        match = pattern.search(text)
        if match is not None:
            return float(match.group(1).replace(",", "")) * multiplier
    return None


def _record_key(record: dict[str, Any]) -> tuple[str, ...]:
    """Build an idempotency key that is stable across Parquet round-trips."""
    announced = pd.to_datetime(record["announcement_date"]).date()
    return (
        str(record["symbol"]).strip().upper(),
        announced.isoformat(),
        str(record["field"]),
        repr(float(record["value"])),
        str(record["source_url"]),
        str(record["filing_path"]),
    )


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    """Write a Parquet frame to a temporary path, then replace the destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    temporary.replace(path)


def parse_filings(
    announcement_index_path: Path,
    settings: AcquisitionConfig,
    fundamentals: FundamentalExtractionConfig,
    output_path: Path,
) -> ParsingSummary:
    """Append newly parsed fundamentals from downloaded filing PDFs."""
    if not announcement_index_path.is_file():
        raise FileNotFoundError(f"Announcement index does not exist: {announcement_index_path}")
    announcements = pd.read_parquet(announcement_index_path)
    missing = {"symbol", "announcement_date", "pdf_url"}.difference(announcements.columns)
    if missing:
        raise ValueError(f"Announcement index is missing columns: {', '.join(sorted(missing))}")
    announcements = announcements.sort_values(
        ["symbol", "announcement_date", "pdf_url"], ignore_index=True
    )

    existing = pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)
    existing_keys: set[tuple[str, ...]] = set()
    if output_path.is_file():
        existing = pd.read_parquet(output_path)
        existing_keys = {_record_key(row) for row in existing.to_dict("records")}

    rows: list[dict[str, Any]] = []
    seen_keys = set(existing_keys)
    files_read = 0
    skipped_rows = 0
    missing_files = 0
    unparsed_files = 0

    for row in announcements.to_dict("records"):
        url = row.get("pdf_url")
        if not isinstance(url, str) or not url:
            continue
        symbol = str(row["symbol"]).strip().upper()
        announced = pd.to_datetime(row["announcement_date"]).date()
        path = filing_path(settings, symbol, announced, url)
        if not path.is_file():
            missing_files += 1
            continue
        files_read += 1
        text = extract_text_from_pdf(path)
        found = 0
        for field_name, field_config in fundamentals.fields.items():
            multiplier = fundamentals.crore_multiplier if field_config.unit == "crore" else 1.0
            value = extract_numeric_field(
                text,
                field_config.labels,
                multiplier,
                fundamentals.label_value_max_chars,
            )
            if value is None:
                continue
            record = {
                "symbol": symbol,
                "announcement_date": announced,
                "fiscal_period": None,
                "field": field_name,
                "value": float(value),
                "source_url": row.get("source_url"),
                "filing_path": str(path),
                "ingested_at": datetime.now(UTC).isoformat(),
            }
            key = _record_key(record)
            if key in seen_keys:
                skipped_rows += 1
                continue
            seen_keys.add(key)
            rows.append(record)
            found += 1
        if found == 0:
            unparsed_files += 1

    parsed = pd.DataFrame.from_records(rows, columns=FUNDAMENTAL_COLUMNS)
    combined = pd.concat([existing, parsed], ignore_index=True) if not existing.empty else parsed
    _write_parquet_atomic(combined, output_path)
    return ParsingSummary(
        files_read=files_read,
        rows_added=len(rows),
        rows_already_present=skipped_rows,
        missing_files=missing_files,
        unparsed_files=unparsed_files,
        output_path=output_path,
    )


def select_fundamentals_as_of(frame: pd.DataFrame, symbol: str, as_of_date: date) -> pd.DataFrame:
    """Return one latest announced value per field using no future rows.

    Callers must apply the configured session-based announcement lag to
    ``as_of_date`` before calling this function.
    """
    if frame.empty:
        return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)
    eligible = frame.loc[
        (frame["symbol"].astype("string").str.strip().str.upper() == symbol.strip().upper())
        & (pd.to_datetime(frame["announcement_date"]).dt.date <= as_of_date)
    ].copy()
    if eligible.empty:
        return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)
    eligible["_announced"] = pd.to_datetime(eligible["announcement_date"]).dt.date
    eligible = eligible.sort_values(["_announced", "ingested_at"])
    return (
        eligible.drop_duplicates("field", keep="last")
        .drop(columns="_announced")
        .reset_index(drop=True)
    )
