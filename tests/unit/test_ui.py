"""Phase 5 Streamlit page dispatch and read-only loader tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from nse_engine.ui import app, common
from nse_engine.ui.pages.event_study_results import trial_summary_frame
from nse_engine.ui.pages.trial_registry import registry_frame


class FakeSidebar:
    """Minimal sidebar stub that returns the selected page."""

    def __init__(self, owner: FakeStreamlit) -> None:
        self.owner = owner

    def radio(self, label: str, options: list[str]) -> str:
        self.owner.record("radio", label, options)
        return self.owner.selected_page or options[0]


class FakeStreamlit:
    """Records Streamlit calls without requiring a script run context."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.sidebar = FakeSidebar(self)
        self.selected_page: str | None = None

    def record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.record(name, *args, **kwargs)

        return record

    def selectbox(self, label: str, options: list[Any], **kwargs: Any) -> Any:
        self.record("selectbox", label, options, **kwargs)
        return options[kwargs.get("index", 0)]

    def checkbox(self, label: str, **kwargs: Any) -> bool:
        self.record("checkbox", label, **kwargs)
        return bool(kwargs.get("value", True))

    def slider(self, label: str, **kwargs: Any) -> int:
        self.record("slider", label, **kwargs)
        return int(kwargs.get("value", 1))

    def texts(self) -> list[str]:
        return [
            value
            for _, args, kwargs in self.calls
            for value in (*args, *kwargs.values())
            if isinstance(value, str)
        ]


def patch_empty_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every store loader at empty data so pages exercise no-data paths."""
    monkeypatch.setattr(common, "load_data_health", lambda: pd.DataFrame())
    monkeypatch.setattr(common, "load_ohlcv", lambda: pd.DataFrame())
    monkeypatch.setattr(common, "load_events", lambda: pd.DataFrame())
    monkeypatch.setattr(common, "load_backtest_folders", lambda: [])
    monkeypatch.setattr(
        common,
        "load_ledger_rows",
        lambda: {
            name: pd.DataFrame() for name in ("signals", "orders", "fills", "positions", "ledger")
        },
    )
    monkeypatch.setattr(common, "load_trial_rows", lambda provisional: [])


@pytest.mark.parametrize("page", list(app.PAGE_RENDERERS))
def test_every_page_shows_bias_banner_and_disclaimer(
    monkeypatch: pytest.MonkeyPatch, page: str
) -> None:
    """No page can render without the required status and disclaimer."""
    fake = FakeStreamlit()
    monkeypatch.setattr(common, "st", fake)
    patch_empty_loaders(monkeypatch)

    app.PAGE_RENDERERS[page]()

    texts = fake.texts()
    assert any("Data-bias status: SURVIVOR_BIASED" in text for text in texts)
    assert any(common.DISCLAIMER in text for text in texts)


def test_app_dispatches_to_selected_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sidebar selection selects the matching renderer."""
    fake = FakeStreamlit()
    fake.selected_page = "Walk-forward"
    monkeypatch.setattr(common, "st", fake)
    patch_empty_loaders(monkeypatch)

    app.main()

    assert any("NSE Engine - Walk-forward" in text for text in fake.texts())
    assert any(name == "radio" for name, _, _ in fake.calls)


def test_event_loader_tags_files_and_excludes_invalid_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Saved events carry setup metadata and the cheating control stays hidden."""
    settings = SimpleNamespace(events_dir=tmp_path)
    monkeypatch.setattr(common, "load_study_settings", lambda: settings)
    base = {"symbol": ["AAA"], "net_return": [0.01], "mae": [-0.01], "mfe": [0.02]}
    pd.DataFrame(base).to_parquet(tmp_path / "breakout_52w_train_5_abcd1234.parquet", index=False)
    pd.DataFrame({**base, "symbol": ["BBB"]}).to_parquet(
        tmp_path / "__control_lookahead___train_5_beef5678.parquet", index=False
    )

    frame = common.load_events()
    assert frame["setup"].tolist() == ["breakout_52w"]
    assert frame["split"].tolist() == ["train"]
    assert frame["horizon"].tolist() == [5]
    assert len(common.load_events(include_controls=True)) == 2


def test_display_frames_flatten_saved_metrics() -> None:
    """UI tables read trial metrics without recomputing research statistics."""
    metrics = {
        "net_return": {"n_events": 12, "expectancy": 0.01, "hit_rate": 0.5},
        "excess_over_baseline": {"date_clustered_p": 0.04},
        "yearly": [{"year": 2020, "events": 3, "expectancy": 0.02, "total_profit": 0.06}],
    }
    import json

    row = {
        "trial_id": "t1",
        "timestamp_utc": "2026-11-18T00:00:00Z",
        "setup_name": "breakout_52w",
        "params_hash": "hash",
        "universe": "top100",
        "split": "train",
        "horizon": 5,
        "metrics_json": json.dumps(metrics),
    }
    event_frame = trial_summary_frame([row])
    assert event_frame.loc[0, "expectancy"] == 0.01
    assert event_frame.loc[0, "date_clustered_p"] == 0.04
    registry = registry_frame([row])
    assert registry.loc[0, "setup"] == "breakout_52w"
