"""Real-time drawdown and margin monitoring with hourly equity logging."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from trading_bot.config import RISK
from trading_bot.infrastructure.state_manager import PersistentState
from trading_bot.logging.logger import StructuredLogger


class EquityMonitor:
    MAX_DRAWDOWN_PCT = RISK.MAX_DRAWDOWN_PCT
    MIN_MARGIN_RATIO = RISK.MIN_MARGIN_RATIO
    MAX_PORTFOLIO_EXPOSURE = RISK.MAX_PORTFOLIO_EXPOSURE

    def __init__(self, state: PersistentState, logger: StructuredLogger) -> None:
        self.state = state
        self.logger = logger
        self._last_hourly_log: Optional[datetime] = None
        self.equity_curve: list = []

    def update_and_check(
        self,
        current_equity: Decimal,
        current_margin_ratio: Decimal,
        total_exposure: Decimal,
        account_balance: Decimal,
    ) -> bool:
        """
        Returns True if OK to continue trading, False if limits exceeded.
        """
        peak_str = self.state.state.get("peak_equity")
        if peak_str is None:
            self.state.state["peak_equity"] = str(current_equity)
            self.state.save()
            peak = current_equity
        else:
            peak = Decimal(str(peak_str))
            if current_equity > peak:
                self.state.state["peak_equity"] = str(current_equity)
                self.state.save()
                peak = current_equity

        drawdown = (current_equity - peak) / peak if peak > 0 else Decimal("0")

        if drawdown <= -self.MAX_DRAWDOWN_PCT:
            self.logger.critical(
                "DRAWDOWN EXCEEDED",
                drawdown=f"{drawdown:.2%}",
                threshold=str(self.MAX_DRAWDOWN_PCT),
            )
            self.state.set_trading_halted(True, reason=f"max_drawdown:{drawdown}")
            return False

        if current_margin_ratio < self.MIN_MARGIN_RATIO:
            self.logger.critical(
                "MARGIN CRITICAL",
                margin_ratio=f"{current_margin_ratio:.2%}",
                threshold=str(self.MIN_MARGIN_RATIO),
            )
            self.state.set_trading_halted(True, reason="margin_critical")
            return False

        if account_balance > 0:
            exposure_ratio = total_exposure / account_balance
            if exposure_ratio > self.MAX_PORTFOLIO_EXPOSURE:
                self.logger.warning(
                    "Portfolio exposure limit reached",
                    exposure=f"{exposure_ratio:.2%}",
                    max=str(self.MAX_PORTFOLIO_EXPOSURE),
                )
                return False

        now = datetime.now(timezone.utc)
        self.equity_curve.append({
            "timestamp": now.isoformat(),
            "equity": str(current_equity),
            "drawdown": str(drawdown),
            "margin_ratio": str(current_margin_ratio),
        })

        if self._last_hourly_log is None or (now - self._last_hourly_log).total_seconds() >= 3600:
            self.logger.info(
                "Hourly equity snapshot",
                equity=str(current_equity),
                drawdown=f"{drawdown:.2%}",
                margin_ratio=f"{current_margin_ratio:.2%}",
            )
            self._last_hourly_log = now

        if drawdown <= -(self.MAX_DRAWDOWN_PCT * Decimal("0.75")):
            self.logger.warning(
                "Liquidation risk alert — approaching max drawdown",
                drawdown=f"{drawdown:.2%}",
            )

        return True
