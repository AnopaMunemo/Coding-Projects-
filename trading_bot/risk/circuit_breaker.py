"""Daily/hourly/weekly/monthly loss circuit breakers with persistent halt state."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from trading_bot.config import RISK
from trading_bot.infrastructure.state_manager import PersistentState
from trading_bot.logging.logger import StructuredLogger


class CircuitBreaker:
    MAX_DAILY_LOSS_PCT = RISK.MAX_DAILY_LOSS_PCT
    MAX_HOURLY_LOSS_PCT = RISK.MAX_HOURLY_LOSS_PCT
    MAX_WEEKLY_LOSS_PCT = RISK.MAX_WEEKLY_LOSS_PCT
    MAX_MONTHLY_LOSS_PCT = RISK.MAX_MONTHLY_LOSS_PCT

    def __init__(self, state: PersistentState, logger: StructuredLogger) -> None:
        self.state = state
        self.logger = logger
        self._hour_start: Optional[datetime] = None
        self._day_start: Optional[datetime] = None

    def _ensure_period_baselines(self, current_balance: Decimal) -> None:
        now = datetime.now(timezone.utc)
        balance_str = str(current_balance)

        if self.state.state.get("day_start_balance") is None:
            self.state.state["day_start_balance"] = balance_str
        if self.state.state.get("hour_start_balance") is None:
            self.state.state["hour_start_balance"] = balance_str
        if self.state.state.get("week_start_balance") is None:
            self.state.state["week_start_balance"] = balance_str
        if self.state.state.get("month_start_balance") is None:
            self.state.state["month_start_balance"] = balance_str

        if self._hour_start is None or (now - self._hour_start).total_seconds() >= 3600:
            self.state.state["hour_start_balance"] = balance_str
            self._hour_start = now

        if self._day_start is None:
            self._day_start = now
        elif now.date() != self._day_start.date():
            self.state.state["day_start_balance"] = balance_str
            self._day_start = now

        if now.weekday() == 0 and (self._day_start is None or now.date() != self._day_start.date()):
            self.state.state["week_start_balance"] = balance_str

        if now.day == 1:
            self.state.state["month_start_balance"] = balance_str

        self.state.save()

    def _loss_pct(self, current: Decimal, start: Decimal) -> Decimal:
        if start <= 0:
            return Decimal("0")
        return (current - start) / start

    def check_loss_limits(self, current_balance: Decimal) -> bool:
        """
        Returns True if trading should STOP (circuit breaker triggered).
        """
        if self.state.is_trading_halted():
            return True

        self._ensure_period_baselines(current_balance)

        checks = [
            ("hourly", self.state.state["hour_start_balance"], self.MAX_HOURLY_LOSS_PCT),
            ("daily", self.state.state["day_start_balance"], self.MAX_DAILY_LOSS_PCT),
            ("weekly", self.state.state["week_start_balance"], self.MAX_WEEKLY_LOSS_PCT),
            ("monthly", self.state.state["month_start_balance"], self.MAX_MONTHLY_LOSS_PCT),
        ]

        for period, start_balance_str, limit in checks:
            start_balance = Decimal(str(start_balance_str))
            loss = self._loss_pct(current_balance, start_balance)
            if loss <= -limit:
                reason = f"{period}_loss_limit_exceeded:{loss:.4f}"
                self.logger.critical(
                    f"CIRCUIT BREAKER TRIGGERED: {period} loss {loss:.2%}",
                    limit=str(limit),
                )
                self.state.set_trading_halted(True, reason=reason)
                return True

        return False

    def is_trading_halted(self) -> bool:
        return self.state.is_trading_halted()
