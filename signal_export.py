"""
signal_export.py — Bridge between the Python signal engine and MetaTrader 5.

The Python desk does NOT place live orders. Instead it writes the current
ForexSignal feed to a JSON file in a location the MQL5 Expert Advisor polls.
The EA (mql5_bridge/AtlasForexEA.mq5) reads that file, validates freshness,
and executes orders with the supplied SL/TP and recovery lot size.

Why a file handshake?
  • Zero coupling — Python and MT5 run as separate processes (even separate
    machines via a shared/synced folder).
  • Auditable — every signal batch is timestamped and persisted.
  • Safe — the EA owns execution, risk checks, and the broker connection;
    Python only proposes.

Usage
─────
    from data_feed import DataFeedOrchestrator
    from forex_engine import run_forex_engine
    from signal_export import export_signals

    bundle = DataFeedOrchestrator().run()
    signals, _ = run_forex_engine(bundle["forex"], account_equity=300/18.5)
    export_signals(signals)        # writes ./mt5_signals/atlas_signals.json
"""

from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional

logger = logging.getLogger("signal_export")

# yfinance ticker → standard MT5 symbol (override per broker if needed)
_MT5_SYMBOL = {
    "EUR/USD": "EURUSD", "GBP/USD": "GBPUSD", "USD/JPY": "USDJPY",
    "USD/CHF": "USDCHF", "AUD/USD": "AUDUSD", "USD/CAD": "USDCAD",
    "NZD/USD": "NZDUSD", "EUR/GBP": "EURGBP", "EUR/JPY": "EURJPY",
    "GBP/JPY": "GBPJPY", "AUD/JPY": "AUDJPY", "CAD/JPY": "CADJPY",
    "XAU/USD": "XAUUSD", "XAG/USD": "XAGUSD",
}

DEFAULT_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mt5_signals")
DEFAULT_FILE = "atlas_signals.json"


def _signal_to_dict(s: Any) -> dict:
    """Serialise a ForexSignal into the EA's expected schema."""
    if is_dataclass(s):
        raw = asdict(s)
    else:                                   # tolerate plain objects
        raw = {k: getattr(s, k) for k in dir(s) if not k.startswith("_")}

    pair = raw.get("pair", "")
    return {
        "symbol":        _MT5_SYMBOL.get(pair, pair.replace("/", "")),
        "pair":          pair,
        "direction":     raw.get("direction"),        # LONG | SHORT
        "entry_price":   raw.get("entry_price"),
        "stop_loss":     raw.get("stop_loss"),
        "take_profit":   raw.get("take_profit"),
        "lot_size":      raw.get("lot_size"),
        "risk_reward":   raw.get("risk_reward"),
        "entry_from_utc": raw.get("entry_window_utc", [0, 0])[0],
        "entry_to_utc":   raw.get("entry_window_utc", [0, 0])[1],
        "exit_from_utc":  raw.get("exit_window_utc", [0, 0])[0],
        "exit_to_utc":    raw.get("exit_window_utc", [0, 0])[1],
        "confidence":    raw.get("confidence"),
        "regime":        raw.get("regime"),
        "recovery_mode": bool(raw.get("recovery_mode", False)),
        "recovery_deficit": raw.get("recovery_deficit", 0.0),
    }


def export_signals(
    signals: List[Any],
    out_dir: str = DEFAULT_DIR,
    filename: str = DEFAULT_FILE,
    account_equity: Optional[float] = None,
    magic_number: int = 20260601,
) -> str:
    """
    Write the signal batch to JSON atomically. Returns the file path.

    The EA should ignore any payload whose `generated_utc` is older than its
    own staleness threshold (default 15 min) to avoid acting on dead signals.
    """
    os.makedirs(out_dir, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "generated_utc":  datetime.now(timezone.utc).isoformat(),
        "magic_number":   magic_number,
        "account_equity": account_equity,
        "signal_count":   len(signals),
        "signals":        [_signal_to_dict(s) for s in signals],
    }

    path     = os.path.join(out_dir, filename)
    tmp_path = path + ".tmp"
    # Atomic write — the EA never sees a half-written file
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)
    os.replace(tmp_path, path)

    # Also emit a flat CSV — the simplest, most robust format for the MQL5 EA
    csv_path = os.path.join(out_dir, os.path.splitext(filename)[0] + ".csv")
    _write_csv(payload["signals"], csv_path, payload["generated_utc"])

    logger.info("Exported %d signal(s) → %s (+ %s)", len(signals), path, csv_path)
    return path


def _write_csv(signal_dicts: List[dict], path: str, generated_utc: str) -> None:
    """Flat CSV consumed by AtlasForexEA.mq5 (one row per signal)."""
    fields = [
        "symbol", "direction", "entry_price", "stop_loss", "take_profit",
        "lot_size", "risk_reward", "entry_from_utc", "entry_to_utc",
        "exit_from_utc", "exit_to_utc", "confidence", "recovery_mode",
        "recovery_deficit", "generated_utc",
    ]
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(fields)
        for s in signal_dicts:
            w.writerow([
                s.get("symbol"), s.get("direction"), s.get("entry_price"),
                s.get("stop_loss"), s.get("take_profit"), s.get("lot_size"),
                s.get("risk_reward"), s.get("entry_from_utc"), s.get("entry_to_utc"),
                s.get("exit_from_utc"), s.get("exit_to_utc"), s.get("confidence"),
                int(bool(s.get("recovery_mode"))), s.get("recovery_deficit"),
                generated_utc,
            ])
    os.replace(tmp, path)


if __name__ == "__main__":
    # Demo: export a couple of synthetic signals
    logging.basicConfig(level=logging.INFO)
    from dataclasses import dataclass

    @dataclass
    class _Stub:
        pair: str; direction: str; entry_price: float
        stop_loss: float; take_profit: float; risk_reward: float
        atr: float; entry_window_utc: tuple; exit_window_utc: tuple
        confidence: float; regime: str; lot_size: float
        dollar_risk: float; recovery_mode: bool; recovery_deficit: float

    demo = [
        _Stub("EUR/USD", "LONG", 1.0850, 1.0790, 1.0950, 1.67, 0.004,
              (8, 16), (18, 22), 0.78, "Bull", 0.02, 12.0, False, 0.0),
        _Stub("USD/JPY", "SHORT", 150.20, 151.10, 148.70, 1.67, 0.6,
              (8, 16), (18, 22), 0.66, "Bear", 0.01, 9.5, True, 18.4),
    ]
    p = export_signals(demo, account_equity=16.2)
    print("Wrote:", p)
    print(open(p).read())
