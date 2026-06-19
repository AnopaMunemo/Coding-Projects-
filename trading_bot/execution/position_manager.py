"""Position tracking and stop enforcement monitoring."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from trading_bot.broker.base_broker import BaseBroker
from trading_bot.infrastructure.state_manager import PersistentState
from trading_bot.logging.logger import StructuredLogger


class PositionManager:
    def __init__(
        self,
        broker: BaseBroker,
        state: PersistentState,
        logger: StructuredLogger,
    ) -> None:
        self.broker = broker
        self.state = state
        self.logger = logger

    async def monitor_stops(self) -> List[Dict[str, Any]]:
        """Check open positions against current prices; enforce stops if broker lacks OCO."""
        closed = []
        positions = await self.broker.get_open_positions()
        for pos in positions:
            symbol = pos["symbol"]
            price_data = await self.broker.get_latest_price(symbol)
            current = Decimal(str(price_data["close"]))
            stop = Decimal(str(pos.get("stop_loss", "0")))
            if stop <= 0:
                continue
            side = pos.get("side", "buy").lower()
            triggered = False
            if side in ("buy", "long") and current <= stop:
                triggered = True
            elif side in ("sell", "short") and current >= stop:
                triggered = True
            if triggered:
                self.logger.critical(
                    "Stop-loss triggered — emergency market close",
                    symbol=symbol,
                    current=str(current),
                    stop=str(stop),
                )
                result = await self.broker.close_position_at_market(symbol)
                self.state.close_position(symbol, str(current))
                closed.append(result)
        return closed

    def get_exposure(self) -> Decimal:
        total = Decimal("0")
        for sym, pos in self.state.state["open_positions"].items():
            entry = Decimal(str(pos["entry"]))
            qty = Decimal(str(pos["qty"]))
            total += entry * qty
        return total
