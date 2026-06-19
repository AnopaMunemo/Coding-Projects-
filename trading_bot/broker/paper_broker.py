"""Paper broker for backtesting and dry-run with Decimal precision."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from trading_bot.broker.base_broker import BaseBroker
from trading_bot.logging.logger import StructuredLogger


class PaperBroker(BaseBroker):
    def __init__(
        self,
        starting_balance: Decimal,
        logger: StructuredLogger,
    ) -> None:
        self.balance = starting_balance
        self.peak_balance = starting_balance
        self.logger = logger
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self._connected = True
        self._price_cache: Dict[str, Decimal] = {}

    async def ping(self) -> bool:
        if not self._connected:
            raise ConnectionError("Paper broker disconnected")
        return True

    async def authenticate(self) -> None:
        self.logger.info("Paper broker authenticated")

    async def get_account_balance(self) -> Decimal:
        return self.balance

    async def get_margin_ratio(self) -> Decimal:
        used = sum(
            Decimal(str(p["quantity"])) * Decimal(str(p["entry_price"]))
            for p in self.positions.values()
        )
        if self.balance <= 0:
            return Decimal("0")
        available = self.balance - used
        return max(Decimal("0"), available / self.balance)

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        return [
            {
                "symbol": sym,
                "quantity": str(pos["quantity"]),
                "entry_price": str(pos["entry_price"]),
                "side": pos["side"],
                "stop_loss": str(pos.get("stop_loss", "0")),
            }
            for sym, pos in self.positions.items()
        ]

    async def get_open_orders(self) -> List[Dict[str, Any]]:
        return list(self.orders.values())

    async def _fetch_yfinance(self, symbol: str, period: str = "60d", interval: str = "1h") -> pd.DataFrame:
        import yfinance as yf

        ticker = symbol
        if symbol == "XAUUSD":
            ticker = "GC=F"
        df = await asyncio.to_thread(
            lambda: yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.title)
        return df.dropna()

    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> List[Dict[str, Any]]:
        interval_map = {"1h": "1h", "1d": "1d", "15m": "15m"}
        interval = interval_map.get(timeframe, "1h")
        period = "730d" if interval == "1h" else "5y"
        df = await self._fetch_yfinance(symbol, period=period, interval=interval)
        if df.empty:
            return []
        df = df.tail(limit)
        candles = []
        for idx, row in df.iterrows():
            close = Decimal(str(round(float(row["Close"]), 6)))
            self._price_cache[symbol] = close
            candles.append({
                "timestamp": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
                "open": str(Decimal(str(round(float(row["Open"]), 6)))),
                "high": str(Decimal(str(round(float(row["High"]), 6)))),
                "low": str(Decimal(str(round(float(row["Low"]), 6)))),
                "close": str(close),
                "volume": str(int(row.get("Volume", 0) or 0)),
            })
        return candles

    async def get_latest_price(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self._price_cache:
            candles = await self.get_ohlcv(symbol, limit=5)
            if not candles:
                raise ValueError(f"No price data for {symbol}")
        price = self._price_cache[symbol]
        return {"symbol": symbol, "close": str(price), "timestamp": datetime.now(timezone.utc).isoformat()}

    async def place_oco_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        order_id = str(uuid.uuid4())
        cost = quantity * entry_price
        if side.lower() in ("buy", "long") and cost > self.balance:
            raise ValueError(f"Insufficient balance: need {cost}, have {self.balance}")

        order = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": str(quantity),
            "entry_price": str(entry_price),
            "stop_loss": str(stop_loss_price),
            "take_profit": str(take_profit_price) if take_profit_price else None,
            "status": "filled",
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }
        self.orders[order_id] = order
        self.positions[symbol] = {
            "quantity": str(quantity),
            "entry_price": str(entry_price),
            "side": side,
            "stop_loss": str(stop_loss_price),
            "take_profit": str(take_profit_price) if take_profit_price else None,
            "order_id": order_id,
        }
        if side.lower() in ("buy", "long"):
            self.balance -= cost
        self.logger.log_trade(
            "ORDER_FILLED",
            order_id=order_id,
            symbol=symbol,
            side=side,
            quantity=str(quantity),
            price=str(entry_price),
            stop_loss=str(stop_loss_price),
        )
        return order

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id]["status"] = "cancelled"
            self.logger.log_trade("ORDER_CANCELLED", order_id=order_id)
            return True
        return False

    async def cancel_all_orders(self) -> int:
        count = 0
        for oid in list(self.orders.keys()):
            if await self.cancel_order(oid):
                count += 1
        return count

    async def close_position_at_market(self, symbol: str) -> Dict[str, Any]:
        if symbol not in self.positions:
            return {"status": "no_position"}
        pos = self.positions.pop(symbol)
        price_data = await self.get_latest_price(symbol)
        exit_price = Decimal(str(price_data["close"]))
        qty = Decimal(str(pos["quantity"]))
        entry = Decimal(str(pos["entry_price"]))
        if pos["side"].lower() in ("buy", "long"):
            pnl = (exit_price - entry) * qty
            self.balance += exit_price * qty
        else:
            pnl = (entry - exit_price) * qty
            self.balance += pnl
        self.logger.log_trade(
            "POSITION_CLOSED",
            symbol=symbol,
            exit_price=str(exit_price),
            pnl=str(pnl),
        )
        return {"symbol": symbol, "exit_price": str(exit_price), "pnl": str(pnl)}

    async def get_historical_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "1h",
    ) -> List[Dict[str, Any]]:
        import yfinance as yf

        ticker = "GC=F" if symbol == "XAUUSD" else symbol
        df = await asyncio.to_thread(
            lambda: yf.download(ticker, start=start_date, end=end_date, interval="1d", progress=False, auto_adjust=True)
        )
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.title).dropna()
        return [
            {
                "timestamp": idx.isoformat() if hasattr(idx, "isoformat") else str(idx),
                "open": str(Decimal(str(round(float(row["Open"]), 6)))),
                "high": str(Decimal(str(round(float(row["High"]), 6)))),
                "low": str(Decimal(str(round(float(row["Low"]), 6)))),
                "close": str(Decimal(str(round(float(row["Close"]), 6)))),
                "volume": str(int(row.get("Volume", 0) or 0)),
            }
            for idx, row in df.iterrows()
        ]

    def simulate_stop_checks(self, symbol: str, current_price: Decimal) -> Optional[str]:
        """Check if stop or TP hit — returns 'stop' or 'tp' or None."""
        if symbol not in self.positions:
            return None
        pos = self.positions[symbol]
        sl = Decimal(str(pos["stop_loss"]))
        tp = Decimal(str(pos["take_profit"])) if pos.get("take_profit") else None
        side = pos["side"].lower()
        if side in ("buy", "long"):
            if current_price <= sl:
                return "stop"
            if tp and current_price >= tp:
                return "tp"
        else:
            if current_price >= sl:
                return "stop"
            if tp and current_price <= tp:
                return "tp"
        return None
