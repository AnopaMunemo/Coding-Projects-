"""Abstract broker interface — swap Investec, MT5, paper without changing execution."""
from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, Dict, List, Optional


class BaseBroker(ABC):
    @abstractmethod
    async def ping(self) -> bool:
        ...

    @abstractmethod
    async def authenticate(self) -> None:
        ...

    @abstractmethod
    async def get_account_balance(self) -> Decimal:
        ...

    @abstractmethod
    async def get_margin_ratio(self) -> Decimal:
        ...

    @abstractmethod
    async def get_open_positions(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    async def get_open_orders(self) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    async def get_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 200
    ) -> List[Dict[str, Any]]:
        ...

    @abstractmethod
    async def get_latest_price(self, symbol: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def place_oco_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    async def cancel_all_orders(self) -> int:
        ...

    @abstractmethod
    async def close_position_at_market(self, symbol: str) -> Dict[str, Any]:
        ...

    @abstractmethod
    async def get_historical_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "1h",
    ) -> List[Dict[str, Any]]:
        ...
