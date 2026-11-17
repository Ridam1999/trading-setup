"""Phase 6 broker safety, guard, and idempotency tests."""

from pathlib import Path

import pytest

from nse_engine.broker.base import LiveTradingDisabled, Side
from nse_engine.broker.config import BrokerConfig, load_broker_config
from nse_engine.broker.fake import FakeBroker
from nse_engine.broker.groww import GrowwBroker
from nse_engine.broker.paper_broker import PaperBroker
from nse_engine.broker.ratelimit import TokenBucket
from nse_engine.paper.ledger import PaperLedger


def _config() -> BrokerConfig:
    """Load the checked-in broker configuration."""
    return load_broker_config(Path("config"))


def test_broker_config_defaults_to_dry_run() -> None:
    """The checked-in config never enables the live path."""
    config = _config()
    assert config.dry_run is True
    assert config.global_kill_switch is False


def test_token_bucket_enforces_configured_burst() -> None:
    """A bucket refuses calls beyond its configured burst."""
    clock = [0.0]
    bucket = TokenBucket(capacity=2, refill_per_second=1, clock=lambda: clock[0])
    assert bucket.allow() is True
    assert bucket.allow() is True
    assert bucket.allow() is False
    clock[0] = 1.0
    assert bucket.allow() is True


def test_groww_order_methods_always_raise() -> None:
    """No Groww order or query method reaches a live endpoint."""
    broker = GrowwBroker(_config())
    with pytest.raises(LiveTradingDisabled):
        broker.place_limit_order("AAA", Side.BUY, 1, 100.0, "ref-1")
    with pytest.raises(LiveTradingDisabled):
        broker.cancel_order("1")
    with pytest.raises(LiveTradingDisabled):
        broker.get_order("1")
    with pytest.raises(LiveTradingDisabled):
        broker.positions()
    with pytest.raises(LiveTradingDisabled):
        broker.holdings()
    with pytest.raises(LiveTradingDisabled):
        broker.ltp(["AAA"])


def test_groww_stays_disabled_even_with_live_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting the live environment flags cannot enable the disabled skeleton."""
    config = _config().model_copy(update={"dry_run": False})
    monkeypatch.setenv(config.live_confirm_env, "yes")
    assert config.live_enabled is True
    with pytest.raises(LiveTradingDisabled):
        GrowwBroker(config).place_limit_order("AAA", Side.BUY, 1, 100.0, "ref-2")


def test_fake_broker_is_idempotent_by_client_ref() -> None:
    """Repeating a client reference returns the same acknowledgement."""
    broker = FakeBroker(_config())
    first = broker.place_limit_order("AAA", Side.BUY, 1, 100.0, "ref-3")
    second = broker.place_limit_order("AAA", Side.BUY, 1, 100.0, "ref-3")
    assert first == second
    assert len(broker.positions()) == 1


def test_guards_enforce_kill_switch_and_size_cap() -> None:
    """Configured guards reject orders before any simulated fill."""
    config = _config()
    kill_config = config.model_copy(update={"global_kill_switch": True})
    with pytest.raises(LiveTradingDisabled):
        FakeBroker(kill_config).place_limit_order("AAA", Side.BUY, 1, 100.0, "ref-4")
    small_cap = config.model_copy(update={"max_order_size_rupees": 50.0})
    with pytest.raises(ValueError, match="exceeds max"):
        FakeBroker(small_cap).place_limit_order("AAA", Side.BUY, 1, 100.0, "ref-5")


def test_paper_broker_reconciles_ledger_and_simulator(tmp_path: Path) -> None:
    """Paper broker reports mismatches between its simulator and the ledger."""
    ledger = PaperLedger(tmp_path / "paper.sqlite")
    broker = PaperBroker(_config(), ledger, {"AAA": 101.0})
    assert broker.reconcile().consistent is True
    broker.place_limit_order("AAA", Side.BUY, 1, 100.0, "ref-6")
    result = broker.reconcile()
    assert result.broker_symbols == ["AAA"]
    assert result.ledger_symbols == []
    assert result.consistent is False
    assert broker.ltp(["AAA"]) == {"AAA": 101.0}
