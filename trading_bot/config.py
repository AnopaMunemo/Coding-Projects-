"""
Immutable production configuration — locked at deploy time.
Manual override during live trading is forbidden; changes require redeploy.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Tuple


@dataclass(frozen=True)
class RiskConfig:
    MAX_POSITION_SIZE: Decimal = Decimal("0.02")
    MAX_RISK_PER_TRADE: Decimal = Decimal("0.01")
    KELLY_FRACTION: Decimal = Decimal("0.25")
    MAX_DAILY_LOSS_PCT: Decimal = Decimal("0.05")
    MAX_HOURLY_LOSS_PCT: Decimal = Decimal("0.02")
    MAX_WEEKLY_LOSS_PCT: Decimal = Decimal("0.08")
    MAX_MONTHLY_LOSS_PCT: Decimal = Decimal("0.12")
    MAX_DRAWDOWN_PCT: Decimal = Decimal("0.20")
    MIN_MARGIN_RATIO: Decimal = Decimal("0.50")
    MAX_PORTFOLIO_EXPOSURE: Decimal = Decimal("0.60")
    ATR_STOP_MULTIPLIER: Decimal = Decimal("1.5")
    ATR_TP_MULTIPLIER: Decimal = Decimal("2.5")


@dataclass(frozen=True)
class ExecutionConfig:
    HEARTBEAT_INTERVAL_SEC: int = 30
    MAX_MISSED_HEARTBEATS: int = 2
    HEARTBEAT_TIMEOUT_SEC: float = 10.0
    DISCONNECT_HALT_SEC: int = 120
    MAX_RETRIES: int = 5
    BASE_RETRY_DELAY_SEC: float = 1.0
    MAX_RETRY_DELAY_SEC: float = 60.0
    CIRCUIT_BREAKER_FAILURES: int = 3
    CIRCUIT_BREAKER_COOLDOWN_SEC: int = 30
    STALE_DATA_THRESHOLD_SEC: int = 300
    ORDER_TIMEOUT_SEC: float = 5.0
    P50_LATENCY_TARGET_MS: float = 500.0
    P99_LATENCY_TARGET_MS: float = 2000.0
    RATE_LIMIT_REQUESTS: int = 100
    RATE_LIMIT_WINDOW_SEC: int = 60


@dataclass(frozen=True)
class BacktestConfig:
    MIN_PROFIT_FACTOR: Decimal = Decimal("1.5")
    MIN_SHARPE: Decimal = Decimal("0.5")
    MIN_WIN_RATE: Decimal = Decimal("0.35")
    MAX_DRAWDOWN: Decimal = Decimal("0.30")
    MIN_RETURN_DD_RATIO: Decimal = Decimal("2.0")
    MIN_TRADES_PER_100_DAYS: int = 5
    MAX_TRADES_PER_100_DAYS: int = 50
    WALK_FORWARD_TRAIN_DAYS: int = 252
    WALK_FORWARD_TEST_DAYS: int = 63
    MONTE_CARLO_SIMULATIONS: int = 1000


@dataclass(frozen=True)
class BotConfig:
    symbols: Tuple[str, ...] = ("GLD.JO", "AAPL", "XAUUSD")
    timeframe: str = "1h"
    strategy_type: str = "swing"   # "swing" | "ensemble" (full Desk library)
    loop_interval_sec: int = 60
    starting_balance: Decimal = Decimal("100000")
    state_file: str = "bot_state.json"
    log_dir: str = "logs"
    audit_dir: str = "audit"
    paper_mode: bool = True
    broker_type: str = "paper"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    daily_reconcile_hour_utc: int = 9
    daily_reconcile_minute_utc: int = 30


RISK = RiskConfig()
EXECUTION = ExecutionConfig()
BACKTEST = BacktestConfig()
DEFAULT_BOT = BotConfig()
