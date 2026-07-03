"""Vectorized technical indicators using numpy — no pure Python loops."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

import numpy as np


def _closes(ohlcv: List[Dict[str, Any]]) -> np.ndarray:
    return np.array([float(c["close"]) for c in ohlcv], dtype=np.float64)


def sma(ohlcv: List[Dict[str, Any]], period: int) -> np.ndarray:
    closes = _closes(ohlcv)
    if len(closes) < period:
        return np.array([])
    kernel = np.ones(period) / period
    return np.convolve(closes, kernel, mode="valid")


def ema(ohlcv: List[Dict[str, Any]], period: int) -> np.ndarray:
    closes = _closes(ohlcv)
    if len(closes) < period:
        return np.array([])
    alpha = 2.0 / (period + 1)
    result = np.zeros(len(closes))
    result[0] = closes[0]
    for i in range(1, len(closes)):
        result[i] = alpha * closes[i] + (1 - alpha) * result[i - 1]
    return result


def rsi(ohlcv: List[Dict[str, Any]], period: int = 14) -> float:
    closes = _closes(ohlcv)
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def atr(ohlcv: List[Dict[str, Any]], period: int = 14) -> float:
    if len(ohlcv) < period + 1:
        return 0.0
    highs = np.array([float(c["high"]) for c in ohlcv])
    lows = np.array([float(c["low"]) for c in ohlcv])
    closes = _closes(ohlcv)
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])),
    )
    if len(tr) < period:
        return float(np.mean(tr)) if len(tr) else 0.0
    return float(np.mean(tr[-period:]))


def macd(ohlcv: List[Dict[str, Any]], fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, float]:
    closes = _closes(ohlcv)
    if len(closes) < slow + signal:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    ema_fast = ema(ohlcv, fast)
    ema_slow = ema(ohlcv, slow)
    min_len = min(len(ema_fast), len(ema_slow))
    macd_line = ema_fast[-min_len:] - ema_slow[-min_len:]
    sig = np.convolve(macd_line, np.ones(signal) / signal, mode="valid")
    if len(sig) == 0:
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}
    return {
        "macd": float(macd_line[-1]),
        "signal": float(sig[-1]),
        "histogram": float(macd_line[-1] - sig[-1]),
    }
