"""Standalone HTML report for the frozen factor study."""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nse_engine.factor.study import FactorStudyOutput

DISCLAIMER = "Research output, not advice; no guarantee of profit."
CANDIDATE_BANNER = "FACTOR RESULTS ARE CANDIDATES ONLY; NOT APPROVAL EVIDENCE"


def _cell(value: Any) -> str:
    """Return one HTML-escaped table cell."""
    return f"<td>{html.escape(str(value))}</td>"


def _table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    """Build a small escaped HTML table."""
    header = "".join(f"<th>{html.escape(name)}</th>" for name in headers)
    body = "".join("<tr>" + "".join(_cell(value) for value in row) + "</tr>" for row in rows)
    return f"<table border='1'><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>"


def _format_metric(value: Any) -> str:
    """Format a numeric metric for display without hiding invalid values."""
    if isinstance(value, int | float):
        return f"{value:.4f}"
    return str(value)


def write_factor_report(
    output: FactorStudyOutput,
    path: Path,
    data_bias_status: str,
    frozen_hash: str,
    frozen_ok: bool,
    holdout_accessed: bool,
) -> Path:
    """Write the candidate-only factor report with all required banners."""
    results = output.results
    candidate_count = sum(1 for result in results if result.candidate)
    result_rows = [
        (
            result.split,
            result.hypothesis_id,
            result.hypothesis_name,
            result.horizon_months,
            _format_metric(result.summary.get("expectancy")),
            _format_metric(result.adjusted_p_value),
            _format_metric(result.excess_summary.get("expectancy")),
            _format_metric(result.niftybees_excess.get("expectancy")),
            "PASS" if result.candidate else "FAIL",
            ", ".join(name for name, passed in result.gates.items() if not passed) or "none",
        )
        for result in results
    ]
    yearly_rows = [
        (
            result.split,
            result.hypothesis_id,
            result.horizon_months,
            row.get("year"),
            row.get("events"),
            _format_metric(row.get("expectancy")),
            _format_metric(row.get("total_profit")),
        )
        for result in results
        for row in result.yearly
    ]
    document = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Frozen factor study</title></head><body>"
        "<h1>Frozen cross-sectional factor study</h1>"
        f"<p><strong>{html.escape(CANDIDATE_BANNER)}</strong></p>"
        f"<p><strong>Data-bias status: {html.escape(data_bias_status)}</strong></p>"
        f"<p>Frozen hash: {html.escape(frozen_hash)}; verified: {frozen_ok}</p>"
        f"<p>Holdout accessed: {holdout_accessed}; trials appended: {output.trials_appended}; "
        f"candidates: {candidate_count}</p>"
        f"<p>{html.escape(DISCLAIMER)}</p>"
        "<h2>Results by split, hypothesis, and horizon</h2>"
        + _table(
            (
                "Split",
                "H",
                "Name",
                "Months",
                "Net expectancy",
                "Adjusted p",
                "Excess vs equal-weight universe",
                "Excess vs NIFTYBEES",
                "Candidate",
                "Failed gates",
            ),
            result_rows,
        )
        + "<h2>Yearly results</h2>"
        + _table(
            ("Split", "H", "Months", "Year", "Events", "Expectancy", "Total profit"),
            yearly_rows,
        )
        + "<h2>Approval status</h2>"
        "<p>No setup is approved by this report. A pass is a candidate requiring "
        "point-in-time verification.</p>"
        f"<p>{html.escape(DISCLAIMER)}</p></body></html>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path
