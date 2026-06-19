"""Async order placement with OCO stops, latency tracking, and emergency handling."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional, Set

from trading_bot.broker.base_broker import BaseBroker
from trading_bot.config import EXECUTION
from trading_bot.data.data_fetcher import DataFetcher
from trading_bot.infrastructure.latency_monitor import LatencyMonitor
from trading_bot.infrastructure.state_manager import PersistentState
from trading_bot.logging.logger import StructuredLogger
from trading_bot.risk.risk_manager import RiskManager
from trading_bot.strategy.swing_trader import TradeSignal


class BotState(str, Enum):
    IDLE = "IDLE"
    PLACING_ORDER = "PLACING_ORDER"
    POSITION_OPEN = "POSITION_OPEN"
    CIRCUIT_BROKEN = "CIRCUIT_BROKEN"
    HALTED = "HALTED"


class TradeExecutor:
    def __init__(
        self,
        broker: BaseBroker,
        risk_manager: RiskManager,
        data_fetcher: DataFetcher,
        state: PersistentState,
        logger: StructuredLogger,
        latency_monitor: LatencyMonitor,
        min_confidence: float = 0.55,
    ) -> None:
        self.broker = broker
        self.risk_manager = risk_manager
        self.data_fetcher = data_fetcher
        self.state = state
        self.logger = logger
        self.latency = latency_monitor
        self.min_confidence = min_confidence
        self.bot_state = BotState.IDLE
        self._order_lock = asyncio.Lock()
        self._symbols_in_flight: Set[str] = set()

    async def execute_signal(
        self,
        signal: TradeSignal,
        account_balance: Decimal,
        candles: list,
        can_trade: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not can_trade or self.state.is_trading_halted():
            return None
        if signal.direction == "HOLD" or signal.confidence < self.min_confidence:
            return None
        if signal.symbol in self.state.state["open_positions"]:
            return None

        async with self._order_lock:
            if signal.symbol in self._symbols_in_flight:
                self.logger.warning("Duplicate order prevented", symbol=signal.symbol)
                return None
            self._symbols_in_flight.add(signal.symbol)

        order_id = None
        try:
            self.bot_state = BotState.PLACING_ORDER
            self.latency.start()

            price_data = await self.data_fetcher.fetch_latest(signal.symbol)
            entry = Decimal(str(price_data["close"]))

            from trading_bot.strategy.swing_trader import SwingTrader
            atr_val = SwingTrader.get_atr(candles)
            if atr_val <= 0:
                self.logger.warning("Zero ATR — rejecting trade", symbol=signal.symbol)
                return None

            stop_loss, take_profit, qty, side = self.risk_manager.calculate_position(
                entry=entry,
                atr=atr_val,
                account_balance=account_balance,
                signal=signal.direction,
            )

            if qty <= 0:
                return None

            if not self.risk_manager.validate_trade_risk(qty, entry, stop_loss, account_balance):
                return None

            try:
                order = await asyncio.wait_for(
                    self.broker.place_oco_order(
                        symbol=signal.symbol,
                        side=side,
                        quantity=qty,
                        entry_price=entry,
                        stop_loss_price=stop_loss,
                        take_profit_price=take_profit,
                    ),
                    timeout=EXECUTION.ORDER_TIMEOUT_SEC,
                )
                order_id = order.get("order_id", order.get("orderId", "unknown"))
                self.latency.end(signal.symbol)

                self.state.add_open_position(
                    symbol=signal.symbol,
                    entry=str(entry),
                    qty=str(qty),
                    stop_loss=str(stop_loss),
                    order_id=str(order_id),
                    side=side,
                )
                self.logger.log_trade(
                    "ORDER_PLACED",
                    symbol=signal.symbol,
                    side=side,
                    quantity=str(qty),
                    entry=str(entry),
                    stop_loss=str(stop_loss),
                    take_profit=str(take_profit),
                    order_id=str(order_id),
                    reason=signal.reason,
                )
                self.bot_state = BotState.POSITION_OPEN
                return order

            except asyncio.TimeoutError:
                self.logger.error("Order placement timed out", symbol=signal.symbol)
                raise
            except ConnectionError as exc:
                self.logger.error("Connection failed during order", error=str(exc))
                raise

        except (ValueError, ConnectionError, asyncio.TimeoutError) as exc:
            self.logger.critical(
                f"TRADE EXECUTION FAILED: {signal.symbol}",
                direction=signal.direction,
                error=str(exc),
                exc_info=True,
            )
            if order_id:
                try:
                    await self.broker.cancel_order(str(order_id))
                except Exception as cancel_exc:
                    self.logger.critical("Failed to cancel order after failure", error=str(cancel_exc))
            await self._emergency_stop_fallback(signal.symbol)
            raise
        finally:
            self._symbols_in_flight.discard(signal.symbol)
            if not self.state.state["open_positions"]:
                self.bot_state = BotState.IDLE

    async def _emergency_stop_fallback(self, symbol: str) -> None:
        """If exchange stop fails, close at market immediately."""
        try:
            result = await self.broker.close_position_at_market(symbol)
            self.logger.critical("EMERGENCY MARKET CLOSE", symbol=symbol, result=result)
        except Exception as exc:
            self.logger.critical("Emergency close failed", symbol=symbol, error=str(exc))

    async def execute_signals(
        self,
        signals: Dict[str, TradeSignal],
        balance: Decimal,
        market_data: dict,
        can_trade: bool = True,
    ) -> list:
        tasks = []
        for symbol, signal in signals.items():
            candles = market_data.get(symbol, [])
            tasks.append(self.execute_signal(signal, balance, candles, can_trade))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        orders = []
        for result in results:
            if isinstance(result, Exception):
                self.logger.error("Signal execution error", error=str(result))
            elif result is not None:
                orders.append(result)
        return orders

    async def kill_switch(self) -> None:
        self.logger.critical("KILL SWITCH ACTIVATED")
        self.bot_state = BotState.HALTED
        try:
            await self.broker.cancel_all_orders()
            positions = await self.broker.get_open_positions()
            for pos in positions:
                await self.broker.close_position_at_market(pos["symbol"])
        except Exception as exc:
            self.logger.critical("Kill switch error", error=str(exc), exc_info=True)
        self.state.set_trading_halted(True, reason="kill_switch")
