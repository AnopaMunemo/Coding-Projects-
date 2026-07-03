"""
ensemble.py — bridges the Desk's strategy library into the execution engine.

This is the "brain → body" merge. The Desk (repo-root `strategies.py`) ships
31 strategy classes across trend / momentum / mean-reversion / breakout / SMC /
seasonal categories, plus a consensus `aggregate_signal()`. `EnsembleStrategy`
wraps all of them behind the engine's `BaseStrategy.calculate_signals()`
contract so the production bot can trade the full library as one weighted,
regime-aware signal instead of the single SwingTrader.

Per-strategy blend weights are loaded from the learning WeightStore, so the
ensemble's behaviour adapts as the retrainer updates weights from outcomes.

Design notes
────────────
• Pure signal generation — no I/O, no order placement (keeps Gate-3 modularity).
• Degrades gracefully: if the Desk library or pandas is unavailable, it falls
  back to the built-in SwingTrader so the engine never crashes.
• Candle dicts (lowercase open/high/low/close) are converted to the capitalised
  OHLCV DataFrame that `strategies.py` expects.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Dict, List

from trading_bot.strategy.swing_trader import BaseStrategy, SwingTrader, TradeSignal

# Make the repo-root Desk modules importable (strategies.py lives at repo root)
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import pandas as pd
    from strategies import run_all_strategies, aggregate_signal, STRATEGY_CATEGORIES  # type: ignore
    _DESK_OK = True
except Exception:  # pragma: no cover - environment without Desk library
    _DESK_OK = False


def _candles_to_df(candles: List[Dict[str, Any]]):
    """Convert engine candle dicts → capitalised OHLCV DataFrame for the Desk."""
    rows = {
        "Open":   [float(c["open"]) for c in candles],
        "High":   [float(c["high"]) for c in candles],
        "Low":    [float(c["low"]) for c in candles],
        "Close":  [float(c["close"]) for c in candles],
        "Volume": [float(c.get("volume", 0) or 0) for c in candles],
    }
    return pd.DataFrame(rows)


# LONG/SHORT/FLAT (Desk) → BUY/SELL/HOLD (engine)
_DIR_MAP = {"LONG": "BUY", "SHORT": "SELL", "FLAT": "HOLD"}


class EnsembleStrategy(BaseStrategy):
    """
    Weighted consensus over the full Desk strategy library.

    Parameters
    ──────────
    weight_store : optional WeightStore — per-strategy blend weights.
    categories   : optional list to restrict which strategy categories run.
    min_conviction : minimum aggregated confidence (0–1) to emit BUY/SELL.
    """

    MIN_BARS = 60

    def __init__(self, weight_store=None, categories=None, min_conviction: float = 0.15) -> None:
        self.weight_store = weight_store
        self.categories = categories
        self.min_conviction = float(min_conviction)
        self._fallback = SwingTrader()

    def calculate_signals(self, market_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, TradeSignal]:
        if not _DESK_OK:
            return self._fallback.calculate_signals(market_data)

        out: Dict[str, TradeSignal] = {}
        for symbol, candles in market_data.items():
            if not candles or len(candles) < self.MIN_BARS:
                out[symbol] = TradeSignal(
                    symbol=symbol, direction="HOLD", confidence=0.0,
                    entry_hint=str(candles[-1]["close"]) if candles else "0",
                    stop_atr_multiplier="1.5", reason="insufficient_data",
                )
                continue
            try:
                df = _candles_to_df(candles)
                signals = run_all_strategies(df)
                signals = self._apply_weights(signals)
                agg = aggregate_signal(signals)
                out[symbol] = self._to_trade_signal(symbol, candles, agg)
            except Exception as exc:  # never let one symbol crash the loop
                fb = self._fallback.calculate_signals({symbol: candles})
                ts = fb[symbol]
                out[symbol] = TradeSignal(
                    symbol=symbol, direction=ts.direction, confidence=ts.confidence,
                    entry_hint=ts.entry_hint, stop_atr_multiplier=ts.stop_atr_multiplier,
                    reason=f"ensemble_fallback({type(exc).__name__})",
                )
        return out

    # ── helpers ────────────────────────────────────────────────────────────
    def _apply_weights(self, signals):
        """Scale each strategy's strength by its learned weight (if any)."""
        if not self.weight_store:
            return signals
        weighted = []
        for s in signals:
            w = self.weight_store.get_weight(getattr(s, "name", ""), 1.0)
            try:
                s.strength = float(min(1.0, max(0.0, s.strength * w)))
            except Exception:
                pass
            weighted.append(s)
        return weighted

    def _to_trade_signal(self, symbol, candles, agg) -> TradeSignal:
        direction = _DIR_MAP.get(agg.get("direction", "FLAT"), "HOLD")
        confidence = float(agg.get("confidence", 0.0) or 0.0)
        if not math.isfinite(confidence):          # guard NaN/inf from aggregation
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))
        close = str(candles[-1]["close"])
        if direction == "HOLD" or confidence < self.min_conviction:
            return TradeSignal(
                symbol=symbol, direction="HOLD", confidence=confidence,
                entry_hint=close, stop_atr_multiplier="1.5",
                reason=f"low_conviction L{agg.get('long_count',0)}/S{agg.get('short_count',0)}",
            )
        return TradeSignal(
            symbol=symbol, direction=direction, confidence=confidence,
            entry_hint=close, stop_atr_multiplier="1.5",
            reason=f"ensemble L{agg.get('long_count',0)}/S{agg.get('short_count',0)} conf={confidence:.2f}",
        )
