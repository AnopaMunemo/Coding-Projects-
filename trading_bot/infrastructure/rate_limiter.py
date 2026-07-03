"""Token-bucket rate limiter for broker API compliance."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.tokens = Decimal(str(max_requests))
        self.last_refill = datetime.utcnow()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while self.tokens <= 0:
                elapsed = (datetime.utcnow() - self.last_refill).total_seconds()
                refill_rate = Decimal(str(self.max_requests)) / Decimal(str(self.window_seconds))
                self.tokens = min(
                    Decimal(str(self.max_requests)),
                    self.tokens + refill_rate * Decimal(str(elapsed)),
                )
                self.last_refill = datetime.utcnow()
                if self.tokens <= 0:
                    await asyncio.sleep(0.1)
            self.tokens -= Decimal("1")
