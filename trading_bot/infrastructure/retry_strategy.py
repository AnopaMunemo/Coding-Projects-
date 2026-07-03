"""Exponential backoff with circuit breaker for API resilience."""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

from trading_bot.config import EXECUTION
from trading_bot.logging.logger import StructuredLogger

T = TypeVar("T")


class CircuitBreakerOpen(Exception):
    pass


class RetryStrategy:
    MAX_RETRIES = EXECUTION.MAX_RETRIES
    BASE_DELAY = EXECUTION.BASE_RETRY_DELAY_SEC
    MAX_DELAY = EXECUTION.MAX_RETRY_DELAY_SEC
    CIRCUIT_BREAKER_THRESHOLD = EXECUTION.CIRCUIT_BREAKER_FAILURES
    CIRCUIT_BREAKER_COOLDOWN = EXECUTION.CIRCUIT_BREAKER_COOLDOWN_SEC

    def __init__(self, logger: StructuredLogger) -> None:
        self.logger = logger
        self.consecutive_failures = 0
        self.circuit_broken = False
        self.circuit_break_until: Optional[float] = None

    async def retry_with_backoff(
        self,
        coro_factory: Callable[[], Awaitable[T]],
        operation_name: str = "",
        timeout: float = 10.0,
        state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> T:
        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                if self.circuit_broken and self.circuit_break_until is not None:
                    elapsed = time.time() - self.circuit_break_until
                    if elapsed < self.CIRCUIT_BREAKER_COOLDOWN:
                        raise CircuitBreakerOpen(
                            f"Circuit breaker open for {operation_name}"
                        )
                    self.circuit_broken = False
                    self.consecutive_failures = 0

                result = await asyncio.wait_for(coro_factory(), timeout=timeout)
                self.consecutive_failures = 0
                return result

            except CircuitBreakerOpen:
                raise
            except (asyncio.TimeoutError, ConnectionError, OSError) as exc:
                await self._handle_failure(attempt, operation_name, exc, state_snapshot)
            except Exception as exc:
                await self._handle_failure(attempt, operation_name, exc, state_snapshot)

        raise RuntimeError(f"FINAL FAILURE: {operation_name} after {self.MAX_RETRIES} retries")

    async def _handle_failure(
        self,
        attempt: int,
        operation_name: str,
        exc: Exception,
        state_snapshot: Optional[Dict[str, Any]],
    ) -> None:
        self.consecutive_failures += 1
        context = {
            "operation": operation_name,
            "attempt": attempt,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "state": state_snapshot or {},
        }
        self.logger.error(
            f"Retry {attempt}/{self.MAX_RETRIES} for {operation_name}: {exc}",
            exc_info=True,
            **context,
        )

        if self.consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            self.circuit_broken = True
            self.circuit_break_until = time.time()
            self.logger.critical(
                f"CIRCUIT BREAKER OPENED: {operation_name}",
                consecutive_failures=self.consecutive_failures,
            )
            raise CircuitBreakerOpen(f"Circuit breaker opened for {operation_name}") from exc

        if attempt < self.MAX_RETRIES:
            delay = min(self.BASE_DELAY ** (attempt - 1), self.MAX_DELAY)
            await asyncio.sleep(delay)
        else:
            raise exc
