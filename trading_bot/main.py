"""Production-grade swing trading bot orchestrator."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_bot.audit.audit_trail import AuditTrail
from trading_bot.backtest.engine import BacktestEngine
from trading_bot.backtest.overfitting_detection import OverfittingDetection
from trading_bot.backtest.profitability_validator import ProfitabilityValidator
from trading_bot.broker.investec_broker import InvestecBroker
from trading_bot.broker.mt5_broker import MT5BridgeBroker
from trading_bot.broker.paper_broker import PaperBroker
from trading_bot.config import DEFAULT_BOT, RISK
from trading_bot.data.data_fetcher import DataFetcher
from trading_bot.execution.executor import BotState, TradeExecutor
from trading_bot.execution.position_manager import PositionManager
from trading_bot.infrastructure.connection_monitor import ConnectionMonitor
from trading_bot.infrastructure.latency_monitor import LatencyMonitor
from trading_bot.infrastructure.retry_strategy import RetryStrategy
from trading_bot.infrastructure.state_manager import PersistentState
from trading_bot.logging.logger import StructuredLogger
from trading_bot.risk.circuit_breaker import CircuitBreaker
from trading_bot.risk.equity_monitor import EquityMonitor
from trading_bot.risk.risk_manager import RiskManager
from trading_bot.strategy.swing_trader import SwingTrader


class TradingBot:
    """Central coordinator with explicit state machine."""

    def __init__(self, config=DEFAULT_BOT) -> None:
        self.config = config
        self.logger = StructuredLogger(
            bot_name="atlas_swing_bot",
            log_dir=config.log_dir,
            telegram_token=config.telegram_bot_token,
            telegram_chat_id=config.telegram_chat_id,
        )
        self.state = PersistentState(config.state_file, self.logger)
        self.audit = AuditTrail(config.audit_dir)
        self.retry = RetryStrategy(self.logger)
        self.latency = LatencyMonitor(self.logger)
        self.risk_manager = RiskManager(self.logger)
        self.strategy = self._create_strategy()
        self.broker = self._create_broker()
        self.data_fetcher = DataFetcher(self.broker, self.retry, self.logger)
        self.executor = TradeExecutor(
            self.broker, self.risk_manager, self.data_fetcher,
            self.state, self.logger, self.latency,
        )
        self.position_manager = PositionManager(self.broker, self.state, self.logger)
        self.circuit_breaker = CircuitBreaker(self.state, self.logger)
        self.equity_monitor = EquityMonitor(self.state, self.logger)
        self.connection_monitor = ConnectionMonitor(self.broker, self.logger)
        self.connection_monitor.set_halt_callback(self._on_connection_lost)
        self._running = False
        self._last_daily_reconcile: Optional[datetime] = None

    def _create_broker(self):
        if self.config.broker_type == "investec":
            return InvestecBroker(
                client_id=os.environ.get("INVESTEC_CLIENT_ID", ""),
                client_secret=os.environ.get("INVESTEC_CLIENT_SECRET", ""),
                api_key=os.environ.get("INVESTEC_API_KEY", ""),
                account_id=os.environ.get("INVESTEC_ACCOUNT_ID", ""),
                logger=self.logger,
            )
        if self.config.broker_type == "mt5":
            return MT5BridgeBroker(
                starting_balance=self.config.starting_balance,
                logger=self.logger,
                dry_run=self.config.paper_mode,
            )
        return PaperBroker(self.config.starting_balance, self.logger)

    def _create_strategy(self):
        if getattr(self.config, "strategy_type", "swing") == "ensemble":
            from trading_bot.strategy.ensemble import EnsembleStrategy
            from trading_bot.learning.weight_store import WeightStore
            self.logger.info("Using EnsembleStrategy (full Desk strategy library)")
            return EnsembleStrategy(weight_store=WeightStore())
        return SwingTrader()

    async def _on_connection_lost(self, reason: str) -> None:
        self.state.set_trading_halted(True, reason=reason)
        await self.executor.kill_switch()

    async def startup(self) -> bool:
        self.logger.info("Bot starting up", paper_mode=self.config.paper_mode)
        await self.broker.authenticate()

        if not await self.state.reconcile_with_broker(self.broker):
            self.logger.critical("Startup reconciliation failed — bot halted")
            return False

        await self.connection_monitor.start()
        self.logger.info("Startup complete")
        return True

    async def _daily_reconcile_if_due(self) -> None:
        now = datetime.now(timezone.utc)
        if (
            now.hour == self.config.daily_reconcile_hour_utc
            and now.minute >= self.config.daily_reconcile_minute_utc
        ):
            if self._last_daily_reconcile is None or self._last_daily_reconcile.date() != now.date():
                await self.state.reconcile_with_broker(self.broker)
                self._last_daily_reconcile = now

    async def trading_loop_iteration(self) -> None:
        can_trade = (
            self.connection_monitor.can_trade()
            and not self.circuit_breaker.is_trading_halted()
            and not self.state.is_trading_halted()
        )

        balance_task = self.data_fetcher.retry.retry_with_backoff(
            self.broker.get_account_balance, operation_name="get_balance"
        )
        positions_task = self.broker.get_open_positions()
        data_task = self.data_fetcher.fetch_market_data(
            list(self.config.symbols), self.config.timeframe
        )

        balance, positions, market_data = await asyncio.gather(
            balance_task, positions_task, data_task
        )

        if self.circuit_breaker.check_loss_limits(balance):
            self.executor.bot_state = BotState.CIRCUIT_BROKEN
            return

        margin_ratio = await self.broker.get_margin_ratio()
        exposure = self.position_manager.get_exposure()
        if not self.equity_monitor.update_and_check(balance, margin_ratio, exposure, balance):
            self.executor.bot_state = BotState.CIRCUIT_BROKEN
            return

        signals = self.strategy.calculate_signals(market_data)
        await self.executor.execute_signals(signals, balance, market_data, can_trade)
        await self.position_manager.monitor_stops()
        await self._daily_reconcile_if_due()

    async def run(self) -> None:
        if not await self.startup():
            return
        self._running = True
        self.logger.info("Entering main trading loop", interval=self.config.loop_interval_sec)

        while self._running:
            try:
                await self.trading_loop_iteration()
            except Exception as exc:
                self.logger.critical(
                    "Unhandled main loop exception",
                    error=str(exc),
                    traceback=traceback.format_exc(),
                )
                await self.executor.kill_switch()
                break
            await asyncio.sleep(self.config.loop_interval_sec)

        await self.connection_monitor.stop()
        if hasattr(self.broker, "close"):
            await self.broker.close()

    async def run_backtest(self, start: str = "2022-01-01", end: str = "2024-01-01") -> dict:
        engine = BacktestEngine(
            self.strategy, self.risk_manager, self.logger, start, end
        )
        metrics = await engine.run(
            self.broker, list(self.config.symbols), self.config.starting_balance
        )
        validator = ProfitabilityValidator(self.logger)
        passed, _, failed = validator.validate_backtest_results(metrics)

        overfit = OverfittingDetection(self.logger)
        wf_results = await overfit.walk_forward_analysis(
            self.broker, self.config.symbols[0], start, end
        )
        mc = await overfit.monte_carlo_simulation(
            [t["pnl"] for t in engine.trades]
        )
        days = (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
        overfit.trade_frequency_check(metrics["total_trades"], days)

        self.logger.info("Backtest complete", passed=passed, monte_carlo=mc)
        return {
            "metrics": {k: v for k, v in metrics.items() if k != "equity_curve"},
            "passed": passed,
            "failed": failed,
            "walk_forward": wf_results,
            "monte_carlo": mc,
        }


def parse_args():
    parser = argparse.ArgumentParser(description="Atlas Production Swing Trading Bot")
    parser.add_argument("--mode", choices=["live", "paper", "backtest", "score"], default="paper")
    parser.add_argument("--broker", choices=["paper", "mt5", "investec"], default="paper")
    parser.add_argument("--strategy", choices=["swing", "ensemble"], default="swing",
                        help="swing = built-in SMA/RSI/MACD; ensemble = full Desk strategy library")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_BOT.symbols))
    parser.add_argument("--start", default="2022-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--balance", type=float, default=float(DEFAULT_BOT.starting_balance))
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()

    if args.mode == "score":
        from trading_bot.evaluation.gate_scorer import main as score_main
        score_main()
        return

    from dataclasses import replace
    config = replace(
        DEFAULT_BOT,
        symbols=tuple(args.symbols),
        starting_balance=Decimal(str(args.balance)),
        paper_mode=args.mode != "live",
        broker_type=args.broker if args.mode == "live" else ("paper" if args.broker == "paper" else args.broker),
        strategy_type=args.strategy,
    )

    bot = TradingBot(config)

    if args.mode == "backtest":
        result = await bot.run_backtest(args.start, args.end)
        print("\n=== BACKTEST RESULTS ===")
        for k, v in result["metrics"].items():
            if k != "equity_curve":
                print(f"  {k}: {v}")
        print(f"\n  Validation passed: {result['passed']}")
        return

    await bot.run()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
