"""
llm_agents.py — optional LLM "analyst council" signal source for Atlas.

Inspired by the TradingAgents paper (arXiv 2412.20138) but deliberately lightweight:
instead of ~31 LLM/tool calls per decision, it sends ONE compact, structured prompt
per symbol (a digest of price + indicators) and asks for a JSON verdict. It is meant
to be ONE weighted input into the ensemble — never the final sizing authority. The
engine's RiskManager always keeps hard-cap control.

Safety / cost design (all enforced here, not by trust):
• API key is read ONLY from the environment (`ATLAS_LLM_API_KEY` or `OPENAI_API_KEY`).
  It is never hardcoded, logged, or persisted.
• Hard cost cap: `max_calls_per_cycle` + `daily_call_cap`. When exhausted → HOLD/fallback.
• Per-(symbol, date) caching so the same day is never paid for twice.
• Timeout on every call; ANY error (no key, no library, bad JSON, network) degrades
  gracefully to the built-in SwingTrader, so the async loop never crashes.

This module makes a NETWORK call, so unlike the pure strategies it is not I/O-free — that
is a documented, deliberate exception. It writes no files and mutates no global state.
"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any, Callable, Dict, List, Optional

from trading_bot.strategy.swing_trader import BaseStrategy, SwingTrader, TradeSignal

try:  # indicators are best-effort context enrichment
    from trading_bot.strategy.indicators import sma, rsi  # type: ignore
    _IND_OK = True
except Exception:  # pragma: no cover
    _IND_OK = False

_ACTION_MAP = {"BUY": "BUY", "SELL": "SELL", "HOLD": "HOLD",
               "LONG": "BUY", "SHORT": "SELL", "FLAT": "HOLD"}

_SYSTEM_PROMPT = (
    "You are a disciplined trading analyst. Given a compact market digest, respond with "
    "ONLY a JSON object: {\"action\": \"BUY\"|\"SELL\"|\"HOLD\", \"confidence\": 0.0-1.0, "
    "\"reason\": \"<=12 words\"}. Be conservative; prefer HOLD when signals conflict. "
    "No prose outside the JSON."
)


class LLMClient:
    """Thin wrapper over an OpenAI-compatible chat endpoint.

    Injectable for testing: pass any object with `.complete(system, user) -> str`.
    The real client is only constructed when a key is present in the environment.
    """

    def __init__(self, model: str = "gpt-4o-mini", timeout: float = 20.0,
                 temperature: float = 0.0) -> None:
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self._client = None

    @staticmethod
    def api_key() -> Optional[str]:
        return os.environ.get("ATLAS_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")

    def available(self) -> bool:
        return bool(self.api_key())

    def _ensure(self):
        if self._client is None:
            from openai import OpenAI  # imported lazily; optional dependency
            self._client = OpenAI(api_key=self.api_key(), timeout=self.timeout)
        return self._client

    def complete(self, system: str, user: str) -> str:
        client = self._ensure()
        resp = client.chat.completions.create(
            model=self.model,
            temperature=self.temperature,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content or ""


class LLMAgentStrategy(BaseStrategy):
    """LLM analyst verdict as a BaseStrategy.

    Parameters
    ──────────
    client            : injectable LLMClient-like object (defaults to env-keyed OpenAI).
    max_calls_per_cycle : cap on LLM calls per calculate_signals() invocation.
    daily_call_cap    : cap on total LLM calls across the strategy's lifetime/day.
    min_conviction    : minimum confidence (0–1) to emit BUY/SELL instead of HOLD.
    fallback          : strategy used when the LLM is unavailable or errors.
    """

    MIN_BARS = 55

    def __init__(
        self,
        client: Optional[Any] = None,
        max_calls_per_cycle: int = 4,
        daily_call_cap: int = 50,
        min_conviction: float = 0.30,
        fallback: Optional[BaseStrategy] = None,
    ) -> None:
        self.client = client if client is not None else LLMClient()
        self.max_calls_per_cycle = int(max_calls_per_cycle)
        self.daily_call_cap = int(daily_call_cap)
        self.min_conviction = float(min_conviction)
        self.fallback = fallback or SwingTrader()
        self._calls_made = 0
        self._cache: Dict[str, TradeSignal] = {}

    # ── public API ───────────────────────────────────────────────────────────
    def calculate_signals(self, market_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, TradeSignal]:
        # If the LLM can't be used at all, hand the whole batch to the fallback.
        if not self._client_available():
            return self.fallback.calculate_signals(market_data)

        out: Dict[str, TradeSignal] = {}
        calls_this_cycle = 0
        for symbol, candles in market_data.items():
            if not candles or len(candles) < self.MIN_BARS:
                out[symbol] = self._hold(symbol, candles, "insufficient_data")
                continue

            cache_key = self._cache_key(symbol, candles)
            if cache_key in self._cache:
                out[symbol] = self._cache[cache_key]
                continue

            # Respect both the per-cycle and lifetime cost caps.
            if calls_this_cycle >= self.max_calls_per_cycle or self._calls_made >= self.daily_call_cap:
                out[symbol] = self._fallback_one(symbol, candles, "cost_cap_reached")
                continue

            try:
                digest = self._digest(symbol, candles)
                raw = self.client.complete(_SYSTEM_PROMPT, digest)
                calls_this_cycle += 1
                self._calls_made += 1
                sig = self._parse(symbol, candles, raw)
            except Exception as exc:  # never let a symbol crash the loop
                sig = self._fallback_one(symbol, candles, f"llm_error({type(exc).__name__})")

            self._cache[cache_key] = sig
            out[symbol] = sig
        return out

    @property
    def calls_made(self) -> int:
        return self._calls_made

    # ── helpers ──────────────────────────────────────────────────────────────
    def _client_available(self) -> bool:
        avail = getattr(self.client, "available", None)
        try:
            return bool(avail()) if callable(avail) else True
        except Exception:
            return False

    def _cache_key(self, symbol: str, candles: List[Dict[str, Any]]) -> str:
        last = candles[-1]
        stamp = last.get("date") or last.get("time") or last.get("timestamp") or len(candles)
        return f"{symbol}:{stamp}"

    def _digest(self, symbol: str, candles: List[Dict[str, Any]]) -> str:
        """Compact, token-efficient market digest — numbers, not prose."""
        closes = [float(c["close"]) for c in candles]
        last = closes[-1]
        ctx: Dict[str, Any] = {
            "symbol": symbol,
            "close": round(last, 4),
            "ret_5": round((last / closes[-6] - 1) * 100, 2) if len(closes) > 5 else None,
            "ret_20": round((last / closes[-21] - 1) * 100, 2) if len(closes) > 20 else None,
            "hi_20": round(max(closes[-20:]), 4),
            "lo_20": round(min(closes[-20:]), 4),
        }
        if _IND_OK:
            try:
                s20, s50 = sma(candles, 20), sma(candles, 50)
                r = rsi(candles, 14)
                if len(s20) and len(s50):
                    ctx["sma20"], ctx["sma50"] = round(float(s20[-1]), 4), round(float(s50[-1]), 4)
                if len(r):
                    ctx["rsi14"] = round(float(r[-1]), 1)
            except Exception:
                pass
        return json.dumps(ctx, separators=(",", ":"))

    def _parse(self, symbol: str, candles: List[Dict[str, Any]], raw: str) -> TradeSignal:
        data = _extract_json(raw)
        action = _ACTION_MAP.get(str(data.get("action", "HOLD")).upper(), "HOLD")
        conf = data.get("confidence", 0.0)
        try:
            conf = float(conf)
        except Exception:
            conf = 0.0
        if not math.isfinite(conf):
            conf = 0.0
        conf = min(1.0, max(0.0, conf))
        reason = str(data.get("reason", ""))[:80] or "llm_verdict"
        close = str(candles[-1]["close"])
        if action == "HOLD" or conf < self.min_conviction:
            return TradeSignal(symbol=symbol, direction="HOLD", confidence=conf,
                               entry_hint=close, stop_atr_multiplier="1.5",
                               reason=f"llm_low_conviction:{reason}")
        return TradeSignal(symbol=symbol, direction=action, confidence=conf,
                           entry_hint=close, stop_atr_multiplier="1.5",
                           reason=f"llm:{reason}")

    def _fallback_one(self, symbol: str, candles: List[Dict[str, Any]], why: str) -> TradeSignal:
        try:
            ts = self.fallback.calculate_signals({symbol: candles})[symbol]
            return TradeSignal(symbol=symbol, direction=ts.direction, confidence=ts.confidence,
                               entry_hint=ts.entry_hint, stop_atr_multiplier=ts.stop_atr_multiplier,
                               reason=f"{why}->{ts.reason}")
        except Exception:
            return self._hold(symbol, candles, why)

    @staticmethod
    def _hold(symbol: str, candles: Optional[List[Dict[str, Any]]], why: str) -> TradeSignal:
        close = str(candles[-1]["close"]) if candles else "0"
        return TradeSignal(symbol=symbol, direction="HOLD", confidence=0.0,
                           entry_hint=close, stop_atr_multiplier="1.5", reason=why)


def _extract_json(raw: str) -> Dict[str, Any]:
    """Best-effort JSON extraction — tolerates code fences / surrounding text."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}
