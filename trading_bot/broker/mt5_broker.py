"""MT5 signal bridge — writes OCO signals for AtlasForexEA.mq5 execution."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from trading_bot.broker.paper_broker import PaperBroker
from trading_bot.logging.logger import StructuredLogger


_MT5_SYMBOL = {
    "EUR/USD": "EURUSD", "GBP/USD": "GBPUSD", "USD/JPY": "USDJPY",
    "XAU/USD": "XAUUSD", "XAUUSD": "XAUUSD", "GLD.JO": "GLD.JO",
    "AAPL": "AAPL",
}


class MT5BridgeBroker(PaperBroker):
    """
    Hybrid broker: validates in Python, exports signals to MT5 for live execution.
    Falls back to paper simulation when dry_run=True.
    """

    def __init__(
        self,
        starting_balance: Decimal,
        logger: StructuredLogger,
        signals_dir: str = "",
        dry_run: bool = True,
        magic_number: int = 20260601,
    ) -> None:
        super().__init__(starting_balance, logger)
        self.signals_dir = signals_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "..",
            "mt5_signals",
        )
        self.dry_run = dry_run
        self.magic_number = magic_number
        os.makedirs(self.signals_dir, exist_ok=True)

    def _mt5_symbol(self, symbol: str) -> str:
        return _MT5_SYMBOL.get(symbol, symbol.replace("/", ""))

    async def place_oco_order(
        self,
        symbol: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        stop_loss_price: Decimal,
        take_profit_price: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        signal = {
            "symbol": self._mt5_symbol(symbol),
            "pair": symbol,
            "direction": "LONG" if side.lower() in ("buy", "long") else "SHORT",
            "entry_price": float(entry_price),
            "stop_loss": float(stop_loss_price),
            "take_profit": float(take_profit_price) if take_profit_price else None,
            "lot_size": float(quantity),
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "magic_number": self.magic_number,
        }
        await self._export_signal(signal)

        if self.dry_run:
            self.logger.info("MT5 dry-run: signal exported, paper fill simulated", symbol=symbol)
            return await super().place_oco_order(
                symbol, side, quantity, entry_price, stop_loss_price, take_profit_price
            )

        self.logger.log_trade(
            "ORDER_PLACED",
            symbol=symbol,
            side=side,
            quantity=str(quantity),
            stop_loss=str(stop_loss_price),
            mode="mt5_bridge",
        )
        return {
            "order_id": f"mt5_{signal['generated_utc']}",
            "symbol": symbol,
            "status": "submitted_to_mt5",
            **signal,
        }

    async def _export_signal(self, signal: Dict[str, Any]) -> str:
        payload = {
            "schema_version": "2.0",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "magic_number": self.magic_number,
            "signal_count": 1,
            "signals": [signal],
        }
        path = os.path.join(self.signals_dir, "atlas_signals.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        os.replace(tmp, path)
        self.logger.info("Signal exported to MT5", path=path)
        return path
