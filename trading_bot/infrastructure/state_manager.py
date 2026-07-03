"""Atomic state persistence with versioned schema and broker reconciliation."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from trading_bot.logging.logger import StructuredLogger


class PersistentState:
    SCHEMA_VERSION = 2

    def __init__(self, state_file: str, logger: StructuredLogger) -> None:
        self.state_file = Path(state_file)
        self.logger = logger
        self.state = self.load()

    def default_state(self) -> Dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "open_positions": {},
            "closed_trades": [],
            "pending_orders": {},
            "trading_halted": False,
            "halt_reason": "",
            "day_start_balance": None,
            "hour_start_balance": None,
            "week_start_balance": None,
            "month_start_balance": None,
            "peak_equity": None,
            "last_updated": None,
            "last_reconcile_utc": None,
        }

    def load(self) -> Dict[str, Any]:
        if not self.state_file.exists():
            return self.default_state()
        with open(self.state_file, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        if state.get("schema_version", 0) < self.SCHEMA_VERSION:
            state = self.migrate(state)
        return state

    def migrate(self, old_state: Dict[str, Any]) -> Dict[str, Any]:
        new_state = self.default_state()
        for key in new_state:
            if key in old_state:
                new_state[key] = old_state[key]
        new_state["schema_version"] = self.SCHEMA_VERSION
        self.logger.info("State schema migrated", from_version=old_state.get("schema_version", 0))
        return new_state

    def save(self) -> None:
        self.state["last_updated"] = datetime.now(timezone.utc).isoformat()
        temp_file = self.state_file.with_suffix(".tmp")
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_file, "w", encoding="utf-8") as fh:
            json.dump(self.state, fh, indent=2, default=str)
        temp_file.replace(self.state_file)  # Atomic rename — prevents corruption
        self.logger.debug("State saved", path=str(self.state_file))

    def set_trading_halted(self, halted: bool, reason: str = "") -> None:
        self.state["trading_halted"] = halted
        self.state["halt_reason"] = reason
        self.save()

    def is_trading_halted(self) -> bool:
        return bool(self.state.get("trading_halted", False))

    def add_open_position(
        self,
        symbol: str,
        entry: str,
        qty: str,
        stop_loss: str,
        order_id: str,
        side: str,
    ) -> None:
        self.state["open_positions"][symbol] = {
            "entry": entry,
            "qty": qty,
            "stop_loss": stop_loss,
            "order_id": order_id,
            "side": side,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save()

    def close_position(self, symbol: str, exit_price: str) -> None:
        if symbol not in self.state["open_positions"]:
            return
        pos = self.state["open_positions"].pop(symbol)
        entry = float(pos["entry"])
        qty = float(pos["qty"])
        exit_val = float(exit_price)
        side = pos.get("side", "buy")
        pnl = (exit_val - entry) * qty if side == "buy" else (entry - exit_val) * qty
        pos["exit_price"] = exit_price
        pos["closed_at"] = datetime.now(timezone.utc).isoformat()
        pos["pnl"] = str(pnl)
        self.state["closed_trades"].append(pos)
        self.save()

    async def reconcile_with_broker(self, broker: Any) -> bool:
        broker_positions = await broker.get_open_positions()
        internal_positions = self.state["open_positions"]

        broker_map = {p["symbol"]: p for p in broker_positions}
        internal_symbols: Set[str] = set(internal_positions.keys())
        broker_symbols: Set[str] = set(broker_map.keys())

        orphaned = broker_symbols - internal_symbols
        missing = internal_symbols - broker_symbols

        if orphaned or missing:
            self.logger.critical(
                "RECONCILIATION FAILED",
                orphaned=list(orphaned),
                missing=list(missing),
            )
            self.set_trading_halted(True, reason="position_reconciliation_failed")
            return False

        for symbol in internal_symbols:
            internal = internal_positions[symbol]
            broker_pos = broker_map[symbol]
            if str(internal.get("qty")) != str(broker_pos.get("quantity")):
                self.logger.critical(
                    "Quantity mismatch during reconciliation",
                    symbol=symbol,
                    internal_qty=internal.get("qty"),
                    broker_qty=broker_pos.get("quantity"),
                )
                self.set_trading_halted(True, reason="quantity_mismatch")
                return False

        self.state["last_reconcile_utc"] = datetime.now(timezone.utc).isoformat()
        self.save()
        self.logger.info("State reconciliation passed")
        return True
