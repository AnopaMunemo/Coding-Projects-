"""
Automatic gate scorer — evaluates codebase against production deployment criteria.
Run: python -m trading_bot.evaluation.gate_scorer
"""
from __future__ import annotations

import ast
import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class GateScorer:
    GATE1_WEIGHTS = {
        "position_sizing": 25,
        "stop_loss": 25,
        "circuit_breaker": 20,
        "drawdown_margin": 15,
        "connection_monitor": 15,
        "reconciliation": 10,
    }
    GATE2_WEIGHTS = {
        "async_architecture": 25,
        "latency": 20,
        "graceful_degradation": 25,
        "exception_handling": 20,
    }
    GATE3_WEIGHTS = {
        "modularity": 20,
        "logging": 15,
        "state_persistence": 15,
        "backtesting": 20,
        "overfitting": 20,
        "profitability": 10,
    }

    CRITICAL_MODULES = [
        "risk/risk_manager.py",
        "risk/circuit_breaker.py",
        "risk/equity_monitor.py",
        "execution/executor.py",
        "infrastructure/connection_monitor.py",
        "infrastructure/state_manager.py",
        "broker/base_broker.py",
        "backtest/engine.py",
        "backtest/overfitting_detection.py",
        "backtest/profitability_validator.py",
    ]

    def __init__(self, root: Path = PROJECT_ROOT) -> None:
        self.root = root
        self.scores: Dict[str, float] = {}

    def _module_exists(self, rel_path: str) -> bool:
        return (self.root / rel_path).exists()

    def _file_contains(self, rel_path: str, *patterns: str) -> bool:
        path = self.root / rel_path
        if not path.exists():
            return False
        content = path.read_text(encoding="utf-8", errors="ignore")
        return all(p in content for p in patterns)

    def score_gate1(self) -> Tuple[float, Dict[str, float]]:
        details = {}
        details["position_sizing"] = 100 if self._file_contains(
            "risk/risk_manager.py", "MAX_POSITION_SIZE", "MAX_RISK_PER_TRADE", "KELLY_FRACTION", "calculate_position_size"
        ) else 0
        details["stop_loss"] = 100 if self._file_contains(
            "execution/executor.py", "place_oco_order", "emergency", "stop_loss"
        ) else 0
        details["circuit_breaker"] = 100 if self._file_contains(
            "risk/circuit_breaker.py", "MAX_DAILY_LOSS", "MAX_HOURLY_LOSS", "set_trading_halted", "save"
        ) else 0
        details["drawdown_margin"] = 100 if self._file_contains(
            "risk/equity_monitor.py", "MAX_DRAWDOWN", "MIN_MARGIN_RATIO", "equity_curve"
        ) else 0
        details["connection_monitor"] = 100 if self._file_contains(
            "infrastructure/connection_monitor.py", "heartbeat_loop", "emergency_shutdown", "MAX_MISSED"
        ) else 0
        details["reconciliation"] = 100 if self._file_contains(
            "infrastructure/state_manager.py", "reconcile_with_broker", "orphaned", "Atomic"
        ) else 0

        total = sum(details[k] * self.GATE1_WEIGHTS[k] / 100 for k in details)
        max_w = sum(self.GATE1_WEIGHTS.values())
        return total / max_w * 100, details

    def score_gate2(self) -> Tuple[float, Dict[str, float]]:
        details = {}
        details["async_architecture"] = 100 if self._file_contains(
            "main.py", "asyncio", "async def", "gather"
        ) else 0
        details["latency"] = 100 if self._module_exists("infrastructure/latency_monitor.py") else 0
        details["graceful_degradation"] = 100 if self._file_contains(
            "infrastructure/retry_strategy.py", "retry_with_backoff", "CircuitBreakerOpen", "MAX_RETRIES"
        ) else 0
        details["exception_handling"] = 100 if self._file_contains(
            "execution/executor.py", "TimeoutError", "ConnectionError", "exc_info", "cancel_order"
        ) else 0

        total = sum(details[k] * self.GATE2_WEIGHTS[k] / 100 for k in details)
        max_w = sum(self.GATE2_WEIGHTS.values())
        return total / max_w * 100, details

    def score_gate3(self) -> Tuple[float, Dict[str, float]]:
        details = {}
        modules = ["strategy/", "execution/", "data/", "risk/", "broker/"]
        details["modularity"] = 100 if all(self._module_exists(m) or (self.root / m).exists() for m in modules) else 60
        details["logging"] = 100 if self._file_contains(
            "logging/logger.py", "RotatingFileHandler", "log_trade", "critical"
        ) else 0
        details["state_persistence"] = 100 if self._file_contains(
            "infrastructure/state_manager.py", "SCHEMA_VERSION", "migrate", "save"
        ) else 0
        details["backtesting"] = 100 if self._module_exists("backtest/engine.py") else 0
        details["overfitting"] = 100 if self._file_contains(
            "backtest/overfitting_detection.py", "walk_forward", "monte_carlo"
        ) else 0
        details["profitability"] = 100 if self._module_exists("backtest/profitability_validator.py") else 0

        total = sum(details[k] * self.GATE3_WEIGHTS[k] / 100 for k in details)
        max_w = sum(self.GATE3_WEIGHTS.values())
        return total / max_w * 100, details

    def run_full_evaluation(self) -> Dict[str, Any]:
        missing = [m for m in self.CRITICAL_MODULES if not self._module_exists(m)]
        g1, g1d = self.score_gate1()
        g2, g2d = self.score_gate2()
        g3, g3d = self.score_gate3()

        overall = g1 * 0.40 + g2 * 0.35 + g3 * 0.25

        if g1 < 95:
            decision = "REJECTED — Gate 1 failed (<95%)"
        elif g2 < 90:
            decision = "REJECTED — Gate 2 failed (<90%)"
        elif g3 < 85:
            decision = "CONDITIONAL PASS — Manual review required (Gate 3 <85%)"
        elif overall >= 90:
            decision = "APPROVED FOR LIVE TRADING (pending backtest validation)"
        else:
            decision = "PROFESSIONAL GRADE — Not institutional"

        return {
            "gate1_score": round(g1, 1),
            "gate1_details": g1d,
            "gate2_score": round(g2, 1),
            "gate2_details": g2d,
            "gate3_score": round(g3, 1),
            "gate3_details": g3d,
            "overall_score": round(overall, 1),
            "decision": decision,
            "missing_modules": missing,
            "gate1_pass": g1 >= 95,
            "gate2_pass": g2 >= 90,
            "gate3_pass": g3 >= 85,
        }


def main() -> None:
    scorer = GateScorer()
    result = scorer.run_full_evaluation()
    print("=" * 70)
    print("TRADING BOT FINAL EVALUATION SCORECARD")
    print("=" * 70)
    print(f"\nGATE 1 (Risk Management):  {result['gate1_score']}/100  {'PASS' if result['gate1_pass'] else 'FAIL'}")
    for k, v in result["gate1_details"].items():
        print(f"  • {k}: {v}/100")
    print(f"\nGATE 2 (Execution):        {result['gate2_score']}/100  {'PASS' if result['gate2_pass'] else 'FAIL'}")
    for k, v in result["gate2_details"].items():
        print(f"  • {k}: {v}/100")
    print(f"\nGATE 3 (Code Quality):     {result['gate3_score']}/100  {'PASS' if result['gate3_pass'] else 'FAIL'}")
    for k, v in result["gate3_details"].items():
        print(f"  • {k}: {v}/100")
    print(f"\nOVERALL SCORE:             {result['overall_score']}/100")
    print(f"DECISION:                  {result['decision']}")
    if result["missing_modules"]:
        print(f"\nMissing modules: {result['missing_modules']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
