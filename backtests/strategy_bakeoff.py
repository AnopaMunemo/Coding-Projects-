"""
Strategy Bake-Off — pick the empirically best base for v9.

Runs 8 candidate strategies on REAL XAUUSD history (yfinance GC=F, GLD
fallback, synthetic last resort), all sharing the same backtest engine
and the same realistic friction (3 bps per side). Ranks them by net
Sharpe and writes the winner to backtests/winner.json.

Run:
  python3 backtests/strategy_bakeoff.py
  python3 backtests/strategy_bakeoff.py --ticker GLD
  python3 backtests/strategy_bakeoff.py --start 2018-01-01

Indicator math (_ema, _rsi, _atr) follows the same conventions as
versions/v8_goldbot.py. Backtest engine pattern follows
versions/v7_1_strategy_comparison.py with vol-targeting removed for
direct comparability.
"""
from __future__ import annotations
import os, sys, json, argparse, warnings
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
np.random.seed(42)

# ── Friction (XAUUSD futures / CFD realistic retail) ────────────────────
FRICTION_PER_SIDE = 0.0003    # 3 bps spread + slippage
INITIAL_CAPITAL   = 10_000.0  # USD
RF_ANNUAL         = 0.045     # current US risk-free


# ── Indicators ──────────────────────────────────────────────────────────
def _ema(x: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i-1] * (1 - k)
    return out


