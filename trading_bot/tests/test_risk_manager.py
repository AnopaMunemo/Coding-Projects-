"""Unit tests for RiskManager position sizing."""
from decimal import Decimal

from trading_bot.logging.logger import StructuredLogger
from trading_bot.risk.risk_manager import RiskManager


def test_position_size_respects_max_risk():
    logger = StructuredLogger(bot_name="test_risk")
    rm = RiskManager(logger)
    balance = Decimal("100000")
    entry = Decimal("100")
    stop = Decimal("95")
    size = rm.calculate_position_size(balance, entry, stop)
    risk = size * abs(entry - stop)
    assert risk <= balance * rm.MAX_RISK_PER_TRADE


def test_position_size_respects_max_position():
    logger = StructuredLogger(bot_name="test_risk2")
    rm = RiskManager(logger)
    balance = Decimal("100000")
    entry = Decimal("100")
    stop = Decimal("99.99")
    size = rm.calculate_position_size(balance, entry, stop)
    assert size * entry <= balance * rm.MAX_POSITION_SIZE


def test_zero_stop_distance_returns_zero():
    logger = StructuredLogger(bot_name="test_risk3")
    rm = RiskManager(logger)
    size = rm.calculate_position_size(Decimal("100000"), Decimal("100"), Decimal("100"))
    assert size == Decimal("0")


def test_fractional_kelly_positive():
    logger = StructuredLogger(bot_name="test_risk4")
    rm = RiskManager(logger)
    kelly = rm.fractional_kelly_size(
        Decimal("0.55"), Decimal("2"), Decimal("1"), Decimal("100000")
    )
    assert kelly > 0
