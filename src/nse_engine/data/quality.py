"""Deterministic quality checks for daily OHLCV and coverage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

DISCLAIMER = "Research output, not advice; no guarantee of profit."


@dataclass(frozen=True)
class QualityReport:
    """Quality-check findings, annual coverage, and critical failure count."""

    findings: pd.DataFrame
    coverage: pd.DataFrame
    critical_failures: int


def check_ohlcv(
    frame: pd.DataFrame,
    calendar: pd.DataFrame,
    maximum_return_pct: float,
    stale_close_days: int,
    maximum_adjusted_raw_difference_pct: float | None = None,
    minimum_analysis_date: date | None = None,
) -> QualityReport:
    """Run structural, completeness, outlier, stale-close, and coverage checks."""
    if maximum_return_pct <= 0 or stale_close_days < 1:
        raise ValueError("Quality thresholds must be positive")
    findings: list[dict[str, object]] = []

    def record(check: str, severity: str, symbol: str, day: object, detail: str) -> None:
        """Append one reproducible issue row."""
        findings.append(
            {"check": check, "severity": severity, "symbol": symbol, "date": day, "detail": detail}
        )

    if frame.empty:
        record("empty_dataset", "critical", "", None, "No OHLCV observations")
        return QualityReport(pd.DataFrame(findings), pd.DataFrame(), 1)

    duplicates = frame.loc[frame.duplicated(["symbol", "date"], keep=False)]
    for row in duplicates.itertuples(index=False):
        record("duplicate_row", "critical", str(row.symbol), row.date, "Duplicate symbol/date key")

    invalid_range = frame.loc[
        (frame["high"] < frame["low"])
        | (frame["close"] < frame["low"])
        | (frame["close"] > frame["high"])
        | (frame["open"] < frame["low"])
        | (frame["open"] > frame["high"])
    ]
    for row in invalid_range.itertuples(index=False):
        record(
            "invalid_ohlc_range",
            "critical",
            str(row.symbol),
            row.date,
            "OHLC violates high/low range",
        )

    nonpositive = frame.loc[(frame[["open", "high", "low", "close"]] <= 0).any(axis=1)]
    for row in nonpositive.itertuples(index=False):
        record(
            "nonpositive_price", "critical", str(row.symbol), row.date, "Price is zero or negative"
        )

    zero_volume = frame.loc[frame["volume"] <= 0]
    for row in zero_volume.itertuples(index=False):
        record("zero_volume", "critical", str(row.symbol), row.date, "Volume is zero or negative")

    if maximum_adjusted_raw_difference_pct is not None:
        raw_close = pd.to_numeric(frame["close_raw"], errors="coerce")
        adjusted_close = pd.to_numeric(frame["close"], errors="coerce")
        has_both = raw_close.notna() & adjusted_close.notna() & raw_close.ne(0)
        difference_pct = (adjusted_close - raw_close).abs().div(raw_close).mul(100)
        for row in frame.loc[
            has_both & (difference_pct > maximum_adjusted_raw_difference_pct)
        ].itertuples(index=False):
            record(
                "adjusted_raw_difference",
                "warning",
                str(row.symbol),
                row.date,
                "Adjusted and raw closes diverge; review corporate-action history",
            )

    if minimum_analysis_date is not None:
        for row in frame.loc[frame["date"] < minimum_analysis_date].itertuples(index=False):
            record(
                "pre_analysis_history",
                "warning",
                str(row.symbol),
                row.date,
                f"History before {minimum_analysis_date} is lower quality",
            )

    trading_dates = set(calendar.loc[calendar["is_trading_day"], "date"])
    for symbol, group in frame.sort_values("date").groupby("symbol", sort=True):
        observed_dates = set(group["date"])
        expected_dates = {
            day for day in trading_dates if min(observed_dates) <= day <= max(observed_dates)
        }
        for missing_day in sorted(expected_dates - observed_dates):
            record(
                "missing_trading_day",
                "warning",
                str(symbol),
                missing_day,
                "No row on observed market date",
            )
        pct_returns = group["close"].pct_change().abs().mul(100)
        large_moves = group.loc[pct_returns > maximum_return_pct]
        for row in large_moves.itertuples(index=False):
            record(
                "large_single_day_return",
                "warning",
                str(symbol),
                row.date,
                "Review possible unadjusted action",
            )
        stale = (
            group["close"]
            .eq(group["close"].shift())
            .rolling(stale_close_days)
            .sum()
            .ge(stale_close_days)
        )
        for row in group.loc[stale].itertuples(index=False):
            record(
                "stale_repeated_close",
                "warning",
                str(symbol),
                row.date,
                "Repeated close across configured window",
            )

    coverage = (
        frame.assign(year=pd.to_datetime(frame["date"]).dt.year)
        .groupby("year")
        .agg(symbols=("symbol", "nunique"), observations=("symbol", "size"))
        .reset_index()
    )
    findings_frame = pd.DataFrame(
        findings, columns=["check", "severity", "symbol", "date", "detail"]
    )
    critical = (
        int(findings_frame["severity"].eq("critical").sum()) if not findings_frame.empty else 0
    )
    return QualityReport(findings_frame, coverage, critical)


def write_health_report(report: QualityReport, path: Path, survivorship_biased: bool) -> None:
    """Write a standalone HTML report with the project disclaimer and bias status."""
    path.parent.mkdir(parents=True, exist_ok=True)
    status = (
        "Static universe; survivorship bias likely"
        if survivorship_biased
        else "Point-in-time universe"
    )
    findings_html = report.findings.to_html(index=False, border=0, escape=True)
    coverage_html = report.coverage.to_html(index=False, border=0, escape=True)
    page = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Data health</title>"
        "<style>body{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}"
        "table{border-collapse:collapse;width:100%}"
        "th,td{border:1px solid #ddd;padding:.4rem;text-align:left}"
        "th{background:#f3f3f3}</style></head><body><h1>Data health</h1>"
        f"<p>{DISCLAIMER}</p><p>Survivorship-bias status: {status}</p>"
        f"<p>Critical failures: {report.critical_failures}</p><h2>Findings</h2>{findings_html}"
        f"<h2>Annual coverage</h2>{coverage_html}</body></html>"
    )
    path.write_text(page, encoding="utf-8")
