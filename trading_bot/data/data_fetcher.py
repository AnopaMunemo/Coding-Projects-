"""Async market data fetching with caching and stale-data detection."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from trading_bot.broker.base_broker import BaseBroker
from trading_bot.config import EXECUTION
from trading_bot.infrastructure.retry_strategy import RetryStrategy
from trading_bot.logging.logger import StructuredLogger


class DataCache:
    def __init__(self, ttl_seconds: int = 60) -> None:
        self.ttl_seconds = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._cache.get(key)
        if entry is None:
            return None
        age = (datetime.now(timezone.utc) - entry["fetched_at"]).total_seconds()
        if age > self.ttl_seconds:
            del self._cache[key]
            return None
        return entry["data"]

    def set(self, key: str, data: Any) -> None:
        self._cache[key] = {"data": data, "fetched_at": datetime.now(timezone.utc)}


class DataFetcher:
    def __init__(
        self,
        broker: BaseBroker,
        retry: RetryStrategy,
        logger: StructuredLogger,
        cache_ttl: int = 60,
    ) -> None:
        self.broker = broker
        self.retry = retry
        self.logger = logger
        self.cache = DataCache(cache_ttl)

    def _is_stale(self, candles: List[Dict[str, Any]]) -> bool:
        if not candles:
            return True
        last_ts = candles[-1].get("timestamp", "")
        try:
            if "T" in last_ts:
                last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            else:
                last_dt = datetime.strptime(last_ts[:19], "%Y-%m-%d")
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - last_dt).total_seconds()
            return age > EXECUTION.STALE_DATA_THRESHOLD_SEC
        except (ValueError, TypeError):
            return True

    async def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> List[Dict[str, Any]]:
        cache_key = f"{symbol}:{timeframe}:{limit}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        candles = await self.retry.retry_with_backoff(
            lambda: self.broker.get_ohlcv(symbol, timeframe, limit),
            operation_name=f"fetch_ohlcv_{symbol}",
        )
        if self._is_stale(candles):
            self.logger.warning("Stale data detected — skipping signal generation", symbol=symbol)
            return []
        self.cache.set(cache_key, candles)
        return candles

    async def fetch_market_data(
        self, symbols: List[str], timeframe: str = "1h"
    ) -> Dict[str, List[Dict[str, Any]]]:
        tasks = [
            asyncio.wait_for(
                self.fetch_ohlcv(symbol, timeframe),
                timeout=EXECUTION.ORDER_TIMEOUT_SEC,
            )
            for symbol in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        data: Dict[str, List[Dict[str, Any]]] = {}
        for symbol, result in zip(symbols, results):
            if isinstance(result, Exception):
                self.logger.error(f"Failed to fetch {symbol}", error=str(result))
                data[symbol] = []
            else:
                data[symbol] = result
        return data

    async def fetch_latest(self, symbol: str) -> Dict[str, Any]:
        return await self.retry.retry_with_backoff(
            lambda: self.broker.get_latest_price(symbol),
            operation_name=f"fetch_latest_{symbol}",
        )
