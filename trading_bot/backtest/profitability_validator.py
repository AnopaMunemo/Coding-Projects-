"""Profitability gate validation for backtest and live comparison."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Tuple

from trading_bot.config import BACKTEST
from trading_bot.logging.logger import StructuredLogger


class ProfitabilityValidator:
    MIN_PROFIT_FACTOR = BACKTEST.MIN_PROFIT_FACTOR
    MIN_SHARPE = BACKTEST.MIN_SHARPE
    MIN_WIN_RATE = BACKTEST.MIN_WIN_RATE
    MAX_DRAWDOWN = BACKTEST.MAX_DRAWDOWN
    MIN_RETURN_DD_RATIO = BACKTEST.MIN_RETURN_DD_RATIO

    def __init__(self, logger: StructuredLogger) -> None:
        self.logger = logger

    def validate_backtest_results(self, metrics: Dict[str, Any]) -> Tuple[bool, List[str], List[str]]:
        passed: List[str] = []
        failed: List[str] = []

        checks = [
            ("profit_factor", metrics.get("profit_factor", 0), float(self.MIN_PROFIT_FACTOR), ">="),
            ("sharpe_ratio", metrics.get("sharpe_ratio", 0), float(self.MIN_SHARPE), ">="),
            ("win_rate", metrics.get("win_rate", 0), float(self.MIN_WIN_RATE), ">="),
            ("max_drawdown", metrics.get("max_drawdown", 0), -float(self.MAX_DRAWDOWN), ">="),
        ]

        for name, value, threshold, op in checks:
            if op == ">=" and value >= threshold:
                passed.append(f"PASS {name}: {value:.4f} >= {threshold:.4f}")
            else:
                failed.append(f"FAIL {name}: {value:.4f} < {threshold:.4f}")

        max_dd = metrics.get("max_drawdown", 0)
        total_return = metrics.get("total_return", 0)
        if max_dd != 0:
            ratio = total_return / abs(max_dd)
            if ratio >= float(self.MIN_RETURN_DD_RATIO):
                passed.append(f"PASS return/dd_ratio: {ratio:.2f}")
            else:
                failed.append(f"FAIL return/dd_ratio: {ratio:.2f} < {float(self.MIN_RETURN_DD_RATIO)}")

        for p in passed:
            self.logger.info(p)
        for f in failed:
            self.logger.error(f)

        if failed:
            self.logger.critical(f"BACKTEST VALIDATION FAILED: {len(failed)} metric(s)")
            return False, passed, failed

        self.logger.info(f"BACKTEST VALIDATION PASSED: All {len(passed)} metrics met")
        return True, passed, failed

    def validate_live_vs_backtest(
        self,
        live_metrics: Dict[str, Any],
        backtest_metrics: Dict[str, Any],
        sharpe_tolerance: float = 0.3,
        pf_tolerance: float = 0.2,
    ) -> bool:
        live_sharpe = live_metrics.get("sharpe_ratio", 0)
        bt_sharpe = backtest_metrics.get("sharpe_ratio", 0)
        if abs(live_sharpe - bt_sharpe) > sharpe_tolerance:
            self.logger.warning(
                "Live Sharpe deviates from backtest",
                live=live_sharpe, backtest=bt_sharpe,
            )
            return False

        live_pf = live_metrics.get("profit_factor", 0)
        bt_pf = backtest_metrics.get("profit_factor", 1)
        if bt_pf > 0 and abs(live_pf - bt_pf) / bt_pf > pf_tolerance:
            self.logger.warning(
                "Live profit factor deviates from backtest",
                live=live_pf, backtest=bt_pf,
            )
            return False
        return True
