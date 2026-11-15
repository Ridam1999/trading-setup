"""Explicit mapping from setup names to their registered classes."""

from nse_engine.setups.base import Setup, SetupConfig
from nse_engine.setups.breakout_52w import Breakout52Week
from nse_engine.setups.index_dip import IndexDip
from nse_engine.setups.pullback_uptrend import PullbackUptrend
from nse_engine.setups.vol_contraction import VolatilityContraction

SETUP_REGISTRY: dict[str, type[Setup]] = {
    "pullback_uptrend": PullbackUptrend,
    "breakout_52w": Breakout52Week,
    "vol_contraction": VolatilityContraction,
    "index_dip": IndexDip,
}


def create_setup(config: SetupConfig) -> Setup:
    """Construct a setup using only the explicit class registry."""
    try:
        setup_type = SETUP_REGISTRY[config.name]
    except KeyError as error:
        raise ValueError(f"Unknown setup: {config.name}") from error
    return setup_type(config)
