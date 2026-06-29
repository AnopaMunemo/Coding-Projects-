"""Investec Programmable Banking API adapter with OAuth and rate limiting."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

import aiohttp

from trading_bot.broker.base_broker import BaseBroker
from trading_bot.config import EXECUTION
from trading_bot.infrastructure.rate_limiter import RateLimiter
from trading_bot.logging.logger import StructuredLogger


class InvestecBroker(BaseBroker):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        api_key: str,
        account_id: str,
        logger: StructuredLogger,
        base_url: str = "https://openapi.investec.com",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_key = api_key
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        self.logger = logger
        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.rate_limiter = RateLimiter(
            EXECUTION.RATE_LIMIT_REQUESTS,
            EXECUTION.RATE_LIMIT_WINDOW_SEC,
        )
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=EXECUTION.ORDER_TIMEOUT_SEC)
            )
        return self._session

    async def ping(self) -> bool:
        await self._ensure_token_valid()
        session = await self._get_session()
        await self.rate_limiter.acquire()
        async with session.get(
            f"{self.base_url}/health",
            headers=self._auth_headers(),
        ) as resp:
            return resp.status == 200

    async def authenticate(self) -> None:
        session = await self._get_session()
        await self.rate_limiter.acquire()
        async with session.post(
            f"{self.base_url}/oauth/token",
            json={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            },
            headers={"x-api-key": self.api_key},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise ConnectionError(f"Investec auth failed: {resp.status} {text}")
            data = await resp.json()
            self.access_token = data["access_token"]
            expires_in = int(data.get("expires_in", 3600))
            self.token_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
            self.logger.info("Authenticated with Investec")

    async def _ensure_token_valid(self) -> None:
        if self.access_token is None or self.token_expiry is None:
            await self.authenticate()
            return
        if datetime.utcnow() > self.token_expiry - timedelta(minutes=5):
            await self.authenticate()

    def _auth_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        await self._ensure_token_valid()
        await self.rate_limiter.acquire()
        session = await self._get_session()
        url = f"{self.base_url}{endpoint}"
        async with session.request(method, url, headers=self._auth_headers(), **kwargs) as resp:
            if resp.status == 429:
                retry_after = int(resp.headers.get("Retry-After", "60"))
                self.logger.warning("Rate limited by Investec", retry_after=retry_after)
                await asyncio.sleep(retry_after)
                return await self._request(method, endpoint, **kwargs)
            if resp.status >= 400:
                text = await resp.text()
                raise ConnectionError(f"Investec API error {resp.status}: {text}")
            return await resp.json()

    async def get_account_balance(self) -> Decimal:
        data = await self._request("GET", f"/za/v1/accounts/{self.account_id}/balance")
        balance = data.get("data", {}).get("availableBalance", data.get("balance", 0))
        return Decimal(str(balance))

    async def get_margin_ratio(self) -> Decimal:
        data = await self._request("GET", f"/za/v1/accounts/{self.account_id}")
        equity = Decimal(str(data.get("equity", data.get("balance", 0))))
        margin_used = Decimal(str(data.get("marginUsed", 0)))
        if equity <= 0:
            return Decimal("0")
        return (equity - margin_used) / equity

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", f"/za/v1/accounts/{self.account_id}/positions")
        positions = data.get("data", data.get("positions", []))
        return [
            {
                "symbol": p.get("symbol", p.get("instrumentId")),
                "quantity": str(p.get("quantity", p.get("units", 0))),
                "entry_price": str(p.get("averagePrice", p.get("entryPrice", 0))),
                "side": p.get("side", "buy"),
            }
            for p in positions
        ]

    async def get_open_orders(self) -> List[Dict[str, Any]]:
        data = await self._request("GET", f"/za/v1/accounts/{self.account_id}/orders")
        return data.get("data", data.get("orders", []))

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> List[Dict[str, Any]]:
        data = await self._request(
            "GET",
            f"/za/v1/market-data/{symbol}/ohlcv",
            params={"interval": timeframe, "limit": limit},
        )
        candles = data.get("data", data.get("candles", []))
        return [
            {
                "timestamp": c.get("timestamp"),
                "open": str(Decimal(str(c["open"]))),
                "high": str(Decimal(str(c["high"]))),
                "low": str(Decimal(str(c["low"]))),
                "close": str(Decimal(str(c["close"]))),
                "volume": str(c.get("volume", 0)),
            }
            for c in candles
        ]

    async def get_latest_price(self, symbol: str) -> Dict[str, Any]:
        data = await self._request("GET", f"/za/v1/market-data/{symbol}/quote")
        quote = data.get("data", data)
        return {
            "symbol": symbol,
            "close": str(Decimal(str(quote.get("last", quote.get("close", 0))))),
            "timestamp": quote.get("timestamp", datetime.utcnow().isoformat()),
        }

    async def place_oco_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "accountId": self.account_id,
            "symbol": symbol,
            "side": side.upper(),
            "quantity": str(quantity),
            "orderType": "LIMIT",
            "price": str(entry_price),
            "stopLoss": str(stop_loss_price),
            "timeInForce": "GTC",
        }
        if take_profit_price:
            payload["takeProfit"] = str(take_profit_price)
        data = await self._request(
            "POST",
            f"/za/v1/accounts/{self.account_id}/orders/oco",
            json=payload,
        )
        order = data.get("data", data)
        self.logger.log_trade(
            "ORDER_PLACED",
            order_id=order.get("orderId"),
            symbol=symbol,
            side=side,
            quantity=str(quantity),
            stop_loss=str(stop_loss_price),
        )
        return order

    async def cancel_order(self, order_id: str) -> bool:
        await self._request(
            "DELETE",
            f"/za/v1/accounts/{self.account_id}/orders/{order_id}",
        )
        self.logger.log_trade("ORDER_CANCELLED", order_id=order_id)
        return True

    async def cancel_all_orders(self) -> int:
        orders = await self.get_open_orders()
        count = 0
        for order in orders:
            oid = order.get("orderId", order.get("id"))
            if oid and await self.cancel_order(str(oid)):
                count += 1
        return count

    async def close_position_at_market(self, symbol: str) -> Dict[str, Any]:
        positions = await self.get_open_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if not pos:
            return {"status": "no_position"}
        side = "sell" if pos.get("side", "buy").lower() in ("buy", "long") else "buy"
        qty = Decimal(str(pos["quantity"]))
        data = await self._request(
            "POST",
            f"/za/v1/accounts/{self.account_id}/orders",
            json={
                "symbol": symbol,
                "side": side.upper(),
                "quantity": str(qty),
                "orderType": "MARKET",
            },
        )
        self.logger.log_trade("EMERGENCY_CLOSE", symbol=symbol, side=side)
        return data.get("data", data)

    async def get_historical_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "1h",
    ) -> List[Dict[str, Any]]:
        data = await self._request(
            "GET",
            f"/za/v1/market-data/{symbol}/history",
            params={"start": start_date, "end": end_date, "interval": timeframe},
        )
        candles = data.get("data", [])
        return [
            {
                "timestamp": c["timestamp"],
                "open": str(Decimal(str(c["open"]))),
                "high": str(Decimal(str(c["high"]))),
                "low": str(Decimal(str(c["low"]))),
                "close": str(Decimal(str(c["close"]))),
                "volume": str(c.get("volume", 0)),
            }
            for c in candles
        ]

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
