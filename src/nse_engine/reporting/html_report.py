"""Standalone Plotly HTML reports with a fixed disclaimer and bias banner."""

from __future__ import annotations

import html
from pathlib import Path

import plotly.graph_objects as go  # type: ignore[import-untyped]

DISCLAIMER = "Research output, not advice; no guarantee of profit."


def write_chart_report(
    figures: list[tuple[str, go.Figure]],
    path: Path,
    data_bias_status: str,
    title: str = "NSE research charts",
) -> Path:
    """Write a self-contained HTML page of Plotly figures with required banners."""
    if not figures:
        raise ValueError("A chart report needs at least one figure")
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    for name, figure in figures:
        sections.append(f"<h2>{html.escape(name)}</h2>{figure.to_html(full_html=False)}")
    bias = html.escape(data_bias_status.replace("_", " ").lower())
    document = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title></head><body>"
        f"<h1>{html.escape(title)}</h1>"
        f"<p style='font-weight:bold'>Data-bias status: {bias}</p>"
        f"<p>{html.escape(DISCLAIMER)}</p>"
        + "".join(sections)
        + f"<p>{html.escape(DISCLAIMER)}</p></body></html>"
    )
    path.write_text(document, encoding="utf-8")
    return path
