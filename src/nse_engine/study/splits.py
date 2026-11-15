"""Train/validation/holdout split resolution and access locking."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd  # type: ignore[import-untyped]
import yaml
from pydantic import BaseModel, ConfigDict

from nse_engine.config import config_directory
from nse_engine.study.trials import TrialRegistry

SplitName = Literal["train", "validation", "holdout"]


class DateRange(BaseModel):
    """Inclusive date bounds for a named experiment split."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date | Literal["latest"]


def configured_split(split: SplitName, config_dir: Path | None = None) -> DateRange:
    """Read configured split bounds without accessing price data."""
    path = (config_dir or config_directory()) / "splits.yaml"
    with path.open(encoding="utf-8") as split_file:
        document = yaml.safe_load(split_file)
    if not isinstance(document, dict) or split not in document:
        raise ValueError(f"Missing {split} range in {path}")
    return DateRange.model_validate(document[split])


def resolve_split(
    frame: pd.DataFrame,
    split: SplitName,
    unlock_holdout: bool,
    registry: TrialRegistry,
    config_dir: Path | None = None,
    access_already_logged: bool = False,
) -> tuple[date, date]:
    """Resolve date bounds and audit explicit holdout access."""
    if split == "holdout" and not unlock_holdout:
        raise PermissionError("holdout is locked; pass --unlock-holdout to record and access it")
    if split == "holdout" and not access_already_logged:
        registry.record_holdout_unlock()
    bounds = configured_split(split, config_dir)
    latest = max(frame["date"])
    if bounds.end == "latest":
        end = latest
    else:
        end = bounds.end
    if bounds.start > end:
        raise ValueError(f"Invalid {split} date range: start is after end")
    return bounds.start, end
