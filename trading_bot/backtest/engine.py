"""Backtesting engine with full performance metrics."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

import numpy as np

from trading_bot.risk.risk_manager import RiskManager
from trading_bot.strategy.swing_trader import SwingTrader
from trading_bot.logging.logger import StructuredLogger


class BacktestEngine:
    def __init__(
        self,
        strategy: SwingTrader,
        risk_manager: RiskManager,
        logger: StructuredLogger,
        start_date: str,
        end_date: str,
    ) -> None:
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.logger = logger
        self.start_date = start_date
        self.end_date = end_date
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = []

    async def run(
        self,
        broker: Any,
        symbols: List[str],
        starting_balance: Decimal = Decimal("100000"),
    ) -> Dict[str, Any]:
        balance = starting_balance
        self.equity_curve = [float(balance)]
        open_positions: Dict[str, Dict[str, Any]] = {}

        for symbol in symbols:
            ohlcv = await broker.get_historical_data(
                symbol, self.start_date, self.end_date, "1d"
            )
            if len(ohlcv) < 60:
                continue

            for i in range(55, len(ohlcv)):
                window = ohlcv[: i + 1]
                signals = self.strategy.calculate_signals({symbol: window})
                signal = signals[symbol]
                candle = ohlcv[i]
                close = Decimal(str(candle["close"]))

                if symbol in open_positions:
                    pos = open_positions[symbol]
                    sl = Decimal(str(pos["stop_loss"]))
                    tp = Decimal(str(pos["take_profit"]))
                    side = pos["side"]
                    if side == "buy":
                        if close <= sl or close >= tp:
                            pnl = (close - Decimal(str(pos["entry"]))) * Decimal(str(pos["qty"]))
                            balance += pnl
                            self.trades.append({
                                "symbol": symbol, "pnl": float(pnl),
                                "outcome": "SL" if close <= sl else "TP",
                            })
                            del open_positions[symbol]
                    else:
                        if close >= sl or close <= tp:
                            pnl = (Decimal(str(pos["entry"])) - close) * Decimal(str(pos["qty"]))
                            balance += pnl
                            self.trades.append({
                                "symbol": symbol, "pnl": float(pnl),
                                "outcome": "SL" if close >= sl else "TP",
                            })
                            del open_positions[symbol]

                if symbol not in open_positions and signal.direction in ("BUY", "SELL"):
                    atr_val = SwingTrader.get_atr(window)
                    sl, tp, qty, side = self.risk_manager.calculate_position(
                        close, atr_val, balance, signal.direction
                    )
                    if qty > 0:
                        open_positions[symbol] = {
                            "entry": str(close), "qty": str(qty),
                            "stop_loss": str(sl), "take_profit": str(tp), "side": side,
                        }

                self.equity_curve.append(float(balance))

        return self.calculate_metrics(balance, starting_balance)

    def calculate_metrics(
        self, final_balance: Decimal, starting_balance: Decimal
    ) -> Dict[str, Any]:
        equity_array = np.array(self.equity_curve) if self.equity_curve else np.array([float(starting_balance)])
        returns = np.diff(equity_array) / equity_array[:-1] if len(equity_array) > 1 else np.array([0.0])

        sharpe = 0.0
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252))

        peak = np.maximum.accumulate(equity_array)
        drawdown = (equity_array - peak) / peak
        max_drawdown = float(np.min(drawdown)) if len(drawdown) else 0.0

        wins = [t["pnl"] for t in self.trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in self.trades if t["pnl"] < 0]
        win_rate = len(wins) / len(self.trades) if self.trades else 0.0
        total_wins = sum(wins)
        total_losses = abs(sum(losses))
        profit_factor = total_wins / total_losses if total_losses > 0 else 0.0
        total_return = float((final_balance - starting_balance) / starting_balance)

        sortino = 0.0
        downside = returns[returns < 0]
        if len(downside) > 0 and np.std(downside) > 0:
            sortino = float(np.mean(returns) / np.std(downside) * np.sqrt(252))

        return {
            "final_balance": float(final_balance),
            "total_return": total_return,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_trades": len(self.trades),
            "equity_curve": self.equity_curve,
        }
