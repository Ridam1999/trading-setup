"""Parse legacy NSE bhavcopy and UDiFF daily files from a local download."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pandas as pd  # type: ignore[import-untyped]
from tenacity import retry, stop_after_attempt, wait_exponential

from nse_engine.data.sources.base import normalize_ohlcv


class BhavcopySource:
    """Read one CSV or ZIP containing a legacy or UDiFF cash bhavcopy."""

    def load(self, path: str | Path) -> pd.DataFrame:
        """Detect common legacy and UDiFF headers and retain EQ-series rows."""
        input_path = Path(path)
        if input_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(input_path) as archive:
                csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
                if len(csv_names) != 1:
                    raise ValueError("Bhavcopy ZIP must contain exactly one CSV file")
                raw = archive.read(csv_names[0])
                frame = pd.read_csv(io.BytesIO(raw), sep=None, engine="python")
        else:
            frame = pd.read_csv(input_path, sep=None, engine="python")
        series_col = next(
            (column for column in frame if str(column).strip().lower() in {"series", "sctysrs"}),
            None,
        )
        if series_col is not None:
            frame = frame.loc[frame[series_col].astype("string").str.strip().str.upper() == "EQ"]
        return normalize_ohlcv(frame, source="nse_bhavcopy_raw")


def download_bhavcopy(
    url: str,
    destination: Path,
    attempts: int,
    wait_seconds: float,
    timeout_seconds: float,
) -> Path:
    """Download a configured daily archive with retry/backoff and atomic replacement."""
    if not url:
        raise ValueError("A bhavcopy URL is required")
    if attempts < 1 or wait_seconds <= 0 or timeout_seconds <= 0:
        raise ValueError("Download retry and timeout settings must be positive")

    @retry(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=wait_seconds, min=wait_seconds, max=wait_seconds * 16),
        reraise=True,
    )
    def fetch() -> bytes:
        """Fetch a non-empty response body."""
        response = httpx.get(url, follow_redirects=True, timeout=timeout_seconds)
        response.raise_for_status()
        if not response.content:
            raise ValueError("Bhavcopy response was empty")
        return response.content

    content = fetch()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(destination)
    return destination
