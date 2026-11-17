"""Deterministic random-signal negative control for event studies."""

from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.setups.base import SetupConfig, parameter_number


class RandomSignal:
    """Emit seeded pseudo-random signals independent of all price history."""

    name = "random_signal"

    def __init__(self, config: SetupConfig) -> None:
        """Validate the configured seed and per-bar signal probability."""
        self.config = config
        seed = config.params.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("random_signal parameter 'seed' must be an integer")
        self.seed = seed
        self.probability = parameter_number(config, "signal_probability")
        if not 0 <= self.probability <= 1:
            raise ValueError("random_signal probability must be between 0 and 1")

    def required_features(self) -> list[str]:
        """List the identity columns used to derive a deterministic draw."""
        return ["symbol", "date"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Return one seeded draw per bar, true when below the configured probability."""
        draws = [
            self._draw(str(symbol), day)
            for symbol, day in zip(frame["symbol"], frame["date"], strict=True)
        ]
        return pd.Series(draws, index=frame.index, dtype=bool)

    def _draw(self, symbol: str, day: object) -> bool:
        """Hash a symbol/session identity into a stable uniform draw."""
        session = day.isoformat() if isinstance(day, date) else str(day)
        identity = f"{self.seed}|{symbol}|{session}".encode()
        digest = hashlib.blake2b(identity, digest_size=8).digest()
        return int.from_bytes(digest, "big") / 2**64 < self.probability
