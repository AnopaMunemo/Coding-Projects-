"""Tests for the merged ensemble strategy + self-learning loop."""
from __future__ import annotations

import os
from pathlib import Path

from trading_bot.learning.weight_store import WeightStore
from trading_bot.learning.retrainer import Retrainer
from trading_bot.strategy.ensemble import EnsembleStrategy
from trading_bot.strategy.swing_trader import TradeSignal


def _synth_candles(n=120, start=100.0, step=0.4):
    candles = []
    price = start
    for i in range(n):
        price += step if (i // 10) % 2 == 0 else -step * 0.5
        candles.append({
            "open": price, "high": price * 1.01, "low": price * 0.99,
            "close": price, "volume": 1000,
        })
    return candles


def test_weight_store_atomic_roundtrip(tmp_path):
    p = tmp_path / "w.json"
    ws = WeightStore(str(p))
    ws.set_weights({"MACD": 2.0, "RSI": 1.0})       # normalised to mean 1.0
    assert p.exists()
    ws2 = WeightStore(str(p))
    assert abs(ws2.get_weight("MACD") - 4 / 3) < 1e-6   # 2.0 / mean(1.5)
    ws2.update_weight("RSI", 0.5)
    assert ws2.get_weight("RSI") > 0


def test_retrainer_rewards_winners_penalises_losers(tmp_path):
    ws = WeightStore(str(tmp_path / "w.json"))
    r = Retrainer(ws, learn_rate=0.1)
    r.update_from_outcomes([
        {"names": ["MACD"], "pnl": 10.0},     # winner → up
        {"names": ["RSI"], "pnl": -5.0},      # loser  → down
    ])
    assert ws.get_weight("MACD") > 1.0
    assert ws.get_weight("RSI") < 1.0


def test_ensemble_returns_valid_tradesignals():
    strat = EnsembleStrategy(min_conviction=0.0)
    out = strat.calculate_signals({"AAPL": _synth_candles()})
    assert "AAPL" in out
    sig = out["AAPL"]
    assert isinstance(sig, TradeSignal)
    assert sig.direction in ("BUY", "SELL", "HOLD")
    assert 0.0 <= sig.confidence <= 1.0


def test_ensemble_handles_insufficient_data():
    strat = EnsembleStrategy()
    out = strat.calculate_signals({"AAPL": _synth_candles(5)})
    assert out["AAPL"].direction == "HOLD"
