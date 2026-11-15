"""Configure consistent application logging."""

from __future__ import annotations

import logging
import os


def configure_logging(level: str | None = None) -> None:
    """Configure standard logging with an environment-overridable level."""
    selected_level = (level or os.environ.get("NSE_ENGINE_LOG_LEVEL") or "INFO").upper()
    numeric_level = getattr(logging, selected_level, None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Invalid log level: {selected_level}")
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
