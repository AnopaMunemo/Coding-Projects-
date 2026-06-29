"""Hard-coded position sizing with fractional Kelly — no runtime overrides."""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Dict, Optional, Tuple

from trading_bot.config import RISK
from trading_bot.logging.logger import StructuredLogger


class RiskManager:
    MAX_POSITION_SIZE = RISK.MAX_POSITION_SIZE
    MAX_RISK_PER_TRADE = RISK.MAX_RISK_PER_TRADE
    KELLY_FRACTION = RISK.KELLY_FRACTION
    ATR_STOP_MULTIPLIER = RISK.ATR_STOP_MULTIPLIER
    ATR_TP_MULTIPLIER = RISK.ATR_TP_MULTIPLIER

    def __init__(self, logger: StructuredLogger) -> None:
        self.logger = logger

    def calculate_stop_loss(
        self,
        entry: Decimal,
        atr: Decimal,
        side: str,
    ) -> Decimal:
        distance = atr * self.ATR_STOP_MULTIPLIER
        if side.lower() in ("buy", "long"):
            return entry - distance
        return entry + distance

    def calculate_take_profit(
        self,
        entry: Decimal,
        atr: Decimal,
        side: str,
    ) -> Decimal:
        distance = atr * self.ATR_TP_MULTIPLIER
        if side.lower() in ("buy", "long"):
            return entry + distance
        return entry - distance

    def fractional_kelly_size(
        self,
        win_rate: Decimal,
        avg_win: Decimal,
        avg_loss: Decimal,
        account_balance: Decimal,
    ) -> Decimal:
        if avg_loss <= 0 or win_rate <= 0 or win_rate >= 1:
            return Decimal("0")
        win_loss_ratio = avg_win / avg_loss
        kelly = win_rate - ((Decimal("1") - win_rate) / win_loss_ratio)
        kelly = max(Decimal("0"), kelly)
        return kelly * self.KELLY_FRACTION * account_balance

    def calculate_position_size(
        self,
        account_balance: Decimal,
        entry: Decimal,
        stop_loss: Decimal,
        win_rate: Decimal = Decimal("0.45"),
        avg_win: Decimal = Decimal("1.5"),
        avg_loss: Decimal = Decimal("1.0"),
    ) -> Decimal:
        """
        Position size calculated at order time with hard caps.
        Returns quantity (shares/units), never exceeds MAX_POSITION_SIZE.
        """
        if account_balance <= 0:
            return Decimal("0")

        position_ticks = abs(entry - stop_loss)
        if position_ticks == 0:
            self.logger.error("Zero stop distance — rejecting trade")
            return Decimal("0")

        risk_amount = account_balance * self.MAX_RISK_PER_TRADE
        position_size = risk_amount / position_ticks

        kelly_cap = self.fractional_kelly_size(
            win_rate, avg_win, avg_loss, account_balance
        )
        if kelly_cap > 0:
            kelly_qty = kelly_cap / entry
            position_size = min(position_size, kelly_qty)

        max_position_value = account_balance * self.MAX_POSITION_SIZE
        max_qty = max_position_value / entry
        position_size = min(position_size, max_qty)

        position_size = position_size.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)

        dollar_risk = position_size * position_ticks
        if dollar_risk > account_balance * self.MAX_RISK_PER_TRADE:
            self.logger.error(
                "RISK EXCEEDED at sizing",
                dollar_risk=str(dollar_risk),
                max_risk=str(account_balance * self.MAX_RISK_PER_TRADE),
            )
            return Decimal("0")

        return position_size

    def calculate_position(
        self,
        entry: Decimal,
        atr: Decimal,
        account_balance: Decimal,
        signal: str,
        win_rate: Decimal = Decimal("0.45"),
        avg_win: Decimal = Decimal("1.5"),
        avg_loss: Decimal = Decimal("1.0"),
    ) -> Tuple[Decimal, Decimal, Decimal, str]:
        side = "buy" if signal.upper() in ("BUY", "LONG") else "sell"
        stop_loss = self.calculate_stop_loss(entry, atr, side)
        take_profit = self.calculate_take_profit(entry, atr, side)
        qty = self.calculate_position_size(
            account_balance, entry, stop_loss, win_rate, avg_win, avg_loss
        )
        return stop_loss, take_profit, qty, side

    def validate_trade_risk(
        self,
        position_size: Decimal,
        entry: Decimal,
        stop_loss: Decimal,
        account_balance: Decimal,
    ) -> bool:
        risk_amount = position_size * abs(entry - stop_loss)
        max_risk = account_balance * self.MAX_RISK_PER_TRADE
        if risk_amount > max_risk:
            self.logger.error(
                "Trade risk validation failed",
                risk=str(risk_amount),
                max_risk=str(max_risk),
            )
            return False
        return True
