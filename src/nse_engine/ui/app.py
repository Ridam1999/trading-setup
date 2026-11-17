"""Streamlit application entry point for the Phase 5 dashboard."""

from __future__ import annotations

from collections.abc import Callable

from nse_engine.ui import common
from nse_engine.ui.pages import (
    backtest,
    data_health,
    event_study_results,
    paper_ledger,
    setup_explorer,
    trial_registry,
    walk_forward,
)

PAGE_RENDERERS: dict[str, Callable[[], None]] = {
    "Data health": data_health.render,
    "Setup explorer": setup_explorer.render,
    "Event-study results": event_study_results.render,
    "Backtest": backtest.render,
    "Walk-forward": walk_forward.render,
    "Paper ledger": paper_ledger.render,
    "Trial registry": trial_registry.render,
}


def main() -> None:
    """Dispatch to the selected read-only page."""
    page = common.st.sidebar.radio("Page", list(PAGE_RENDERERS))
    PAGE_RENDERERS[page]()


if __name__ == "__main__":
    main()
