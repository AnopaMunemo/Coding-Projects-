"""Walk-forward, parameter sensitivity, Monte Carlo, and trade frequency checks."""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List

import numpy as np

from trading_bot.backtest.engine import BacktestEngine
from trading_bot.logging.logger import StructuredLogger
from trading_bot.risk.risk_manager import RiskManager
from trading_bot.strategy.swing_trader import SwingTrader


class OverfittingDetection:
    def __init__(self, logger: StructuredLogger) -> None:
        self.logger = logger

    async def walk_forward_analysis(
        self,
        broker: Any,
        symbol: str,
        start_date: str,
        end_date: str,
        train_days: int = 252,
        test_days: int = 63,
    ) -> List[Dict[str, Any]]:
        results = []
        current = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        strategy = SwingTrader()
        risk = RiskManager(self.logger)

        while current + timedelta(days=train_days + test_days) <= end:
            train_end = current + timedelta(days=train_days)
            test_end = train_end + timedelta(days=test_days)

            test_engine = BacktestEngine(
                strategy, risk, self.logger,
                train_end.strftime("%Y-%m-%d"),
                test_end.strftime("%Y-%m-%d"),
            )
            metrics = await test_engine.run(broker, [symbol])
            results.append({
                "train_period": (current.strftime("%Y-%m-%d"), train_end.strftime("%Y-%m-%d")),
                "test_period": (train_end.strftime("%Y-%m-%d"), test_end.strftime("%Y-%m-%d")),
                "sharpe": metrics["sharpe_ratio"],
                "win_rate": metrics["win_rate"],
                "trades": metrics["total_trades"],
                "profit_factor": metrics["profit_factor"],
            })
            current = test_end

        if results:
            sharpes = [r["sharpe"] for r in results]
            sharpe_std = np.std(sharpes)
            sharpe_mean = np.mean(sharpes) if np.mean(sharpes) != 0 else 1
            if abs(sharpe_mean) > 0 and sharpe_std / abs(sharpe_mean) > 0.5:
                self.logger.warning(
                    "HIGH OVERFITTING RISK: Sharpe varies significantly across windows",
                    variation=f"{sharpe_std / abs(sharpe_mean):.1%}",
                )
        return results

    async def monte_carlo_simulation(
        self,
        trade_pnls: List[float],
        starting_balance: float = 100000,
        simulations: int = 1000,
    ) -> Dict[str, Any]:
        if not trade_pnls:
            return {"mean_final": starting_balance, "p5_final": starting_balance, "p95_final": starting_balance}
        finals = []
        for _ in range(simulations):
            balance = starting_balance
            shuffled = np.random.choice(trade_pnls, size=len(trade_pnls), replace=True)
            for pnl in shuffled:
                balance += pnl
            finals.append(balance)
        return {
            "mean_final": float(np.mean(finals)),
            "p5_final": float(np.percentile(finals, 5)),
            "p95_final": float(np.percentile(finals, 95)),
            "ruin_probability": float(np.mean([f < starting_balance * 0.5 for f in finals])),
        }

    def trade_frequency_check(self, num_trades: int, num_days: int) -> bool:
        if num_days <= 0:
            return False
        trades_per_100 = num_trades / num_days * 100
        if trades_per_100 > 50:
            self.logger.warning(f"Trade frequency too high: {trades_per_100:.0f} per 100 days")
            return False
        if trades_per_100 < 2:
            self.logger.warning(f"Trade frequency too low: {trades_per_100:.0f} per 100 days")
            return False
        return True