def _rsi_series(x: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI computed for every bar (Wilder's smoothing)."""
    n = len(x)
    out = np.full(n, 50.0)
    deltas = np.diff(x)
    gains  = np.where(deltas > 0,  deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    if n <= period + 1:
        return out
    ag = gains[:period].mean()
    al = losses[:period].mean()
    alpha = 1.0 / period
    for i in range(period, n - 1):
        ag = alpha * gains[i] + (1 - alpha) * ag
        al = alpha * losses[i] + (1 - alpha) * al
        out[i + 1] = 100.0 if al < 1e-12 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def _atr_series(x: np.ndarray, period: int = 14) -> np.ndarray:
    """ATR approximation using close-to-close (we only have close)."""
    n = len(x)
    out = np.zeros(n)
    abs_diff = np.abs(np.diff(x))
    for i in range(period, n):
        out[i] = abs_diff[i - period:i].mean()
    return out


def _rolling_max(x: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(x).rolling(period, min_periods=1).max().to_numpy()


def _rolling_min(x: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(x).rolling(period, min_periods=1).min().to_numpy()


# ── Data ────────────────────────────────────────────────────────────────
def fetch_prices(ticker: str, start: str) -> Tuple[np.ndarray, str]:
    """Returns (close_prices, source_label)."""
    try:
        import yfinance as yf
        for tk in [ticker, "GLD"]:
            try:
                df = yf.download(tk, start=start, auto_adjust=True,
                                 progress=False)
                if df is None or df.empty:
                    continue
                if isinstance(df.columns, pd.MultiIndex):
                    df = df["Close"]
                    if isinstance(df, pd.DataFrame):
                        df = df.iloc[:, 0]
                else:
                    df = df["Close"]
                arr = df.dropna().to_numpy(dtype=float)
                if len(arr) > 200:
                    return arr, f"yfinance:{tk}"
            except Exception as e:
                print(f"  yfinance {tk}: {e}")
                continue
    except ImportError:
        print("  yfinance not installed — using synthetic gold path")

    # Synthetic fallback (gold-calibrated, deterministic)
    rng = np.random.default_rng(42)
    n = 2700
    rets = rng.normal(0.00035, 0.011, n)
    arr = 1200.0 * np.exp(np.cumsum(rets))
    return arr, "synthetic-gbm"


# ── Strategy interface ──────────────────────────────────────────────────
class Strategy:
    name: str = "base"
    def precompute(self, prices: np.ndarray) -> None:
        """Optional: pre-compute indicators for the whole series."""
        pass
    def target_weight(self, t: int, prices: np.ndarray) -> float:
        raise NotImplementedError


class BuyAndHold(Strategy):
    name = "buy_and_hold"
    def target_weight(self, t, prices):
        return 1.0


class TrendEMA(Strategy):
    def __init__(self, fast: int, slow: int):
        self.fast, self.slow = fast, slow
        self.name = f"trend_ema_{fast}_{slow}"
    def precompute(self, prices):
        self.f = _ema(prices, self.fast)
        self.s = _ema(prices, self.slow)
    def target_weight(self, t, prices):
        if t < self.slow + 2:
            return 0.0
        return 1.0 if self.f[t] > self.s[t] else 0.0


class TrendEMAWithATRStop(Strategy):
    """EMA(fast)>EMA(slow) entry, exit on cross or peak-since-entry − k×ATR."""
    name = "trend_ema_20_50_atr_stop"
    def __init__(self, fast=20, slow=50, atr_mult=2.0):
        self.fast, self.slow, self.atr_mult = fast, slow, atr_mult
        self.peak = 0.0
        self.in_pos = False
    def precompute(self, prices):
        self.f = _ema(prices, self.fast)
        self.s = _ema(prices, self.slow)
        self.atr = _atr_series(prices, 14)
        self.peak = 0.0
        self.in_pos = False
    def target_weight(self, t, prices):
        if t < self.slow + 2:
            return 0.0
        long_signal = self.f[t] > self.s[t]
        if self.in_pos:
            self.peak = max(self.peak, prices[t])
            stop_hit = prices[t] < self.peak - self.atr_mult * self.atr[t]
            if (not long_signal) or stop_hit:
                self.in_pos = False
                self.peak = 0.0
                return 0.0
            return 1.0
        else:
            if long_signal:
                self.in_pos = True
                self.peak = prices[t]
                return 1.0
            return 0.0


class RSIMeanRevert(Strategy):
    name = "rsi_mean_revert"
    def __init__(self, low=30, high=70):
        self.low, self.high = low, high
    def precompute(self, prices):
        self.rsi = _rsi_series(prices, 14)
        self.holding = False
    def target_weight(self, t, prices):
        if t < 30:
            return 0.0
        r = self.rsi[t]
        if not self.holding and r < self.low:
            self.holding = True
        elif self.holding and r > self.high:
            self.holding = False
        return 1.0 if self.holding else 0.0


class DonchianBreakout(Strategy):
    name = "donchian_breakout_20"
    def __init__(self, entry=20, exit_=10):
        self.entry, self.exit_ = entry, exit_
    def precompute(self, prices):
        # Use prior-bar highs/lows (lag by 1) to avoid look-ahead at entry bar
        self.hi = _rolling_max(prices, self.entry)
        self.lo = _rolling_min(prices, self.exit_)
        self.hi = np.concatenate([[prices[0]], self.hi[:-1]])
        self.lo = np.concatenate([[prices[0]], self.lo[:-1]])
        self.holding = False
    def target_weight(self, t, prices):
        if t < self.entry + 2:
            return 0.0
        if not self.holding and prices[t] > self.hi[t]:
            self.holding = True
        elif self.holding and prices[t] < self.lo[t]:
            self.holding = False
        return 1.0 if self.holding else 0.0


class VolTargetedTrend(Strategy):
    """EMA 50/200 trend with realized-vol-targeted sizing."""
    name = "vol_targeted_trend"
    def __init__(self, target_vol=0.15):
        self.target_vol = target_vol
    def precompute(self, prices):
        self.f = _ema(prices, 50)
        self.s = _ema(prices, 200)
        log_ret = np.diff(np.log(prices))
        rv = pd.Series(log_ret).rolling(60).std().to_numpy() * np.sqrt(252)
        # align: rv[i] corresponds to prices[i+1]; pad front
        self.rv = np.concatenate([[np.nan], rv])
    def target_weight(self, t, prices):
        if t < 210:
            return 0.0
        if not (self.f[t] > self.s[t]):
            return 0.0
        rv = self.rv[t]
        if not np.isfinite(rv) or rv <= 0:
            return 0.0
        return float(np.clip(self.target_vol / rv, 0, 1.0))


class MLLogistic(Strategy):
    """
    Walk-forward logistic regression on 5 features.
    Refits every 252 bars using ONLY data strictly before bar t.
    No look-ahead.
    """
    name = "ml_logistic"
    REFIT_EVERY = 252
    MIN_TRAIN = 504
    def __init__(self, threshold=0.55):
        self.threshold = threshold
        self.model = None
        self.scaler = None
        self.last_fit_t = -1
    def precompute(self, prices):
        self.prices = prices
        log_ret = np.diff(np.log(prices))
        self.log_ret = np.concatenate([[0.0], log_ret])
        self.ema20 = _ema(prices, 20)
        self.ema50 = _ema(prices, 50)
        self.ema200 = _ema(prices, 200)
        self.rsi = _rsi_series(prices, 14)
        rv = pd.Series(self.log_ret).rolling(20).std().to_numpy() * np.sqrt(252)
        self.rv = np.nan_to_num(rv, nan=0.0)
        vov = pd.Series(self.log_ret).rolling(20).std().rolling(20).std().to_numpy()
        self.vov = np.nan_to_num(vov, nan=0.0)
        self.model = None
        self.last_fit_t = -1

    def _features_at(self, t: int) -> np.ndarray:
        p = self.prices[t]
        ema_trend_short = (self.ema20[t] - self.ema50[t]) / (p + 1e-8)
        ema_trend_long  = (self.ema50[t] - self.ema200[t]) / (p + 1e-8)
        mom20 = 0.0 if t < 20 else (p - self.prices[t-20]) / (self.prices[t-20] + 1e-8)
        return np.array([ema_trend_short, ema_trend_long,
                          (self.rsi[t] - 50) / 50, mom20,
                          self.rv[t]], dtype=float)

    def _fit_up_to(self, t: int):
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        X, y = [], []
        # Train ONLY on bars where we can know next-bar label, and only on
        # bars STRICTLY before t (no look-ahead).
        for i in range(210, t - 1):
            X.append(self._features_at(i))
            y.append(1 if self.prices[i + 1] > self.prices[i] else 0)
        if len(X) < 100 or len(set(y)) < 2:
            self.model = None
            return
        X = np.array(X); y = np.array(y)
        mask = np.isfinite(X).all(axis=1)
        X, y = X[mask], y[mask]
        self.scaler = StandardScaler().fit(X)
        self.model = LogisticRegression(penalty="l2", C=0.5, max_iter=500,
                                          random_state=42,
                                          class_weight="balanced")
        self.model.fit(self.scaler.transform(X), y)
        self.last_fit_t = t

    def target_weight(self, t, prices):
        if t < self.MIN_TRAIN:
            return 0.0
        if self.model is None or (t - self.last_fit_t) >= self.REFIT_EVERY:
            self._fit_up_to(t)
        if self.model is None:
            return 0.0
        x = self._features_at(t).reshape(1, -1)
        if not np.isfinite(x).all():
            return 0.0
        p_up = float(self.model.predict_proba(self.scaler.transform(x))[0, 1])
        return 1.0 if p_up >= self.threshold else 0.0


class EnsembleVote(Strategy):
    """Long when ≥ 2 of {trend50/200, donchian20, ml_logistic} agree."""
    name = "ensemble_vote"
    def __init__(self):
        self.trend = TrendEMA(50, 200)
        self.donch = DonchianBreakout(20, 10)
        self.ml = MLLogistic(threshold=0.55)
    def precompute(self, prices):
        self.trend.precompute(prices)
        self.donch.precompute(prices)
        self.ml.precompute(prices)
    def target_weight(self, t, prices):
        votes = (
            (self.trend.target_weight(t, prices) > 0.5) +
            (self.donch.target_weight(t, prices) > 0.5) +
            (self.ml.target_weight(t, prices)    > 0.5)
        )
        return 1.0 if votes >= 2 else 0.0


# ── Engine ──────────────────────────────────────────────────────────────
@dataclass
class BTResult:
    name: str
    nav: np.ndarray
    n_trades: int
    trades: List[Dict] = field(default_factory=list)

    def stats(self, periods_per_year: int = 252) -> Dict:
        n = len(self.nav)
        if n < 2:
            return {"name": self.name, "sharpe": 0, "sortino": 0,
                    "cagr": 0, "ann_vol": 0, "max_dd": 0, "calmar": 0,
                    "total_return": 0, "final_nav": float(self.nav[-1]),
                    "n_trades": self.n_trades, "n_bars": n}
        rets = np.diff(self.nav) / self.nav[:-1]
        total_ret = self.nav[-1] / self.nav[0] - 1
        years = n / periods_per_year
        cagr = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
        vol = float(np.std(rets) * np.sqrt(periods_per_year))
        sharpe = (cagr - RF_ANNUAL) / vol if vol > 0 else 0
        neg = rets[rets < 0]
        dvol = float(np.std(neg) * np.sqrt(periods_per_year)) if len(neg) > 1 else 0
        sortino = (cagr - RF_ANNUAL) / dvol if dvol > 0 else 0
        peak = np.maximum.accumulate(self.nav)
        dd = (self.nav - peak) / (peak + 1e-8)
        max_dd = float(dd.min())
        calmar = cagr / abs(max_dd) if abs(max_dd) > 1e-6 else 0
        return {
            "name": self.name,
            "total_return": float(total_ret),
            "cagr": float(cagr),
            "ann_vol": vol,
            "sharpe": float(sharpe),
            "sortino": float(sortino),
            "max_dd": max_dd,
            "calmar": float(calmar),
            "final_nav": float(self.nav[-1]),
            "n_trades": self.n_trades,
            "n_bars": n,
        }


def run_strategy(prices: np.ndarray, strat: Strategy,
                  rebalance_band: float = 0.10) -> BTResult:
    strat.precompute(prices)
    cash = INITIAL_CAPITAL
    units = 0.0
    nav_hist = np.zeros(len(prices))
    trades = []
    cur_w = 0.0
    for t in range(len(prices)):
        target = float(np.clip(strat.target_weight(t, prices), 0.0, 1.0))
        nav = cash + units * prices[t]
        cur_w = (units * prices[t]) / nav if nav > 0 else 0.0
        if abs(target - cur_w) > rebalance_band:
            target_value = target * nav
            current_value = units * prices[t]
            if target_value > current_value:
                # buy
                buy_value = target_value - current_value
                eff_price = prices[t] * (1 + FRICTION_PER_SIDE)
                buy_units = buy_value / eff_price
                cost = buy_units * eff_price
                if cost <= cash + 1e-6:
                    cash -= cost
                    units += buy_units
                    trades.append({"t": t, "side": "BUY", "px": eff_price,
                                    "u": buy_units})
            else:
                # sell
                sell_value = current_value - target_value
                eff_price = prices[t] * (1 - FRICTION_PER_SIDE)
                sell_units = min(units, sell_value / eff_price)
                cash += sell_units * eff_price
                units -= sell_units
                trades.append({"t": t, "side": "SELL", "px": eff_price,
                                "u": sell_units})
        nav_hist[t] = cash + units * prices[t]

    # Close any final position
    if units > 0:
        eff_price = prices[-1] * (1 - FRICTION_PER_SIDE)
        cash += units * eff_price
        trades.append({"t": len(prices)-1, "side": "SELL_FINAL",
                        "px": eff_price, "u": units})
        units = 0
        nav_hist[-1] = cash

    return BTResult(strat.name, nav_hist, len(trades), trades)


# ── Reporting ───────────────────────────────────────────────────────────
def print_table(stats_list: List[Dict], window_label: str):
    print(f"\n{'═'*98}")
    print(f"  {window_label}")
    print('═'*98)
    print(f"  {'Strategy':<26} {'TotRet':>9} {'CAGR':>8} {'Vol':>7} "
          f"{'Sharpe':>8} {'Sortino':>8} {'MaxDD':>8} {'Calmar':>7} "
          f"{'Trades':>7}")
    print('─'*98)
    sorted_by_sharpe = sorted(stats_list, key=lambda r: r["sharpe"], reverse=True)
    best_name = sorted_by_sharpe[0]["name"]
    for s in sorted_by_sharpe:
        marker = "🥇" if s["name"] == best_name else "  "
        print(f"  {marker}{s['name']:<24} "
              f"{s['total_return']*100:>8.1f}% "
              f"{s['cagr']*100:>7.1f}% "
              f"{s['ann_vol']*100:>6.1f}% "
              f"{s['sharpe']:>8.2f} "
              f"{s['sortino']:>8.2f} "
              f"{s['max_dd']*100:>7.1f}% "
              f"{s['calmar']:>7.2f} "
              f"{s['n_trades']:>7}")
    print('═'*98)


def honest_verdict(stats_list: List[Dict]):
    bh = next((s for s in stats_list if s["name"] == "buy_and_hold"), None)
    if not bh:
        return
    print("\n  HONEST VERDICT (vs Buy-and-Hold, net of friction)")
    print('─'*98)
    beat_count = 0
    for s in sorted(stats_list, key=lambda r: r["sharpe"], reverse=True):
        if s["name"] == "buy_and_hold":
            continue
        alpha_cagr = s["cagr"] - bh["cagr"]
        sharpe_diff = s["sharpe"] - bh["sharpe"]
        beats = sharpe_diff > 0.05
        if beats:
            beat_count += 1
        tag = "✓ beats B&H" if beats else "✗ does not beat B&H"
        print(f"  {s['name']:<26} {tag:<22} "
              f"αCAGR={alpha_cagr*100:+.2f}%  ΔSharpe={sharpe_diff:+.2f}")
    print('─'*98)
    print(f"  {beat_count} of {len(stats_list)-1} strategies beat buy-and-hold "
          f"on net Sharpe.\n")


# ── Main ────────────────────────────────────────────────────────────────
def make_strategies() -> List[Strategy]:
    return [
        BuyAndHold(),
        TrendEMA(50, 200),
        TrendEMAWithATRStop(20, 50, 2.0),
        RSIMeanRevert(30, 70),
        DonchianBreakout(20, 10),
        VolTargetedTrend(0.15),
        MLLogistic(threshold=0.55),
        EnsembleVote(),
    ]


def run_window(prices: np.ndarray, label: str) -> List[Dict]:
    results = []
    for strat in make_strategies():
        r = run_strategy(prices, strat)
        results.append(r.stats())
    print_table(results, label)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="GC=F",
                     help="yfinance ticker (default GC=F)")
    ap.add_argument("--start", default="2015-01-01",
                     help="start date YYYY-MM-DD (default 2015-01-01)")
    ap.add_argument("--out", default="backtests/winner.json")
    args = ap.parse_args()

    print(f"\n📥  Fetching {args.ticker} from {args.start}…")
    prices, source = fetch_prices(args.ticker, args.start)
    print(f"   source = {source}   bars = {len(prices)}   "
          f"first = {prices[0]:.2f}   last = {prices[-1]:.2f}")

    # Full window
    full = run_window(prices, f"FULL WINDOW  ({len(prices)} bars,  {source})")
    honest_verdict(full)

    # Sub-window robustness — only if we have enough bars
    if len(prices) > 252 * 4:
        n = len(prices)
        bull_end = min(n, 252 * 5)
        side_start = max(0, n - 252 * 3)
        bull = run_window(prices[:bull_end],
                          f"BULL-WEIGHTED SUB-WINDOW (first ~5y, {bull_end} bars)")
        side = run_window(prices[side_start:],
                          f"RECENT SUB-WINDOW (last ~3y, {n - side_start} bars)")
    else:
        bull, side = None, None

    # Persist winner from full window
    winner = sorted(full, key=lambda r: r["sharpe"], reverse=True)[0]
    payload = {
        "name":         winner["name"],
        "sharpe":       round(winner["sharpe"], 4),
        "sortino":      round(winner["sortino"], 4),
        "cagr":         round(winner["cagr"], 6),
        "ann_vol":      round(winner["ann_vol"], 6),
        "max_dd":       round(winner["max_dd"], 6),
        "calmar":       round(winner["calmar"], 4),
        "total_return": round(winner["total_return"], 6),
        "n_trades":     winner["n_trades"],
        "n_bars":       winner["n_bars"],
        "ticker":       args.ticker,
        "data_source":  source,
        "start":        args.start,
        "ran_on":       datetime.now().isoformat(timespec="seconds"),
        "friction_per_side": FRICTION_PER_SIDE,
        "bh_sharpe":    round(next(s for s in full if s["name"] == "buy_and_hold")["sharpe"], 4),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\n📄  Winner persisted to {args.out}: {winner['name']} "
          f"(Sharpe {winner['sharpe']:.2f}, CAGR {winner['cagr']*100:.1f}%)")
    print("\n  Disclaimer: educational / research only. Not financial advice.\n")


if __name__ == "__main__":
    main()
