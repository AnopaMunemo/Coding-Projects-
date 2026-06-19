"""Bridge existing forex_engine signals to MT5 production export."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, List


async def export_atlas_forex_signals(
    signals: List[Any],
    account_equity: float,
    signals_dir: str = "",
) -> str:
    if not signals_dir:
        signals_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "mt5_signals",
        )
    os.makedirs(signals_dir, exist_ok=True)

    exported = []
    for s in signals:
        exported.append({
            "symbol": getattr(s, "pair", "").replace("/", ""),
            "pair": getattr(s, "pair", ""),
            "direction": getattr(s, "direction", "NEUTRAL"),
            "entry_price": float(getattr(s, "entry_price", 0)),
            "stop_loss": float(getattr(s, "stop_loss", 0)),
            "take_profit": float(getattr(s, "take_profit", 0)),
            "lot_size": float(getattr(s, "lot_size", 0)),
            "confidence": float(getattr(s, "confidence", 0)),
            "recovery_mode": bool(getattr(s, "recovery_mode", False)),
        })

    payload = {
        "schema_version": "2.0",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "account_equity": account_equity,
        "signal_count": len(exported),
        "signals": exported,
    }
    path = os.path.join(signals_dir, "atlas_signals.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    os.replace(tmp, path)
    return path


async def run_desk_to_mt5(account_equity: float = 10000.0) -> str:
    """Pull forex signals from existing Atlas engine and export via production bot."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    from data_feed import DataFeedOrchestrator
    from forex_engine import run_forex_engine
    from trading_bot.logging.logger import StructuredLogger

    logger = StructuredLogger(bot_name="atlas_bridge")
    bundle = DataFeedOrchestrator().run()
    signals, _ = run_forex_engine(bundle["forex"], account_equity=account_equity)
    path = await export_atlas_forex_signals(signals, account_equity)
    logger.info("Desk signals exported", path=path, count=len(signals))
    return path
