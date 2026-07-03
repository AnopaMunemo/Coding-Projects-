"""Tests for the optional LLM analyst-council strategy (llm_agents.py).

These run WITHOUT any API key or network: a fake client is injected, and the
no-key path is exercised to prove graceful fallback. No real LLM is ever called.
"""
from __future__ import annotations

from trading_bot.strategy.llm_agents import LLMAgentStrategy, _extract_json
from trading_bot.strategy.swing_trader import TradeSignal


def _synth_candles(n=120, start=100.0, step=0.4):
    candles, price = [], start
    for i in range(n):
        price += step if (i // 10) % 2 == 0 else -step * 0.5
        candles.append({"open": price, "high": price * 1.01, "low": price * 0.99,
                        "close": price, "volume": 1000, "date": f"2024-01-{(i % 28) + 1:02d}"})
    return candles


class _FakeClient:
    """Deterministic stand-in for an LLM endpoint."""
    def __init__(self, reply: str, available: bool = True):
        self.reply, self._available, self.calls = reply, available, 0
    def available(self) -> bool:
        return self._available
    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


def test_no_key_falls_back_to_swingtrader():
    # available()=False → whole batch handled by fallback, never crashes.
    strat = LLMAgentStrategy(client=_FakeClient("", available=False))
    out = strat.calculate_signals({"AAPL": _synth_candles()})
    assert isinstance(out["AAPL"], TradeSignal)
    assert out["AAPL"].direction in {"BUY", "SELL", "HOLD"}
    assert strat.calls_made == 0


def test_parses_buy_verdict_into_tradesignal():
    fake = _FakeClient('{"action":"BUY","confidence":0.8,"reason":"uptrend"}')
    strat = LLMAgentStrategy(client=fake, min_conviction=0.3)
    out = strat.calculate_signals({"XAUUSD": _synth_candles()})
    sig = out["XAUUSD"]
    assert sig.direction == "BUY"
    assert 0.0 <= sig.confidence <= 1.0
    assert sig.reason.startswith("llm:")
    assert fake.calls == 1


def test_low_conviction_downgraded_to_hold():
    fake = _FakeClient('{"action":"BUY","confidence":0.1,"reason":"weak"}')
    strat = LLMAgentStrategy(client=fake, min_conviction=0.5)
    sig = strat.calculate_signals({"AAPL": _synth_candles()})["AAPL"]
    assert sig.direction == "HOLD"


def test_cost_cap_limits_calls():
    fake = _FakeClient('{"action":"SELL","confidence":0.9,"reason":"x"}')
    strat = LLMAgentStrategy(client=fake, max_calls_per_cycle=2)
    data = {f"S{i}": _synth_candles() for i in range(5)}
    out = strat.calculate_signals(data)
    assert fake.calls == 2                      # hard cap respected
    assert len(out) == 5                        # every symbol still gets a signal
    assert all(isinstance(s, TradeSignal) for s in out.values())


def test_cache_avoids_repeat_calls():
    fake = _FakeClient('{"action":"BUY","confidence":0.7,"reason":"x"}')
    strat = LLMAgentStrategy(client=fake)
    candles = _synth_candles()
    strat.calculate_signals({"AAPL": candles})
    strat.calculate_signals({"AAPL": candles})  # same (symbol,date) → cached
    assert fake.calls == 1


def test_bad_json_degrades_gracefully():
    fake = _FakeClient("the model rambled with no json")
    strat = LLMAgentStrategy(client=fake)
    sig = strat.calculate_signals({"AAPL": _synth_candles()})["AAPL"]
    assert isinstance(sig, TradeSignal)         # parsed to HOLD, no crash


def test_insufficient_data_holds():
    strat = LLMAgentStrategy(client=_FakeClient('{"action":"BUY","confidence":0.9}'))
    sig = strat.calculate_signals({"AAPL": _synth_candles(n=10)})["AAPL"]
    assert sig.direction == "HOLD"
    assert sig.reason == "insufficient_data"


def test_extract_json_tolerates_fences():
    assert _extract_json('```json\n{"action":"BUY"}\n```')["action"] == "BUY"
    assert _extract_json("garbage") == {}
