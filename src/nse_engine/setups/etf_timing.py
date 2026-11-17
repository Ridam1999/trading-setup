"""Short-term index ETF timing setup for liquid funds with long history."""

from nse_engine.setups.base import SetupConfig
from nse_engine.setups.index_dip import IndexDip


class ETFTiming(IndexDip):
    """Reuse the close-only index dip rule with an ETF-only universe."""

    name = "etf_timing"

    def __init__(self, config: SetupConfig) -> None:
        """Store ETF-specific parameters using the shared timing rule."""
        super().__init__(config)
