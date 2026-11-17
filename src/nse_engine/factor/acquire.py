"""Local acquisition bot for point-in-time NSE inputs.

All endpoints, dates, delays, retries and exclusions come from YAML. The bot
never places orders, never uses credentials, and never overwrites an existing
dated snapshot. Tests inject an HTTP transport so no network is touched.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd  # type: ignore[import-untyped]

from nse_engine.data.sources.nselib_source import BhavcopySource
from nse_engine.factor.config import AcquisitionConfig, FactorConfig

DISCLAIMER = "Research output, not advice; no guarantee of profit."
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; nse-engine-research/1.0)",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


@dataclass(frozen=True)
class PriceDownloadSummary:
    """Outcome of a dated bhavcopy acquisition run."""

    downloaded: tuple[date, ...]
    skipped: tuple[date, ...]
    preserved: tuple[date, ...]


@dataclass(frozen=True)
class AnnouncementDownloadSummary:
    """Outcome of an announcement metadata acquisition run."""

    chunks: int
    rows: int
    output_path: Path


def legacy_filename(day: date) -> str:
    """Return the legacy NSE bhavcopy archive filename for one date."""
    return f"cm{day.strftime('%d%b%Y').upper()}bhav.csv.zip"


def bhavcopy_url(day: date, settings: AcquisitionConfig) -> str:
    """Build the configured URL for one dated bhavcopy."""
    if day >= settings.udiff_start_date:
        return settings.udiff_url_template.format(compact_date=day.strftime("%d%m%Y"))
    return settings.legacy_url_template.format(
        year=day.year,
        month=day.strftime("%b").upper(),
        filename=legacy_filename(day),
    )


def bhavcopy_destination(directory: Path, day: date, settings: AcquisitionConfig) -> Path:
    """Return the immutable local path for one dated bhavcopy."""
    suffix = ".csv" if day >= settings.udiff_start_date else ".zip"
    return directory / f"year={day.year}" / f"date={day.isoformat()}" / f"bhavcopy{suffix}"


def filing_path(settings: AcquisitionConfig, symbol: str, announced: date, url: str) -> Path:
    """Return the deterministic local path for one linked filing PDF."""
    label = pd.to_datetime(announced).strftime("%Y-%m-%d")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return settings.filing_dir / symbol / f"{label}_{digest}.pdf"


def _session(settings: AcquisitionConfig) -> httpx.Client:
    """Open a client with NSE cookies established by the configured session URL."""
    client = httpx.Client(headers=HEADERS, timeout=settings.timeout_seconds, follow_redirects=True)
    try:
        response = client.get(settings.session_url)
        response.raise_for_status()
    except Exception:
        client.close()
        raise
    return client


def _fetch(client: httpx.Client, url: str, settings: AcquisitionConfig) -> bytes | None:
    """Fetch one URL with configured retries; return None for a missing date."""
    for attempt in range(settings.retry_attempts):
        try:
            response = client.get(url)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            if not response.content:
                raise ValueError(f"Empty response from {url}")
            return response.content
        except (httpx.HTTPError, ValueError):
            if attempt + 1 == settings.retry_attempts:
                raise
            time.sleep(settings.request_delay_seconds * 2**attempt)
    return None


def _write_atomic(path: Path, content: bytes) -> None:
    """Write bytes to a temporary path, then replace the destination atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def download_bhavcopies(
    settings: AcquisitionConfig,
    destination: Path,
    client: httpx.Client | None = None,
) -> PriceDownloadSummary:
    """Download missing dated bhavcopies from the configured public archive."""
    owned_client = client is None
    session = client or _session(settings)
    downloaded: list[date] = []
    skipped: list[date] = []
    preserved: list[date] = []
    try:
        day = settings.start_date
        while day <= settings.end_date:
            if day.weekday() in settings.weekdays:
                path = bhavcopy_destination(destination, day, settings)
                if path.is_file() and path.stat().st_size > 0:
                    preserved.append(day)
                else:
                    content = _fetch(session, bhavcopy_url(day, settings), settings)
                    if content is None:
                        skipped.append(day)
                    else:
                        _write_atomic(path, content)
                        downloaded.append(day)
                    time.sleep(settings.request_delay_seconds)
            day += timedelta(days=1)
    finally:
        if owned_client:
            session.close()
    return PriceDownloadSummary(tuple(downloaded), tuple(skipped), tuple(preserved))


def load_pit_bars(directory: Path, exclude_pattern: str | None = None) -> pd.DataFrame:
    """Load every dated bhavcopy snapshot already stored on disk."""
    paths = sorted(directory.glob("year=*/date=*/bhavcopy.*"))
    if not paths:
        return pd.DataFrame()
    source = BhavcopySource()
    frames = [source.load(path) for path in paths if not path.name.endswith(".tmp")]
    if not frames:
        return pd.DataFrame()
    bars = pd.concat(frames, ignore_index=True)
    if exclude_pattern:
        symbols = bars["symbol"].astype("string")
        bars = bars.loc[~symbols.str.contains(exclude_pattern, regex=True, na=False)].copy()
    return bars.sort_values(["date", "symbol"], ignore_index=True)


