"""
strategies.py — Comprehensive Trading Strategy Library for Atlas Capital
════════════════════════════════════════════════════════════════════════
Modular signal generators covering:
  Trend & Momentum · Mean Reversion · Breakout · SMC · Seasonal · BTMM
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import math


@dataclass
class Signal:
    strategy: str
    category: str
    direction: str   # LONG / SHORT / FLAT
    strength: float  # 0–1
    detail: str = ""
    indicators: Dict[str, Any] = field(default_factory=dict)


# ── helpers ───────────────────────────────────────────────────────────────────

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()

def _rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    g = d.clip(lower=0).ewm(span=n, adjust=False).mean()
    ls = (-d.clip(upper=0)).ewm(span=n, adjust=False).mean()
    return 100 - 100 / (1 + g / ls.replace(0, np.nan))

def _vwap(df: pd.DataFrame) -> pd.Series:
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df.get("Volume", pd.Series(1, index=df.index)).replace(0, np.nan)
    return (tp * vol).cumsum() / vol.cumsum()

def _sf(v, fmt, fallback="—"):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return fallback
    try:
        return format(v, fmt)
    except Exception:
        return fallback


# ══════════════════════════════════════════════════════════════════════════════
# TREND & MOMENTUM
# ══════════════════════════════════════════════════════════════════════════════

class MACross:
    name = "MA Cross"
    category = "Trend"
    def __init__(self, fast=20, slow=50):
        self.fast, self.slow = fast, slow
        self.name = f"MA {fast}/{slow}"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        f = c.rolling(self.fast).mean()
        s = c.rolling(self.slow).mean()
        fv, sv = float(f.iloc[-1]), float(s.iloc[-1])
        direction = "LONG" if fv > sv else "SHORT"
        spread = abs(fv - sv) / sv if sv else 0
        return Signal(self.name, self.category, direction, min(spread * 20, 1.0),
                      f"MA{self.fast}={fv:.4f} | MA{self.slow}={sv:.4f}",
                      {f"MA{self.fast}": fv, f"MA{self.slow}": sv})


class EMACross:
    name = "EMA Cross"
    category = "Trend"
    def __init__(self, fast=12, slow=26):
        self.fast, self.slow = fast, slow
        self.name = f"EMA {fast}/{slow}"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        fv, sv = float(_ema(c, self.fast).iloc[-1]), float(_ema(c, self.slow).iloc[-1])
        direction = "LONG" if fv > sv else "SHORT"
        spread = abs(fv - sv) / sv if sv else 0
        return Signal(self.name, self.category, direction, min(spread * 20, 1.0),
                      f"EMA{self.fast}={fv:.4f} | EMA{self.slow}={sv:.4f}",
                      {f"EMA{self.fast}": fv, f"EMA{self.slow}": sv})


class MACDStrategy:
    name = "MACD"
    category = "Momentum"
    def __init__(self, fast=12, slow=26, signal=9):
        self.fast, self.slow, self.signal = fast, slow, signal
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        macd = _ema(c, self.fast) - _ema(c, self.slow)
        sig = _ema(macd, self.signal)
        hist = float((macd - sig).iloc[-1])
        mv, sv = float(macd.iloc[-1]), float(sig.iloc[-1])
        direction = "LONG" if hist > 0 else "SHORT"
        strength = min(abs(hist) / (abs(mv) + 1e-9), 1.0)
        return Signal(self.name, self.category, direction, strength,
                      f"MACD={mv:.4f} | Signal={sv:.4f} | Hist={hist:.4f}",
                      {"macd": mv, "signal": sv, "histogram": hist})


class RSIStrategy:
    name = "RSI"
    category = "Momentum"
    def __init__(self, period=14, oversold=30.0, overbought=70.0):
        self.period, self.oversold, self.overbought = period, oversold, overbought
    def get_signal(self, df: pd.DataFrame) -> Signal:
        val = float(_rsi(df["Close"], self.period).iloc[-1])
        if val < self.oversold:
            d, s = "LONG", (self.oversold - val) / self.oversold
        elif val > self.overbought:
            d, s = "SHORT", (val - self.overbought) / (100 - self.overbought)
        else:
            d, s = "FLAT", 0.3
        return Signal(self.name, self.category, d, min(s, 1.0),
                      f"RSI({self.period})={val:.1f} | OS={self.oversold} OB={self.overbought}",
                      {"rsi": val})


class ATRVolatility:
    name = "ATR Volatility"
    category = "Volatility"
    def __init__(self, period=14, threshold_pct=0.02):
        self.period, self.threshold_pct = period, threshold_pct
    def get_signal(self, df: pd.DataFrame) -> Signal:
        atr_val = float(_atr(df, self.period).iloc[-1])
        price = float(df["Close"].iloc[-1])
        atr_pct = atr_val / price if price else 0
        if atr_pct < self.threshold_pct:
            d, detail = "LONG", "Low volatility — trending environment"
        elif atr_pct > self.threshold_pct * 2:
            d, detail = "SHORT", "High volatility — caution zone"
        else:
            d, detail = "FLAT", "Moderate volatility"
        return Signal(self.name, self.category, d, 0.5,
                      f"ATR={atr_val:.4f} ({atr_pct:.2%} of price) | {detail}",
                      {"atr": atr_val, "atr_pct": atr_pct})


class VWAPSignal:
    name = "VWAP"
    category = "Trend"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        vwap_val = float(_vwap(df).iloc[-1])
        price = float(df["Close"].iloc[-1])
        direction = "LONG" if price > vwap_val else "SHORT"
        dist = abs(price - vwap_val) / vwap_val if vwap_val else 0
        return Signal(self.name, self.category, direction, min(dist * 10, 1.0),
                      f"Price={price:.4f} | VWAP={vwap_val:.4f} | Gap={dist:.2%}",
                      {"vwap": vwap_val, "price": price})


class SMALevels:
    name = "SMA Levels"
    category = "Trend"
    PERIODS = [20, 50, 100, 150, 200]
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        price = float(c.iloc[-1])
        above = 0
        vals = {}
        for p in self.PERIODS:
            sma = float(c.rolling(p).mean().iloc[-1])
            vals[f"SMA{p}"] = sma
            if not math.isnan(sma) and price > sma:
                above += 1
        total = len(self.PERIODS)
        if above >= 4:
            d, s = "LONG", above / total
        elif above <= 1:
            d, s = "SHORT", (total - above) / total
        else:
            d, s = "FLAT", 0.4
        return Signal(self.name, self.category, d, s,
                      f"Price above {above}/{total} SMAs (20/50/100/150/200)", vals)


class GoldenDeathCross:
    name = "Golden/Death Cross"
    category = "Trend"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        ma50 = float(c.rolling(50).mean().iloc[-1])
        ma200 = float(c.rolling(200).mean().iloc[-1])
        if math.isnan(ma200):
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data (need 200+ bars)")
        direction = "LONG" if ma50 > ma200 else "SHORT"
        label = "🌟 Golden Cross" if direction == "LONG" else "💀 Death Cross"
        return Signal(self.name, self.category, direction, 0.80,
                      f"{label} | MA50={ma50:.4f} | MA200={ma200:.4f}",
                      {"ma50": ma50, "ma200": ma200})


class MomentumRotation:
    name = "Momentum Rotation"
    category = "Momentum"
    def __init__(self, lookback=63):
        self.lookback = lookback
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        n = min(self.lookback, len(c) - 1)
        ret = float(c.iloc[-1] / c.iloc[-n] - 1) if n > 0 else 0.0
        vol = float(c.pct_change().tail(n).std() * 252**0.5) if n > 1 else 1.0
        score = ret / (vol + 1e-9)
        if score > 0.5:
            d, s = "LONG", min(score / 2, 1.0)
        elif score < -0.5:
            d, s = "SHORT", min(-score / 2, 1.0)
        else:
            d, s = "FLAT", 0.3
        return Signal(self.name, self.category, d, s,
                      f"{n}d return={ret:.1%} | Risk-adj score={score:.2f}",
                      {"momentum_score": score, "raw_return": ret})


# ══════════════════════════════════════════════════════════════════════════════
# MEAN REVERSION
# ══════════════════════════════════════════════════════════════════════════════

class BollingerBands:
    name = "Bollinger Bands"
    category = "Mean Reversion"
    def __init__(self, period=20, std_dev=2.0):
        self.period, self.std_dev = period, std_dev
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        mid = c.rolling(self.period).mean()
        std = c.rolling(self.period).std()
        lower = mid - self.std_dev * std
        upper = mid + self.std_dev * std
        price = float(c.iloc[-1])
        lv, uv, mv = float(lower.iloc[-1]), float(upper.iloc[-1]), float(mid.iloc[-1])
        bw = (uv - lv) / mv if mv else 0
        pct_b = (price - lv) / (uv - lv + 1e-9)
        if price < lv:
            d, s = "LONG", min((lv - price) / (mv * 0.01 + 1e-9), 1.0)
        elif price > uv:
            d, s = "SHORT", min((price - uv) / (mv * 0.01 + 1e-9), 1.0)
        else:
            d, s = "FLAT", abs(pct_b - 0.5)
        return Signal(self.name, self.category, d, min(s, 1.0),
                      f"%B={pct_b:.2f} | BW={bw:.2%} | Mid={mv:.4f}",
                      {"pct_b": pct_b, "bandwidth": bw, "lower": lv, "upper": uv})


class ZScoreReversion:
    name = "Z-Score Reversion"
    category = "Mean Reversion"
    def __init__(self, period=20, threshold=2.0):
        self.period, self.threshold = period, threshold
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        z = (c - c.rolling(self.period).mean()) / c.rolling(self.period).std()
        z_val = float(z.iloc[-1])
        if math.isnan(z_val):
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        if z_val < -self.threshold:
            d, s = "LONG", min(abs(z_val) / self.threshold / 2, 1.0)
        elif z_val > self.threshold:
            d, s = "SHORT", min(z_val / self.threshold / 2, 1.0)
        else:
            d, s = "FLAT", 0.2
        return Signal(self.name, self.category, d, s,
                      f"Z={z_val:.2f} (threshold ±{self.threshold})", {"z_score": z_val})


# ══════════════════════════════════════════════════════════════════════════════
# BREAKOUT
# ══════════════════════════════════════════════════════════════════════════════

class TurtleBreakout:
    name = "Turtle Breakout"
    category = "Breakout"
    def __init__(self, entry_period=20, exit_period=10):
        self.entry_period, self.exit_period = entry_period, exit_period
        self.name = f"Turtle {entry_period}/{exit_period}"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        price = float(c.iloc[-1])
        high_n = float(c.rolling(self.entry_period).max().iloc[-1])
        low_n = float(c.rolling(self.entry_period).min().iloc[-1])
        if price >= high_n * 0.999:
            d, s, detail = "LONG", 0.85, f"Breakout above {self.entry_period}d high={high_n:.4f}"
        elif price <= low_n * 1.001:
            d, s, detail = "SHORT", 0.85, f"Breakdown below {self.entry_period}d low={low_n:.4f}"
        else:
            rng = high_n - low_n
            pct = (price - low_n) / rng if rng > 0 else 0.5
            d, s, detail = "FLAT", 0.3, f"In range {pct:.0%} between {low_n:.4f}–{high_n:.4f}"
        return Signal(self.name, self.category, d, s, detail,
                      {f"high_{self.entry_period}": high_n, f"low_{self.entry_period}": low_n})


class RangeBreakout:
    name = "Range Breakout"
    category = "Breakout"
    def __init__(self, period=10):
        self.period = period
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c, h, l = df["Close"], df["High"], df["Low"]
        price = float(c.iloc[-1])
        rh = float(h.rolling(self.period).max().iloc[-1])
        rl = float(l.rolling(self.period).min().iloc[-1])
        atr_now = float(_atr(df, self.period).iloc[-1])
        atr_avg = float(_atr(df, self.period).mean())
        compressed = atr_now < atr_avg * 0.5
        comp_label = " (COMPRESSED)" if compressed else ""
        if price >= rh * 0.999:
            d, s, detail = "LONG", 0.90 if compressed else 0.70, f"Breakout above {self.period}d high={rh:.4f}{comp_label}"
        elif price <= rl * 1.001:
            d, s, detail = "SHORT", 0.90 if compressed else 0.70, f"Breakdown below {self.period}d low={rl:.4f}{comp_label}"
        else:
            d, s = "FLAT", 0.3
            detail = f"Compressed in [{rl:.4f}–{rh:.4f}]{comp_label}"
        return Signal(self.name, self.category, d, s, detail,
                      {"high": rh, "low": rl, "compressed": compressed})


class GoldORB:
    """Opening-Range Breakout — distilled from the GOLD_ORB EA (XAUUSD H1).

    Establishes the session's opening range from the first `open_bars`, waits
    for `confirm_bars` of consolidation inside that range, then trades the
    break (long above the range high, short below the low). Strength scales
    with breakout magnitude vs ATR and whether the range was confirmed.

    Session-aware when the frame is intraday (groups by calendar day); degrades
    gracefully to a rolling opening range on daily bars or a non-datetime index.
    Pure signal only — TP/SL/trailing/daily-trade-cap live in the engine's
    RiskManager (see knowledge/external_repos.md for the parameter mapping).
    """
    name = "Gold ORB"
    category = "Breakout"

    def __init__(self, open_bars: int = 1, confirm_bars: int = 3):
        self.open_bars = max(1, open_bars)
        self.confirm_bars = max(1, confirm_bars)

    def get_signal(self, df: pd.DataFrame) -> Signal:
        need = self.open_bars + self.confirm_bars + 1
        if len(df) < need:
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        price = float(df["Close"].iloc[-1])
        atr_now = float(_atr(df, 14).iloc[-1])

        # Pick the opening-range window: the live session if intraday, else rolling.
        session, label = df, "rolling"
        try:
            same_day = df.index.normalize() == df.index[-1].normalize()
            if 1 < int(same_day.sum()) < len(df):
                session, label = df[same_day], "session"
        except Exception:
            pass
        if len(session) < self.open_bars + self.confirm_bars + 1:
            session, label = df.tail(need), "rolling"

        ob = self.open_bars
        or_high = float(session["High"].iloc[:ob].max())
        or_low = float(session["Low"].iloc[:ob].min())
        rng = max(or_high - or_low, 1e-9)
        denom = atr_now if atr_now and math.isfinite(atr_now) else rng

        inside = session.iloc[ob:ob + self.confirm_bars]
        confirmed = bool(
            len(inside) >= self.confirm_bars
            and (inside["High"] <= or_high * 1.0005).all()
            and (inside["Low"] >= or_low * 0.9995).all()
        )
        conf_mult = 1.0 if confirmed else 0.6

        if price > or_high:
            mag = min((price - or_high) / denom, 1.0)
            s = min((0.6 + 0.3 * mag) * conf_mult, 1.0)
            return Signal(self.name, self.category, "LONG", round(s, 3),
                          f"ORB break ↑ {or_high:.4f} ({label}, confirmed={confirmed})",
                          {"or_high": or_high, "or_low": or_low, "confirmed": confirmed})
        if price < or_low:
            mag = min((or_low - price) / denom, 1.0)
            s = min((0.6 + 0.3 * mag) * conf_mult, 1.0)
            return Signal(self.name, self.category, "SHORT", round(s, 3),
                          f"ORB break ↓ {or_low:.4f} ({label}, confirmed={confirmed})",
                          {"or_high": or_high, "or_low": or_low, "confirmed": confirmed})
        return Signal(self.name, self.category, "FLAT", 0.25,
                      f"Inside opening range [{or_low:.4f}–{or_high:.4f}] ({label})",
                      {"or_high": or_high, "or_low": or_low, "confirmed": confirmed})


class SeasonalFilter:
    name = "Seasonal Filter"
    category = "Seasonal"
    BULL_MONTHS = {11, 12, 1, 2, 3, 4}
    def get_signal(self, df: pd.DataFrame) -> Signal:
        month = df.index[-1].month
        if month in self.BULL_MONTHS:
            d, s, detail = "LONG", 0.65, f"Month {month}: Historically bullish (Nov–Apr)"
        else:
            d, s, detail = "SHORT", 0.55, f"Month {month}: 'Sell in May' — weaker period (May–Oct)"
        return Signal(self.name, self.category, d, s, detail, {"month": month})


# ══════════════════════════════════════════════════════════════════════════════
# SMART MONEY CONCEPTS (SMC)
# ══════════════════════════════════════════════════════════════════════════════

class OrderBlockDetector:
    name = "Order Block"
    category = "SMC"
    def __init__(self, impulse_factor=1.5):
        self.impulse_factor = impulse_factor
    def get_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 20:
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        c, o, h, l = df["Close"], df["Open"], df["High"], df["Low"]
        atr = _atr(df, 14)
        price = float(c.iloc[-1])
        bull_obs, bear_obs = [], []
        for i in range(5, min(len(df) - 4, len(df))):
            body = abs(float(c.iloc[i]) - float(o.iloc[i]))
            if body < float(atr.iloc[i]) * 0.3:
                continue
            if float(c.iloc[i]) < float(o.iloc[i]):  # bearish candle → potential bull OB
                future = float(c.iloc[min(i+3, len(df)-1)]) - float(c.iloc[i])
                if future > float(atr.iloc[i]) * self.impulse_factor:
                    bull_obs.append((float(l.iloc[i]), float(h.iloc[i])))
            else:
                future = float(c.iloc[i]) - float(c.iloc[min(i+3, len(df)-1)])
                if future > float(atr.iloc[i]) * self.impulse_factor:
                    bear_obs.append((float(l.iloc[i]), float(h.iloc[i])))
        for ob_lo, ob_hi in reversed(bull_obs[-10:]):
            if ob_lo <= price <= ob_hi:
                return Signal(self.name, self.category, "LONG", 0.85,
                              f"Price in Bullish OB [{ob_lo:.4f}–{ob_hi:.4f}]",
                              {"ob_low": ob_lo, "ob_high": ob_hi, "type": "bullish"})
        for ob_lo, ob_hi in reversed(bear_obs[-10:]):
            if ob_lo <= price <= ob_hi:
                return Signal(self.name, self.category, "SHORT", 0.85,
                              f"Price in Bearish OB [{ob_lo:.4f}–{ob_hi:.4f}]",
                              {"ob_low": ob_lo, "ob_high": ob_hi, "type": "bearish"})
        return Signal(self.name, self.category, "FLAT", 0.3,
                      f"Not in OB zone | {len(bull_obs)} bull, {len(bear_obs)} bear OBs detected")


class FairValueGap:
    name = "Fair Value Gap"
    category = "SMC"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 10:
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        c, h, l = df["Close"], df["High"], df["Low"]
        price = float(c.iloc[-1])
        bull_fvg, bear_fvg = [], []
        for i in range(2, len(df)):
            if float(l.iloc[i]) > float(h.iloc[i-2]):
                bull_fvg.append((float(h.iloc[i-2]), float(l.iloc[i])))
            if float(h.iloc[i]) < float(l.iloc[i-2]):
                bear_fvg.append((float(h.iloc[i]), float(l.iloc[i-2])))
        for flo, fhi in reversed(bull_fvg[-15:]):
            if flo <= price <= fhi:
                return Signal(self.name, self.category, "LONG", 0.80,
                              f"In Bullish FVG [{flo:.4f}–{fhi:.4f}] — expect bounce",
                              {"fvg_low": flo, "fvg_high": fhi, "type": "bullish"})
        for flo, fhi in reversed(bear_fvg[-15:]):
            if flo <= price <= fhi:
                return Signal(self.name, self.category, "SHORT", 0.80,
                              f"In Bearish FVG [{flo:.4f}–{fhi:.4f}] — expect rejection",
                              {"fvg_low": flo, "fvg_high": fhi, "type": "bearish"})
        return Signal(self.name, self.category, "FLAT", 0.3,
                      f"Not in FVG | {len(bull_fvg)} bull, {len(bear_fvg)} bear FVGs")


class BreakOfStructure:
    name = "BOS / ChoCh"
    category = "SMC"
    def __init__(self, swing_period=10):
        self.swing_period = swing_period
    def _swings(self, df):
        h, l, n = df["High"], df["Low"], self.swing_period
        highs, lows = [], []
        for i in range(n, len(df) - n):
            window_h = h.iloc[max(0,i-n):i+n+1]
            window_l = l.iloc[max(0,i-n):i+n+1]
            if float(h.iloc[i]) == float(window_h.max()):
                highs.append(float(h.iloc[i]))
            if float(l.iloc[i]) == float(window_l.min()):
                lows.append(float(l.iloc[i]))
        return highs, lows
    def get_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.swing_period * 4:
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        highs, lows = self._swings(df)
        if len(highs) < 2 or len(lows) < 2:
            return Signal(self.name, self.category, "FLAT", 0.3, "No clear structure yet")
        price = float(df["Close"].iloc[-1])
        lh, llh = highs[-1], highs[-2]
        ll, lll = lows[-1], lows[-2]
        if price > lh and lh > llh:
            return Signal(self.name, self.category, "LONG", 0.85,
                          f"BOS Bullish — HH sequence. Break above {lh:.4f}",
                          {"last_high": lh, "last_low": ll, "type": "BOS_bullish"})
        if price < ll and lh > llh:
            return Signal(self.name, self.category, "SHORT", 0.90,
                          f"ChoCh — Trend reversal detected. Break below {ll:.4f}",
                          {"last_high": lh, "last_low": ll, "type": "ChoCh_bearish"})
        if price < ll and ll < lll:
            return Signal(self.name, self.category, "SHORT", 0.85,
                          f"BOS Bearish — LL sequence. Break below {ll:.4f}",
                          {"last_high": lh, "last_low": ll, "type": "BOS_bearish"})
        return Signal(self.name, self.category, "FLAT", 0.3,
                      f"Structure intact | High={lh:.4f} | Low={ll:.4f}")


class LiquiditySweep:
    name = "Liquidity Sweep"
    category = "SMC"
    def __init__(self, period=20):
        self.period = period
    def get_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 30:
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        h, l, c = df["High"], df["Low"], df["Close"]
        prev_high = float(h.iloc[:-3].rolling(self.period).max().iloc[-1])
        prev_low  = float(l.iloc[:-3].rolling(self.period).min().iloc[-1])
        rec_high  = float(h.iloc[-3:].max())
        rec_low   = float(l.iloc[-3:].min())
        rec_close = float(c.iloc[-1])
        if rec_high > prev_high and rec_close < prev_high:
            return Signal(self.name, self.category, "SHORT", 0.90,
                          f"Bearish sweep above {prev_high:.4f} — smart money selling",
                          {"swept_level": prev_high, "type": "bearish_sweep"})
        if rec_low < prev_low and rec_close > prev_low:
            return Signal(self.name, self.category, "LONG", 0.90,
                          f"Bullish sweep below {prev_low:.4f} — smart money buying",
                          {"swept_level": prev_low, "type": "bullish_sweep"})
        return Signal(self.name, self.category, "FLAT", 0.3,
                      f"No sweep detected | Range [{prev_low:.4f}–{prev_high:.4f}]")


class RallyBaseRally:
    name = "Rally-Base-Rally"
    category = "SMC"
    def __init__(self, base_atr_mult=0.5, lookback=30):
        self.base_atr_mult, self.lookback = base_atr_mult, lookback
    def get_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < self.lookback + 5:
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        c = df["Close"]
        atr = _atr(df)
        price = float(c.iloc[-1])
        recent = c.tail(self.lookback)
        atr_val = float(atr.iloc[-1])
        base_zones = []
        for i in range(1, len(recent) - 1):
            move_prev = abs(float(recent.iloc[i]) - float(recent.iloc[i-1]))
            move_next = abs(float(recent.iloc[i+1]) - float(recent.iloc[i]))
            if move_prev < atr_val * self.base_atr_mult and move_next < atr_val * self.base_atr_mult:
                base_zones.append((float(recent.index[i]), float(recent.iloc[i])))
        if not base_zones:
            return Signal(self.name, self.category, "FLAT", 0.3, "No RBR base detected")
        nearest = min(base_zones, key=lambda x: abs(price - x[1]))
        dist = abs(price - nearest[1]) / (atr_val + 1e-9)
        if dist < 2.0:
            direction = "LONG" if price >= nearest[1] else "SHORT"
            return Signal(self.name, self.category, direction, max(0.5, 1.0 - dist * 0.25),
                          f"Near RBR base zone at {nearest[1]:.4f} ({dist:.1f} ATRs away)",
                          {"base_price": nearest[1], "atr_distance": dist})
        return Signal(self.name, self.category, "FLAT", 0.2,
                      f"RBR base at {nearest[1]:.4f} — too far ({dist:.1f} ATRs)")


# ══════════════════════════════════════════════════════════════════════════════
# BTMM (Beat The Market Maker)
# ══════════════════════════════════════════════════════════════════════════════

class BTMMSignal:
    name = "BTMM"
    category = "Institutional"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c, h, l = df["Close"], df["High"], df["Low"]
        e5  = float(_ema(c, 5).iloc[-1])
        e13 = float(_ema(c, 13).iloc[-1])
        e50 = float(_ema(c, 50).iloc[-1])
        price = float(c.iloc[-1])
        dr = (h - l).rolling(20).mean()
        adr = float(dr.iloc[-1])
        day_range = float(h.iloc[-1] - l.iloc[-1])
        adr_used = day_range / adr if adr > 0 else 0.5
        bull = e5 > e13 > e50 and price > e5
        bear = e5 < e13 < e50 and price < e5
        if bull:
            d, s = "LONG", min(0.6 + (1 - adr_used) * 0.3, 0.95)
            detail = f"Bullish EMA stack (5>13>50) | ADR used={adr_used:.0%}"
        elif bear:
            d, s = "SHORT", min(0.6 + (1 - adr_used) * 0.3, 0.95)
            detail = f"Bearish EMA stack (5<13<50) | ADR used={adr_used:.0%}"
        else:
            d, s = "FLAT", 0.3
            detail = f"Mixed EMA stack | 5={e5:.4f} 13={e13:.4f} 50={e50:.4f}"
        return Signal(self.name, self.category, d, s, detail,
                      {"ema5": e5, "ema13": e13, "ema50": e50, "adr": adr, "adr_used": adr_used})


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN
# ══════════════════════════════════════════════════════════════════════════════

class CandlestickPatterns:
    name = "Candlestick Patterns"
    category = "Pattern"
    def get_signal(self, df: pd.DataFrame) -> Signal:
        if len(df) < 3:
            return Signal(self.name, self.category, "FLAT", 0.0, "Insufficient data")
        o, h, l, c = float(df["Open"].iloc[-1]), float(df["High"].iloc[-1]), float(df["Low"].iloc[-1]), float(df["Close"].iloc[-1])
        po, ph, pl, pc = float(df["Open"].iloc[-2]), float(df["High"].iloc[-2]), float(df["Low"].iloc[-2]), float(df["Close"].iloc[-2])
        body = abs(c - o)
        rng = h - l + 1e-9
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        if body / rng < 0.1:
            return Signal(self.name, self.category, "FLAT", 0.5, "Doji — indecision, await confirmation")
        if pc < po and c > o and c > po and o < pc:
            return Signal(self.name, self.category, "LONG", 0.80, "Bullish Engulfing — strong reversal")
        if pc > po and c < o and c < po and o > pc:
            return Signal(self.name, self.category, "SHORT", 0.80, "Bearish Engulfing — strong reversal")
        if lower_wick > body * 2 and upper_wick < body * 0.5 and c > o:
            return Signal(self.name, self.category, "LONG", 0.70, "Hammer — bullish reversal at support")
        if upper_wick > body * 2 and lower_wick < body * 0.5 and c < o:
            return Signal(self.name, self.category, "SHORT", 0.70, "Shooting Star — bearish reversal")
        if body / rng > 0.85:
            direction = "LONG" if c > o else "SHORT"
            return Signal(self.name, self.category, direction, 0.65,
                          f"{'Bullish' if direction=='LONG' else 'Bearish'} Marubozu — strong momentum")
        return Signal(self.name, self.category, "FLAT", 0.2, "No significant pattern")


class GridTrading:
    name = "Grid Trading"
    category = "Range"
    def __init__(self, grid_levels=5, period=20):
        self.grid_levels, self.period = grid_levels, period
    def get_signal(self, df: pd.DataFrame) -> Signal:
        c = df["Close"]
        price = float(c.iloc[-1])
        high = float(c.rolling(self.period).max().iloc[-1])
        low  = float(c.rolling(self.period).min().iloc[-1])
        rng = high - low
        if rng == 0:
            return Signal(self.name, self.category, "FLAT", 0.0, "No range detected")
        step = rng / self.grid_levels
        grid_idx = int((price - low) / step)
        pct_in_grid = (price - low) / rng
        if pct_in_grid < 0.2:
            d, s, detail = "LONG", 0.75, f"At grid bottom (level {grid_idx}/{self.grid_levels})"
        elif pct_in_grid > 0.8:
            d, s, detail = "SHORT", 0.75, f"At grid top (level {grid_idx}/{self.grid_levels})"
        else:
            d, s, detail = "FLAT", 0.4, f"Mid-grid at level {grid_idx}/{self.grid_levels} | {pct_in_grid:.0%} of range"
        return Signal(self.name, self.category, d, s, detail,
                      {"grid_level": grid_idx, "grid_total": self.grid_levels, "pct_in_range": pct_in_grid})


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY REGISTRY
# ══════════════════════════════════════════════════════════════════════════════

ALL_STRATEGIES = [
    # Trend
    MACross(20, 50),
    MACross(50, 200),
    EMACross(12, 26),
    EMACross(9, 21),
    GoldenDeathCross(),
    SMALevels(),
    VWAPSignal(),
    # Momentum
    MACDStrategy(),
    RSIStrategy(14, 30, 70),
    MomentumRotation(63),
    # Volatility
    ATRVolatility(),
    # Mean Reversion
    BollingerBands(20, 2.0),
    ZScoreReversion(20, 2.0),
    # Breakout
    TurtleBreakout(20, 10),
    TurtleBreakout(55, 20),
    RangeBreakout(10),
    GoldORB(open_bars=1, confirm_bars=3),
    # Seasonal
    SeasonalFilter(),
    # SMC
    OrderBlockDetector(),
    FairValueGap(),
    BreakOfStructure(),
    LiquiditySweep(),
    RallyBaseRally(),
    # Institutional
    BTMMSignal(),
    # Pattern
    CandlestickPatterns(),
    # Range
    GridTrading(),
]

STRATEGY_CATEGORIES = sorted(set(s.category for s in ALL_STRATEGIES))


def run_all_strategies(df: pd.DataFrame, selected_names: Optional[List[str]] = None) -> List[Signal]:
    strategies = ALL_STRATEGIES if selected_names is None else [s for s in ALL_STRATEGIES if s.name in selected_names]
    results = []
    for strat in strategies:
        try:
            results.append(strat.get_signal(df))
        except Exception:
            pass
    return results


def aggregate_signal(signals: List[Signal]) -> Dict[str, Any]:
    if not signals:
        return {"direction": "FLAT", "score": 0.0, "confidence": 0.0,
                "long_count": 0, "short_count": 0, "flat_count": 0}
    long_score  = sum(s.strength for s in signals if s.direction == "LONG")
    short_score = sum(s.strength for s in signals if s.direction == "SHORT")
    total = long_score + short_score
    long_count  = sum(1 for s in signals if s.direction == "LONG")
    short_count = sum(1 for s in signals if s.direction == "SHORT")
    flat_count  = len(signals) - long_count - short_count
    if total == 0:
        return {"direction": "FLAT", "score": 0.0, "confidence": 0.0,
                "long_count": long_count, "short_count": short_count, "flat_count": flat_count}
    consensus = (long_score - short_score) / total
    direction = "LONG" if consensus > 0.1 else ("SHORT" if consensus < -0.1 else "FLAT")
    return {
        "direction": direction, "score": consensus, "confidence": abs(consensus),
        "long_score": long_score, "short_score": short_score,
        "long_count": long_count, "short_count": short_count, "flat_count": flat_count,
    }
