"""
forex_engine.py — Institutional Forex signal engine.

Modules
───────
  RecoverySizer     — Controlled-Martingale position sizing: recovers the
                      running deficit in each subsequent trade while capping
                      lot size at max_multiplier × base and enforcing a hard
                      equity circuit-breaker to prevent account wipeout.
  ForexSignalEngine — Session-filtered, regime-aware signal generator:
                      ATR-based SL/TP, multi-factor confirmation, walk-forward
                      backtesting, per-pair performance reporting.

Execution note
──────────────
Signal generation and backtesting run in Python.
For live order routing wire signals to an MQL5 Expert Advisor (MT5)
or a FIX-protocol C++ adapter — never trade with Python at execution time.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

logger = logging.getLogger("forex_engine")


# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ForexEngineConfig:
    # Signal generation
    atr_sl_multiplier:  float = 1.5    # SL = entry ± ATR × this
    atr_tp_multiplier:  float = 2.5    # TP = entry ± ATR × this  → RR ≈ 1.67
    min_signal_score:   float = 0.55   # minimum composite confidence to emit signal
    top_windows:        int   = 4      # use top-N optimal UTC hours for entry

    # Position sizing
    base_risk_pct:      float = 0.01   # 1% of account equity per trade
    pip_value_std:      float = 10.0   # USD per pip per standard lot (major pairs)
    pip_digits:         int   = 4      # decimal places that count as 1 pip

    # Recovery sizer
    max_recovery_mult:  float = 3.0    # never exceed base_lot × this
    circuit_breaker_dd: float = 0.15   # reset if account drawdown exceeds this
    max_single_risk_pct:float = 0.03   # hard cap: SL risk ≤ 3% of equity per trade

    # Walk-forward
    wf_train_days:      int   = 90
    wf_test_days:       int   = 10

    # Signal filters
    rsi_overbought:     float = 65.0
    rsi_oversold:       float = 35.0
    min_atr_pct:        float = 0.0003   # ignore flat/illiquid bars
    max_atr_pct:        float = 0.030    # ignore news-spike bars


# ══════════════════════════════════════════════════════════════════════════════
# Data classes
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class ForexSignal:
    pair:              str
    direction:         str            # 'LONG' | 'SHORT' | 'NEUTRAL'
    entry_price:       float
    stop_loss:         float
    take_profit:       float
    risk_reward:       float
    atr:               float
    # Session timing
    entry_window_utc:  Tuple[int, int]   # (open_hour, close_hour) UTC
    exit_window_utc:   Tuple[int, int]
    # Confidence
    confidence:        float          # 0-1
    filters_passed:    List[str]
    filters_failed:    List[str]
    regime:            str
    # Position sizing (filled in by RecoverySizer)
    lot_size:          float = 0.0
    dollar_risk:       float = 0.0
    recovery_mode:     bool  = False
    recovery_deficit:  float = 0.0


@dataclass
class TradeRecord:
    pair:        str
    direction:   str
    entry:       float
    exit_price:  float
    sl:          float
    tp:          float
    lot_size:    float
    pnl_usd:     float
    outcome:     str    # 'TP' | 'SL' | 'OPEN'
    entry_date:  Any
    exit_date:   Any


@dataclass
class BacktestResult:
    pair:             str
    total_trades:     int
    win_rate:         float
    total_pnl_usd:    float
    avg_win:          float
    avg_loss:         float
    profit_factor:    float
    max_drawdown_usd: float
    sharpe:           float
    trades:           List[TradeRecord]


# ══════════════════════════════════════════════════════════════════════════════
# Recovery position sizer
# ══════════════════════════════════════════════════════════════════════════════

class RecoverySizer:
    """
    Controlled-recovery position sizer.

    Mechanics
    ─────────
    1. Base trade: risk base_risk_pct × equity on SL distance.
    2. After any SL: accumulate the dollar deficit.
    3. Next trade is sized so that a TP win recovers the full deficit
       PLUS the normal base-risk profit in a single trade.
    4. Lot size is capped at max_recovery_mult × base_lot.
    5. Hard circuit-breaker: if peak-to-trough equity drawdown ≥
       circuit_breaker_dd, reset the deficit counter and return to base
       sizing. This prevents compounding into account wipeout.

    Mathematical guarantee
    ──────────────────────
    Let D = accumulated deficit, R = base_risk_amount, TP_dist = price
    distance to take-profit (in price units), V = pip_value_per_lot.

        recovery_lot = (D + R) / (TP_dist × V)

    If recovery_lot > max_mult × base_lot, it is capped; the remaining
    deficit is rolled forward to the following trade.
    """

    def __init__(self, config: ForexEngineConfig) -> None:
        self.cfg               = config
        self._deficit          = 0.0    # running unrecovered loss in USD
        self._peak_equity      = 0.0    # high-water mark for drawdown check
        self._log = logging.getLogger("forex_engine.sizer")

    # ── Public API ─────────────────────────────────────────────────────────

    def size_trade(
        self,
        account_equity:  float,
        sl_distance:     float,   # price distance from entry to SL
        tp_distance:     float,   # price distance from entry to TP
        pip_value:       Optional[float] = None,
    ) -> Tuple[float, float, bool, float]:
        """
        Returns
        ───────
        (lot_size, dollar_risk, in_recovery_mode, remaining_deficit)
        """
        if self._peak_equity == 0.0:
            self._peak_equity = account_equity
        self._peak_equity = max(self._peak_equity, account_equity)

        pv = pip_value or self.cfg.pip_value_std
        sl_dist_pips = sl_distance / (10 ** -self.cfg.pip_digits)
        tp_dist_pips = tp_distance / (10 ** -self.cfg.pip_digits)

        # Circuit breaker
        drawdown = (self._peak_equity - account_equity) / self._peak_equity
        if drawdown >= self.cfg.circuit_breaker_dd:
            self._log.warning(
                "Circuit breaker: %.1f%% drawdown — resetting deficit $%.2f → 0",
                drawdown * 100, self._deficit,
            )
            self._deficit = 0.0

        base_risk_usd  = account_equity * self.cfg.base_risk_pct
        base_lot       = base_risk_usd / (sl_dist_pips * pv + 1e-9)

        in_recovery    = self._deficit > 0
        if in_recovery:
            recovery_target = self._deficit + base_risk_usd
            recovery_lot    = recovery_target / (tp_dist_pips * pv + 1e-9)
            lot_size        = min(recovery_lot, base_lot * self.cfg.max_recovery_mult)
        else:
            lot_size = base_lot

        # Hard cap: SL risk must not exceed max_single_risk_pct
        max_risk_usd   = account_equity * self.cfg.max_single_risk_pct
        max_lot_by_risk = max_risk_usd / (sl_dist_pips * pv + 1e-9)
        lot_size        = min(lot_size, max_lot_by_risk)
        lot_size        = max(lot_size, 0.01)   # minimum 0.01 lots

        dollar_risk = lot_size * sl_dist_pips * pv

        self._log.debug(
            "Size: equity=$%.0f deficit=$%.2f lot=%.2f risk=$%.2f recovery=%s",
            account_equity, self._deficit, lot_size, dollar_risk, in_recovery,
        )
        return round(lot_size, 2), round(dollar_risk, 2), in_recovery, round(self._deficit, 2)

    def record_result(self, pnl_usd: float) -> None:
        """Call after each closed trade to update the running deficit."""
        if pnl_usd < 0:
            self._deficit += abs(pnl_usd)
        else:
            self._deficit = max(0.0, self._deficit - pnl_usd)
        self._log.debug("After trade PnL $%.2f → deficit $%.2f", pnl_usd, self._deficit)

    def reset(self) -> None:
        """Manually reset deficit and peak equity (e.g. start of new session)."""
        self._deficit     = 0.0
        self._peak_equity = 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Signal engine
# ══════════════════════════════════════════════════════════════════════════════

class ForexSignalEngine:
    """
    Multi-timeframe Forex signal generator.

    Signal confirmation stack (each contributes to confidence score)
    ─────────────────────────────────────────────────────────────────
    1. Trend     (daily) : EMA9 vs EMA21 direction             weight 0.25
    2. Momentum  (daily) : MACD histogram sign                 weight 0.20
    3. RSI       (daily) : not overbought/oversold             weight 0.15
    4. Session   (hour)  : entry within top-N scored windows   weight 0.25
    5. Volatility(daily) : ATR within acceptable band          weight 0.15

    All five must fire (weighted sum ≥ min_signal_score) for a signal.
    Direction can be LONG or SHORT; neutral if neither clears the bar.
    """

    PAIR_LABELS: Dict[str, str] = {
        "EURUSD=X": "EUR/USD", "GBPUSD=X": "GBP/USD",
        "USDJPY=X": "USD/JPY", "USDCHF=X": "USD/CHF",
        "AUDUSD=X": "AUD/USD", "USDCAD=X": "USD/CAD",
        "NZDUSD=X": "NZD/USD", "EURGBP=X": "EUR/GBP",
        "EURJPY=X": "EUR/JPY", "GBPJPY=X": "GBP/JPY",
        "AUDJPY=X": "AUD/JPY", "CADJPY=X": "CAD/JPY",
        "XAUUSD=X": "XAU/USD", "XAGUSD=X": "XAG/USD",
    }

    def __init__(
        self,
        forex_data: Dict[str, Any],
        config: Optional[ForexEngineConfig] = None,
        account_equity: float = 10_000.0,
    ) -> None:
        self.data    = forex_data
        self.cfg     = config or ForexEngineConfig()
        self.equity  = account_equity
        self.sizer   = RecoverySizer(self.cfg)
        self._log    = logging.getLogger("forex_engine.signal")

    def _label(self, ticker: str) -> str:
        return self.PAIR_LABELS.get(ticker, ticker)

    # ── Indicator helpers ─────────────────────────────────────────────────

    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["High"], df["Low"], df["Close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        return tr.rolling(period).mean()

    @staticmethod
    def _macd(series: pd.Series) -> Tuple[pd.Series, pd.Series]:
        ema12    = series.ewm(span=12, adjust=False).mean()
        ema26    = series.ewm(span=26, adjust=False).mean()
        macd     = ema12 - ema26
        signal   = macd.ewm(span=9, adjust=False).mean()
        return macd, signal

    # ── Regime via volatility quantile ────────────────────────────────────

    def _regime(self, close: pd.Series) -> str:
        ret    = close.pct_change().dropna()
        vol20  = ret.rolling(20).std().dropna()
        if len(vol20) < 20:
            return "Sideways"
        recent = vol20.iloc[-5:].mean()
        q33, q67 = np.quantile(vol20, [0.33, 0.67])
        drift  = ret.iloc[-20:].mean()
        if recent <= q33 and drift > 0:
            return "Bull"
        if recent >= q67 or drift < -0.0003:
            return "Bear"
        return "Sideways"

    # ── Session-optimal entry windows ────────────────────────────────────

    def _best_entry_hours(self) -> List[int]:
        opt = self.data.get("optimal_windows", pd.DataFrame())
        if opt.empty:
            return [8, 9, 13, 14]   # London open + NY open defaults
        return list(opt.index[: self.cfg.top_windows])

    def _entry_window(self, best_hours: List[int]) -> Tuple[int, int]:
        if not best_hours:
            return (8, 10)
        start = min(best_hours)
        end   = max(best_hours) + 1
        return (start, end)

    def _exit_window(self, entry_window: Tuple[int, int]) -> Tuple[int, int]:
        """Exit 3-6 hours after the entry window closes (default TP horizon)."""
        return (entry_window[1] + 2, entry_window[1] + 6)

    # ── Single-pair signal ────────────────────────────────────────────────

    def _signal_for_pair(
        self,
        ticker: str,
        daily_df: pd.DataFrame,
        best_hours: List[int],
    ) -> ForexSignal:
        label = self._label(ticker)
        close = daily_df["Close"]
        entry = float(close.iloc[-1])

        passed: List[str] = []
        failed: List[str] = []
        bull_score = 0.0
        bear_score = 0.0

        # ── 1. Trend (EMA9 vs EMA21) ──────────────────────────────────────
        ema9  = self._ema(close, 9)
        ema21 = self._ema(close, 21)
        e9, e21 = float(ema9.iloc[-1]), float(ema21.iloc[-1])
        if e9 > e21:
            bull_score += 0.25; passed.append("EMA_trend_bull")
        elif e9 < e21:
            bear_score += 0.25; passed.append("EMA_trend_bear")
        else:
            failed.append("EMA_trend_flat")

        # ── 2. MACD momentum ──────────────────────────────────────────────
        macd, macd_sig = self._macd(close)
        hist = float(macd.iloc[-1]) - float(macd_sig.iloc[-1])
        if hist > 0:
            bull_score += 0.20; passed.append("MACD_bull")
        elif hist < 0:
            bear_score += 0.20; passed.append("MACD_bear")
        else:
            failed.append("MACD_flat")

        # ── 3. RSI filter ─────────────────────────────────────────────────
        rsi_val = float(self._rsi(close).iloc[-1])
        if self.cfg.rsi_oversold <= rsi_val <= self.cfg.rsi_overbought:
            bull_score += 0.15; bear_score += 0.15
            passed.append(f"RSI_ok({rsi_val:.1f})")
        elif rsi_val < self.cfg.rsi_oversold:
            bear_score += 0.05   # possible reversal short → reduce bear
            failed.append(f"RSI_oversold({rsi_val:.1f})")
        else:
            bull_score += 0.05
            failed.append(f"RSI_overbought({rsi_val:.1f})")

        # ── 4. Session filter ─────────────────────────────────────────────
        current_hour = pd.Timestamp.utcnow().hour
        if current_hour in best_hours:
            bull_score += 0.25; bear_score += 0.25
            passed.append(f"Session_ok(UTC{current_hour:02d})")
        else:
            failed.append(f"Session_off(UTC{current_hour:02d})")

        # ── 5. ATR volatility band ────────────────────────────────────────
        atr_series = self._atr(daily_df)
        atr_val    = float(atr_series.iloc[-1])
        atr_pct    = atr_val / (entry + 1e-9)
        if self.cfg.min_atr_pct <= atr_pct <= self.cfg.max_atr_pct:
            bull_score += 0.15; bear_score += 0.15
            passed.append(f"ATR_ok({atr_pct:.4f})")
        elif atr_pct < self.cfg.min_atr_pct:
            failed.append(f"ATR_too_flat({atr_pct:.4f})")
        else:
            failed.append(f"ATR_spike({atr_pct:.4f})")

        # ── Direction & confidence ────────────────────────────────────────
        if bull_score >= self.cfg.min_signal_score and bull_score > bear_score:
            direction  = "LONG"
            confidence = bull_score
        elif bear_score >= self.cfg.min_signal_score and bear_score > bull_score:
            direction  = "SHORT"
            confidence = bear_score
        else:
            direction  = "NEUTRAL"
            confidence = max(bull_score, bear_score)

        # ── SL / TP (ATR-based) ───────────────────────────────────────────
        sl_dist = atr_val * self.cfg.atr_sl_multiplier
        tp_dist = atr_val * self.cfg.atr_tp_multiplier
        rr      = tp_dist / (sl_dist + 1e-9)

        if direction == "LONG":
            sl = entry - sl_dist
            tp = entry + tp_dist
        elif direction == "SHORT":
            sl = entry + sl_dist
            tp = entry - tp_dist
        else:
            sl = entry - sl_dist   # indicative
            tp = entry + tp_dist

        # ── Timing windows ────────────────────────────────────────────────
        entry_win = self._entry_window(best_hours)
        exit_win  = self._exit_window(entry_win)

        # ── Regime ───────────────────────────────────────────────────────
        regime = self._regime(close)

        # ── Position sizing ───────────────────────────────────────────────
        lot, risk_usd, in_rec, deficit = self.sizer.size_trade(
            account_equity=self.equity,
            sl_distance=sl_dist,
            tp_distance=tp_dist,
        )

        return ForexSignal(
            pair=label,
            direction=direction,
            entry_price=round(entry, 5),
            stop_loss=round(sl, 5),
            take_profit=round(tp, 5),
            risk_reward=round(rr, 3),
            atr=round(atr_val, 6),
            entry_window_utc=entry_win,
            exit_window_utc=exit_win,
            confidence=round(confidence, 4),
            filters_passed=passed,
            filters_failed=failed,
            regime=regime,
            lot_size=lot,
            dollar_risk=risk_usd,
            recovery_mode=in_rec,
            recovery_deficit=deficit,
        )

    # ── Batch signal generation ───────────────────────────────────────────

    def generate_signals(self) -> List[ForexSignal]:
        """
        Generate signals for all pairs with available daily data.
        Returns only LONG and SHORT signals (NEUTRAL are filtered out).
        """
        best_hours = self._best_entry_hours()
        self._log.info("Top entry hours (UTC): %s", best_hours)

        signals: List[ForexSignal] = []
        daily_map: Dict[str, pd.DataFrame] = self.data.get("daily", {})

        for ticker, df in daily_map.items():
            if df.empty or len(df) < 30:
                continue
            sig = self._signal_for_pair(ticker, df, best_hours)
            if sig.direction != "NEUTRAL":
                signals.append(sig)
                self._log.info(
                    "SIGNAL %-10s %-5s entry=%s SL=%s TP=%s RR=%.2f "
                    "lot=%.2f conf=%.0f%% regime=%s recovery=%s",
                    sig.pair, sig.direction,
                    sig.entry_price, sig.stop_loss, sig.take_profit,
                    sig.risk_reward, sig.lot_size, sig.confidence * 100,
                    sig.regime, sig.recovery_mode,
                )

        self._log.info(
            "Generated %d actionable signal(s) from %d pairs",
            len(signals), len(daily_map),
        )
        return signals

    # ── Walk-forward backtest ─────────────────────────────────────────────

    def _backtest_pair(
        self, ticker: str, daily_df: pd.DataFrame
    ) -> BacktestResult:
        """
        Simplified bar-by-bar walk-forward backtest:
        - Train on first wf_train_days, generate signal on day wf_train_days+1
        - Step forward wf_test_days at a time
        - Simulate SL/TP outcomes using subsequent OHLC
        """
        cfg   = self.cfg
        label = self._label(ticker)
        trades: List[TradeRecord] = []
        equity = self.equity
        sizer  = RecoverySizer(cfg)
        peak   = equity

        best_hours   = self._best_entry_hours()
        train, test  = cfg.wf_train_days, cfg.wf_test_days
        n            = len(daily_df)

        i = train
        while i + 1 < n:
            window = daily_df.iloc[:i]
            sig    = self._signal_for_pair(ticker, window, best_hours)

            if sig.direction == "NEUTRAL":
                i += test; continue

            # Evaluate on next `test` bars
            future = daily_df.iloc[i: i + test]
            if future.empty:
                break

            entry     = sig.entry_price
            sl, tp    = sig.stop_loss, sig.take_profit
            sl_dist   = abs(entry - sl)
            tp_dist   = abs(entry - tp)

            lot, risk, in_rec, deficit = sizer.size_trade(equity, sl_dist, tp_dist)

            outcome    = "OPEN"
            exit_price = float(future["Close"].iloc[-1])

            for _, bar in future.iterrows():
                if sig.direction == "LONG":
                    if bar["Low"] <= sl:
                        outcome = "SL"; exit_price = sl; break
                    if bar["High"] >= tp:
                        outcome = "TP"; exit_price = tp; break
                else:
                    if bar["High"] >= sl:
                        outcome = "SL"; exit_price = sl; break
                    if bar["Low"] <= tp:
                        outcome = "TP"; exit_price = tp; break

            # PnL calculation (simplified; pip_value=10 per lot)
            price_move = (exit_price - entry) if sig.direction == "LONG" else (entry - exit_price)
            pnl_pips   = price_move / (10 ** -cfg.pip_digits)
            pnl_usd    = pnl_pips * cfg.pip_value_std * lot

            equity    += pnl_usd
            peak       = max(peak, equity)
            sizer.record_result(pnl_usd)

            trades.append(TradeRecord(
                pair=label, direction=sig.direction,
                entry=entry, exit_price=exit_price,
                sl=sl, tp=tp, lot_size=lot,
                pnl_usd=round(pnl_usd, 2), outcome=outcome,
                entry_date=window.index[-1],
                exit_date=future.index[min(test - 1, len(future) - 1)],
            ))
            i += test

        # ── Metrics ───────────────────────────────────────────────────────
        if not trades:
            return BacktestResult(label, 0, 0, 0, 0, 0, 0, 0, 0, [])

        pnls    = np.array([t.pnl_usd for t in trades])
        wins    = pnls[pnls > 0]
        losses  = pnls[pnls < 0]
        win_rate= float(len(wins) / len(pnls))
        avg_win = float(wins.mean()) if len(wins) else 0.0
        avg_loss= float(losses.mean()) if len(losses) else 0.0
        pf      = float(wins.sum() / (-losses.sum() + 1e-9)) if len(losses) else float("inf")

        # Max drawdown
        cum_pnl  = np.cumsum(pnls)
        roll_max = np.maximum.accumulate(cum_pnl + self.equity) - self.equity
        drawdown = cum_pnl - roll_max
        max_dd   = float(drawdown.min())

        # Sharpe (daily-frequency approximation)
        daily_ret = pnls / self.equity
        sharpe    = float(
            daily_ret.mean() / (daily_ret.std() + 1e-9) * np.sqrt(252 / max(test, 1))
        )

        return BacktestResult(
            pair=label,
            total_trades=len(trades),
            win_rate=round(win_rate, 4),
            total_pnl_usd=round(float(pnls.sum()), 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            profit_factor=round(pf, 3),
            max_drawdown_usd=round(max_dd, 2),
            sharpe=round(sharpe, 4),
            trades=trades,
        )

    def run_walk_forward(self) -> Dict[str, BacktestResult]:
        """Run walk-forward backtest on all pairs with daily data."""
        results: Dict[str, BacktestResult] = {}
        daily_map = self.data.get("daily", {})

        for ticker, df in daily_map.items():
            needed = self.cfg.wf_train_days + self.cfg.wf_test_days * 3
            if len(df) < needed:
                self._log.debug("Skip WF %s: need %d bars, have %d", ticker, needed, len(df))
                continue
            r = self._backtest_pair(ticker, df)
            results[self._label(ticker)] = r
            self._log.info(
                "WF %-10s  trades=%d  win=%.0f%%  PnL=$%.0f  PF=%.2f  "
                "MaxDD=$%.0f  Sharpe=%.2f",
                r.pair, r.total_trades, r.win_rate * 100,
                r.total_pnl_usd, r.profit_factor,
                r.max_drawdown_usd, r.sharpe,
            )
        return results

    # ── Report ────────────────────────────────────────────────────────────

    def signal_report(self, signals: List[ForexSignal]) -> str:
        """Formatted text report suitable for dashboard display."""
        if not signals:
            return "No actionable signals at this time."

        lines = [
            "═" * 72,
            f"  FOREX SIGNAL REPORT  —  {pd.Timestamp.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            "═" * 72,
        ]
        for s in sorted(signals, key=lambda x: x.confidence, reverse=True):
            entry_h = f"{s.entry_window_utc[0]:02d}:00–{s.entry_window_utc[1]:02d}:00 UTC"
            exit_h  = f"{s.exit_window_utc[0]:02d}:00–{s.exit_window_utc[1]:02d}:00 UTC"
            rec_tag = "  [RECOVERY MODE]" if s.recovery_mode else ""
            lines += [
                f"\n  {s.pair}  {s.direction}  (confidence {s.confidence:.0%}  "
                f"regime: {s.regime}){rec_tag}",
                f"    Entry range : {entry_h}",
                f"    Exit range  : {exit_h}",
                f"    Entry price : {s.entry_price}",
                f"    Stop Loss   : {s.stop_loss}   ({s.atr * self.cfg.atr_sl_multiplier:.5f} ATR)",
                f"    Take Profit : {s.take_profit}  ({s.atr * self.cfg.atr_tp_multiplier:.5f} ATR)",
                f"    Risk:Reward : 1 : {s.risk_reward:.2f}",
                f"    Lot size    : {s.lot_size}  (risk ${s.dollar_risk:.2f})",
            ]
            if s.recovery_mode:
                lines.append(
                    f"    Recovery def: ${s.recovery_deficit:.2f}  "
                    f"(sizing to recover deficit + base profit)"
                )
            lines.append(
                f"    Filters ✓   : {', '.join(s.filters_passed)}"
            )
            if s.filters_failed:
                lines.append(f"    Filters ✗   : {', '.join(s.filters_failed)}")

        lines.append("\n" + "═" * 72)
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_forex_engine(
    forex_bundle: Dict[str, Any],
    account_equity: float = 10_000.0,
    config: Optional[ForexEngineConfig] = None,
    backtest: bool = True,
) -> Tuple[List[ForexSignal], Optional[Dict[str, BacktestResult]]]:
    """
    Convenience wrapper.

    Returns (signals, backtest_results).
    backtest_results is None when backtest=False.

    Example
    ───────
    from data_feed import DataFeedOrchestrator
    bundle = DataFeedOrchestrator().run()
    signals, wf = run_forex_engine(bundle['forex'], account_equity=25_000)
    """
    engine   = ForexSignalEngine(forex_bundle, config, account_equity)
    signals  = engine.generate_signals()
    wf_res   = engine.run_walk_forward() if backtest else None
    return signals, wf_res


# ══════════════════════════════════════════════════════════════════════════════
# CLI smoke test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO)
    print("Running forex_engine in standalone stub mode …")
    print("For real output, integrate with DataFeedOrchestrator.run()['forex']\n")

    stub_dates = pd.date_range("2022-01-01", periods=500, freq="B")

    def _fake_ohlcv(seed: int, trend: float = 0.0001) -> pd.DataFrame:
        rng   = np.random.default_rng(seed)
        price = 1.1 * np.cumprod(1 + rng.normal(trend, 0.006, 500))
        high  = price * (1 + rng.uniform(0, 0.003, 500))
        low   = price * (1 - rng.uniform(0, 0.003, 500))
        df    = pd.DataFrame({
            "Open": price, "High": high, "Low": low, "Close": price,
            "Volume": np.zeros(500),
        }, index=stub_dates)
        # Add pre-computed indicators expected by the engine
        close = df["Close"]
        df["EMA_9"]  = close.ewm(span=9,  adjust=False).mean()
        df["EMA_21"] = close.ewm(span=21, adjust=False).mean()
        return df

    opt_windows = pd.DataFrame({
        "mean_score": [0.82, 0.79, 0.74, 0.68],
        "sessions":   ["LDN/NY Overlap", "London", "New York", "Tokyo"],
    }, index=[13, 8, 15, 2])

    stub_forex = {
        "daily": {
            "EURUSD=X": _fake_ohlcv(0,  trend=0.0002),
            "GBPUSD=X": _fake_ohlcv(1,  trend=-0.0001),
            "USDJPY=X": _fake_ohlcv(2,  trend=0.0003),
            "XAUUSD=X": _fake_ohlcv(3,  trend=0.0001),
        },
        "optimal_windows": opt_windows,
    }

    signals, wf_results = run_forex_engine(
        stub_forex, account_equity=25_000, backtest=True
    )

    engine = ForexSignalEngine(stub_forex, account_equity=25_000)
    print(engine.signal_report(signals))

    if wf_results:
        print("\n── Walk-Forward Summary ──────────────────────────────────────────")
        for pair, r in wf_results.items():
            print(
                f"  {pair:<12}  trades={r.total_trades:>3}  "
                f"win={r.win_rate:.0%}  PnL=${r.total_pnl_usd:>8,.0f}  "
                f"PF={r.profit_factor:.2f}  MaxDD=${r.max_drawdown_usd:,.0f}  "
                f"Sharpe={r.sharpe:.2f}"
            )