def build_membership(
    factor: FactorConfig,
    settings: AcquisitionConfig,
    price_dir: Path,
    output_path: Path,
) -> Path:
    """Write PIT top-N membership snapshots from dated price files only."""
    bars = load_pit_bars(price_dir, settings.exclude_symbol_pattern)
    if bars.empty:
        raise ValueError(f"No dated bhavcopy files found in {price_dir}")

    bar_dates = pd.to_datetime(bars["date"]).dt.date
    dates = sorted(set(bar_dates))
    month_ends: list[date] = []
    for year, month in sorted({(day.year, day.month) for day in dates}):
        candidates = [day for day in dates if day.year == year and day.month == month]
        month_ends.append(candidates[-1])

    rows: list[dict[str, Any]] = []
    size = factor.universe.size
    lookback = factor.universe.liquidity_lookback_sessions
    for rebalance_date in month_ends:
        eligible_dates = [day for day in dates if day <= rebalance_date][-lookback:]
        window = bars.loc[bar_dates.isin(eligible_dates)].copy()
        window["traded_value"] = pd.to_numeric(window["close"]) * pd.to_numeric(window["volume"])
        scores = window.groupby("symbol", sort=True)["traded_value"].mean().dropna()
        for rank, (symbol, value) in enumerate(scores.nlargest(size).items(), start=1):
            rows.append(
                {
                    "rebalance_date": rebalance_date,
                    "symbol": symbol,
                    "rank": rank,
                    "traded_value": float(value),
                    "index_name": "pit_traded_value_top_n",
                    "membership_type": "point_in_time_delisted_inclusive",
                }
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame.from_records(rows).to_parquet(output_path, index=False)
    return output_path


def extract_announcement_rows(payload: Any, source_url: str) -> list[dict[str, Any]]:
    """Normalize a public announcement API payload into metadata rows."""
    if not isinstance(payload, dict):
        return []
    records = payload.get("data") or payload.get("announcements") or []
    if not isinstance(records, list):
        return []
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        symbol = record.get("symbol") or record.get("symbolName")
        announced = record.get("announcementDate") or record.get("date") or record.get("neapsDate")
        if not symbol or not announced:
            continue
        parsed = pd.to_datetime(announced, errors="coerce")
        if pd.isna(parsed):
            continue
        rows.append(
            {
                "symbol": str(symbol).strip().upper(),
                "announcement_date": parsed.date(),
                "headline": record.get("subject") or record.get("headline") or record.get("desc"),
                "pdf_url": record.get("pdfUrl")
                or record.get("attachmentUrl")
                or record.get("fileUrl"),
                "source_url": source_url,
                "ingested_at": datetime.now(UTC).isoformat(),
            }
        )
    return rows


def download_announcements(
    settings: AcquisitionConfig,
    output_path: Path,
    client: httpx.Client | None = None,
) -> AnnouncementDownloadSummary:
    """Download announcement metadata in date chunks and write a Parquet index."""
    owned_client = client is None
    session = client or _session(settings)
    all_rows: list[dict[str, Any]] = []
    chunk_count = 0
    try:
        start = settings.start_date
        while start <= settings.end_date:
            end = min(
                start + timedelta(days=settings.announcement_chunk_days - 1), settings.end_date
            )
            url = settings.announcement_url_template.format(
                start=start.strftime("%d-%m-%Y"), end=end.strftime("%d-%m-%Y")
            )
            content = _fetch(session, url, settings)
            if content is not None:
                settings.announcement_raw_dir.mkdir(parents=True, exist_ok=True)
                (
                    settings.announcement_raw_dir / f"{start.isoformat()}_{end.isoformat()}.json"
                ).write_bytes(content)
                all_rows.extend(extract_announcement_rows(json.loads(content), url))
            chunk_count += 1
            start = end + timedelta(days=1)
            time.sleep(settings.request_delay_seconds)
    finally:
        if owned_client:
            session.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame.from_records(
        all_rows,
        columns=[
            "symbol",
            "announcement_date",
            "headline",
            "pdf_url",
            "source_url",
            "ingested_at",
        ],
    ).to_parquet(output_path, index=False)
    return AnnouncementDownloadSummary(chunk_count, len(all_rows), output_path)


def download_filing_pdfs(
    announcements_path: Path,
    settings: AcquisitionConfig,
    client: httpx.Client | None = None,
) -> int:
    """Download linked filing PDFs without overwriting existing copies."""
    if not announcements_path.is_file():
        raise FileNotFoundError(f"Announcement index does not exist: {announcements_path}")
    announcements = pd.read_parquet(announcements_path)
    if announcements.empty or "pdf_url" not in announcements.columns:
        return 0
    owned_client = client is None
    session = client or _session(settings)
    saved = 0
    try:
        for row in announcements.itertuples(index=False):
            url = getattr(row, "pdf_url", None)
            if not isinstance(url, str) or not url:
                continue
            symbol = str(getattr(row, "symbol", "UNKNOWN"))
            announced = pd.to_datetime(row.announcement_date).date()
            path = filing_path(settings, symbol, announced, url)
            if path.is_file() and path.stat().st_size > 0:
                continue
            content = _fetch(session, url, settings)
            if content is None:
                continue
            _write_atomic(path, content)
            saved += 1
            time.sleep(settings.request_delay_seconds)
    finally:
        if owned_client:
            session.close()
    return saved
