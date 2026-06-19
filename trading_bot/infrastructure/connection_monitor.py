"""Broker connection heartbeat and emergency shutdown."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Optional

from trading_bot.config import EXECUTION
from trading_bot.logging.logger import StructuredLogger


class ConnectionMonitor:
    HEARTBEAT_INTERVAL = EXECUTION.HEARTBEAT_INTERVAL_SEC
    MAX_MISSED_BEATS = EXECUTION.MAX_MISSED_HEARTBEATS
    HEARTBEAT_TIMEOUT = EXECUTION.HEARTBEAT_TIMEOUT_SEC
    DISCONNECT_HALT_SEC = EXECUTION.DISCONNECT_HALT_SEC

    def __init__(self, broker: Any, logger: StructuredLogger) -> None:
        self.broker = broker
        self.logger = logger
        self.last_successful_ping: Optional[datetime] = None
        self.is_connected = True
        self._task: Optional[asyncio.Task] = None
        self._halt_callback: Optional[Any] = None

    def set_halt_callback(self, callback: Any) -> None:
        self._halt_callback = callback

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.heartbeat_loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def heartbeat_loop(self) -> None:
        missed_beats = 0
        while True:
            try:
                await asyncio.wait_for(self.broker.ping(), timeout=self.HEARTBEAT_TIMEOUT)
                self.last_successful_ping = datetime.utcnow()
                missed_beats = 0
                self.is_connected = True
            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                missed_beats += 1
                self.logger.warning(
                    "Heartbeat failed",
                    missed=missed_beats,
                    max_missed=self.MAX_MISSED_BEATS,
                    error=str(exc),
                )
                if missed_beats >= self.MAX_MISSED_BEATS:
                    self.is_connected = False
                    self.logger.critical("CONNECTION LOST - HALTING TRADING")
                    await self.emergency_shutdown()
            except Exception as exc:
                missed_beats += 1
                self.logger.error("Heartbeat unexpected error", error=str(exc), exc_info=True)
                if missed_beats >= self.MAX_MISSED_BEATS:
                    self.is_connected = False
                    await self.emergency_shutdown()

            await asyncio.sleep(self.HEARTBEAT_INTERVAL)

    async def emergency_shutdown(self) -> None:
        self.logger.critical("EMERGENCY SHUTDOWN INITIATED")
        try:
            await self.broker.cancel_all_orders()
            positions = await self.broker.get_open_positions()
            self.logger.critical("Open positions at shutdown", positions=positions)
            if self._halt_callback:
                await self._halt_callback("connection_lost")
        except Exception as exc:
            self.logger.critical("Emergency shutdown failed", error=str(exc), exc_info=True)

    def can_trade(self) -> bool:
        if not self.is_connected:
            return False
        if self.last_successful_ping is None:
            return True
        elapsed = (datetime.utcnow() - self.last_successful_ping).total_seconds()
        return elapsed < self.DISCONNECT_HALT_SEC
