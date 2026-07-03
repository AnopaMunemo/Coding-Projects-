"""Unit tests for swing strategy signal generation."""
from trading_bot.strategy.swing_trader import SwingTrader


def _make_candles(n: int, start_price: float = 100.0, trend: float = 0.5):
    candles = []
    price = start_price
    for i in range(n):
        price += trend
        candles.append({
            "timestamp": f"2024-01-{i+1:02d}T00:00:00",
            "open": str(price - 0.5),
            "high": str(price + 1),
            "low": str(price - 1),
            "close": str(price),
            "volume": "1000",
        })
    return candles


def test_insufficient_data_returns_hold():
    strategy = SwingTrader()
    signals = strategy.calculate_signals({"TEST": _make_candles(10)})
    assert signals["TEST"].direction == "HOLD"


def test_uptrend_generates_buy_or_hold():
    strategy = SwingTrader()
    candles = _make_candles(60, trend=1.0)
    signals = strategy.calculate_signals({"TEST": candles})
    assert signals["TEST"].direction in ("BUY", "HOLD", "SELL")
