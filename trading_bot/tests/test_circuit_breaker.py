"""Integration tests for circuit breaker."""
from decimal import Decimal

from trading_bot.infrastructure.state_manager import PersistentState
from trading_bot.logging.logger import StructuredLogger
from trading_bot.risk.circuit_breaker import CircuitBreaker
import tempfile
import os


def test_daily_loss_triggers_halt():
    with tempfile.TemporaryDirectory() as tmp:
        state_file = os.path.join(tmp, "test_state.json")
        logger = StructuredLogger(bot_name="test_cb")
        state = PersistentState(state_file, logger)
        state.state["day_start_balance"] = "100000"
        cb = CircuitBreaker(state, logger)
        halted = cb.check_loss_limits(Decimal("94000"))
        assert halted is True
        assert state.is_trading_halted()
