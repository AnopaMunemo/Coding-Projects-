"""Pure signal generation — no I/O, no state mutation."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List

from trading_bot.strategy.indicators import atr, ema, macd, rsi, sma


@dataclass(frozen=True)
class TradeSignal:
    symbol: str
    direction: str
    confidence: float
    entry_hint: str
    stop_atr_multiplier: str
    reason: str


class BaseStrategy(ABC):
    @abstractmethod
    def calculate_signals(self, market_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, TradeSignal]:
        ...


class SwingTrader(BaseStrategy):
    """
    Swing strategy for stocks and gold:
    BUY when SMA20 > SMA50, RSI < 70, MACD histogram positive
    SELL when SMA20 < SMA50, RSI > 30, MACD histogram negative
    """

    RSI_OVERBOUGHT = 70.0
    RSI_OVERSOLD = 30.0
    MIN_BARS = 55

    def calculate_signals(self, market_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, TradeSignal]:
        signals: Dict[str, TradeSignal] = {}
        for symbol, candles in market_data.items():
            if len(candles) < self.MIN_BARS:
                signals[symbol] = TradeSignal(
                    symbol=symbol,
                    direction="HOLD",
                    confidence=0.0,
                    entry_hint=str(candles[-1]["close"]) if candles else "0",
                    stop_atr_multiplier="1.5",
                    reason="insufficient_data",
                )
                continue

            sma20_arr = sma(candles, 20)
            sma50_arr = sma(candles, 50)
            if len(sma20_arr) == 0 or len(sma50_arr) == 0:
                signals[symbol] = TradeSignal(
                    symbol=symbol, direction="HOLD", confidence=0.0,
                    entry_hint=str(candles[-1]["close"]),
                    stop_atr_multiplier="1.5", reason="indicator_error",
                )
                continue

            sma20 = float(sma20_arr[-1])
            sma50 = float(sma50_arr[-50:][-1]) if len(sma50_arr) >= 50 else float(sma50_arr[-1])
            rsi_val = rsi(candles, 14)
            macd_data = macd(candles)
            close = str(candles[-1]["close"])

            if sma20 > sma50 and rsi_val < self.RSI_OVERBOUGHT and macd_data["histogram"] > 0:
                spread = abs(sma20 - sma50) / sma50 if sma50 else 0
                confidence = min(0.5 + spread * 10 + (self.RSI_OVERBOUGHT - rsi_val) / 100, 1.0)
                signals[symbol] = TradeSignal(
                    symbol=symbol,
                    direction="BUY",
                    confidence=confidence,
                    entry_hint=close,
                    stop_atr_multiplier="1.5",
                    reason=f"SMA20>{sma50:.2f}, RSI={rsi_val:.1f}, MACD+",
                )
            elif sma20 < sma50 and rsi_val > self.RSI_OVERSOLD and macd_data["histogram"] < 0:
                spread = abs(sma20 - sma50) / sma50 if sma50 else 0
                confidence = min(0.5 + spread * 10 + (rsi_val - self.RSI_OVERSOLD) / 100, 1.0)
                signals[symbol] = TradeSignal(
                    symbol=symbol,
                    direction="SELL",
                    confidence=confidence,
                    entry_hint=close,
                    stop_atr_multiplier="1.5",
                    reason=f"SMA20<{sma50:.2f}, RSI={rsi_val:.1f}, MACD-",
                )
            else:
                signals[symbol] = TradeSignal(
                    symbol=symbol,
                    direction="HOLD",
                    confidence=0.0,
                    entry_hint=close,
                    stop_atr_multiplier="1.5",
                    reason="no_confluence",
                )
        return signals

    @staticmethod
    def get_atr(candles: List[Dict[str, Any]]) -> Decimal:
        return Decimal(str(round(atr(candles, 14), 6)))
