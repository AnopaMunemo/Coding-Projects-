"""
v7.2 — Backtest using REAL historical gold prices (monthly, 2000-2026).
Compares:
  1. Pure buy-and-hold (no rebalancing)
  2. Trend-following (50/200 EMA crossover, binary on/off)
  3. Trend-following + simple cointegration overlay

Note: this runs at monthly granularity because the freely-available
historical data is monthly. The bot logic is the same; just slower
signal updates.
"""
from __future__ import annotations
import sys, logging, warnings
from typing import Dict, List, Tuple, Optional
import numpy as np
import pandas as pd
import requests
from io import StringIO

warnings.filterwarnings("ignore")

def _log():
    lg = logging.getLogger("v72")
    if not lg.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        lg.addHandler(h)
    lg.setLevel(logging.INFO)
    return lg
LOG = _log()

# JSE retail friction (per side)
BUY_COST  = 0.0050 + 0.00125 + 0.0010 + 0.0025  # brokerage + half-spread + slippage + STT
SELL_COST = 0.0050 + 0.00125 + 0.0010           # no STT on sell
INITIAL_CAPITAL = 100_000.0

# ──────────────────────────────────────────────────────────────────────
def ema(x, period):
    k = 2.0 / (period + 1)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i-1] * (1 - k)
    return out

def fetch_real_gold() -> pd.DataFrame:
    """Pulls monthly USD gold prices from the public GitHub dataset (1833-2026)."""
    url = "https://raw.githubusercontent.com/datasets/gold-prices/master/data/monthly.csv"
    r = requests.get(url, timeout=10)
    df = pd.read_csv(StringIO(r.text), parse_dates=["Date"])
    df = df.set_index("Date").sort_index()
    df = df[df.index >= "2000-01-01"]   # focus on modern period
    return df

# ──────────────────────────────────────────────────────────────────────
class BacktestResult:
    def __init__(self, name, nav, prices, trades):
        self.name = name
        self.nav = np.array(nav, dtype=float)
        self.prices = np.array(prices, dtype=float)
        self.trades = trades

    def stats(self) -> Dict:
        # Monthly returns
        rets = np.diff(self.nav) / self.nav[:-1]
        n_months = len(self.nav)
        n_years = n_months / 12
        total_ret = self.nav[-1] / self.nav[0] - 1
        ann_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
        ann_vol = float(np.std(rets) * np.sqrt(12)) if len(rets) > 1 else 0
        sharpe = (ann_ret - 0.07) / ann_vol if ann_vol > 0 else 0
        # Drawdown
        peak = np.maximum.accumulate(self.nav)
        dd = (self.nav - peak) / (peak + 1e-8)
        max_dd = float(dd.min())
        neg_rets = rets[rets < 0]
        downside_vol = float(np.std(neg_rets) * np.sqrt(12)) if len(neg_rets) > 1 else 0
        sortino = (ann_ret - 0.07) / downside_vol if downside_vol > 0 else 0
        return {
            "name": self.name,
            "months": n_months, "years": n_years,
            "total_return": total_ret, "ann_return": ann_ret,
            "ann_vol": ann_vol, "sharpe": sharpe, "sortino": sortino,
            "max_drawdown": max_dd, "final_nav": self.nav[-1],
            "n_trades": len(self.trades)
        }

def backtest_buyhold(prices: np.ndarray) -> BacktestResult:
    """Pure buy-and-hold: buy at start, sell at end."""
    units = INITIAL_CAPITAL / (prices[0] * (1 + BUY_COST))
    nav = []
    for p in prices:
        nav.append(units * p)
    # Close at end (subtract sell cost)
    nav[-1] = nav[-1] * (1 - SELL_COST)
    return BacktestResult("buy_and_hold", nav, prices,
                          [{"t": 0, "side": "BUY"}, {"t": len(prices)-1, "side": "SELL"}])

def backtest_trend(prices: np.ndarray, fast: int = 5, slow: int = 20) -> BacktestResult:
    """
    Trend-following on monthly data:
      fast=5 months, slow=20 months (roughly equivalent to 100/400 day on daily data)
    Long when fast EMA > slow EMA, flat otherwise.
    """
    cash = INITIAL_CAPITAL
    units = 0
    nav = []
    trades = []
    f = ema(prices, fast)
    s = ema(prices, slow)
    for t in range(len(prices)):
        if t < slow + 2:
            nav.append(cash + units * prices[t])
            continue
        long_signal = f[t] > s[t]
        if long_signal and units == 0:
            # Buy
            effective = prices[t] * (1 + BUY_COST)
            units = int(cash / effective)
            cash -= units * effective
            trades.append({"t": t, "side": "BUY", "price": effective})
        elif not long_signal and units > 0:
            # Sell
            effective = prices[t] * (1 - SELL_COST)
            cash += units * effective
            trades.append({"t": t, "side": "SELL", "price": effective})
            units = 0
        nav.append(cash + units * prices[t])
    # Close final position
    if units > 0:
        nav[-1] = cash + units * prices[-1] * (1 - SELL_COST)
    return BacktestResult(f"trend_following_{fast}_{slow}", nav, prices, trades)

def backtest_trend_with_stop(prices: np.ndarray, fast: int = 5, slow: int = 20,
                              trailing_stop_pct: float = 0.15) -> BacktestResult:
    """
    Trend-following + trailing stop:
      Buy on golden cross (fast > slow)
      Sell on death cross OR if price falls > trailing_stop_pct from peak since entry
    """
    cash = INITIAL_CAPITAL
    units = 0
    nav = []
    trades = []
    f = ema(prices, fast)
    s = ema(prices, slow)
    peak_since_entry = 0.0
    for t in range(len(prices)):
        if t < slow + 2:
            nav.append(cash + units * prices[t])
            continue
        long_signal = f[t] > s[t]
        # If in position, update trailing peak
        if units > 0:
            peak_since_entry = max(peak_since_entry, prices[t])
            stop_triggered = prices[t] < peak_since_entry * (1 - trailing_stop_pct)
            if not long_signal or stop_triggered:
                effective = prices[t] * (1 - SELL_COST)
                cash += units * effective
                trades.append({"t": t, "side": "SELL", "price": effective,
                              "reason": "stop" if stop_triggered else "cross"})
                units = 0
                peak_since_entry = 0.0
        elif long_signal and units == 0:
            effective = prices[t] * (1 + BUY_COST)
            units = int(cash / effective)
            cash -= units * effective
            peak_since_entry = prices[t]
            trades.append({"t": t, "side": "BUY", "price": effective})
        nav.append(cash + units * prices[t])
    if units > 0:
        nav[-1] = cash + units * prices[-1] * (1 - SELL_COST)
    return BacktestResult(f"trend_stop_{fast}_{slow}", nav, prices, trades)

# ──────────────────────────────────────────────────────────────────────
def print_results(results: List[BacktestResult]):
    print("\n" + "═" * 96)
    print(f"  REAL GOLD HISTORICAL BACKTEST ({results[0].nav.shape[0]} months, "
          f"~{results[0].nav.shape[0]/12:.1f} years)")
    print(f"  All numbers are NET of JSE friction: buy={BUY_COST*100:.2f}%, sell={SELL_COST*100:.2f}%")
    print("═" * 96)
    hdr = f"  {'Strategy':<26} {'TotRet':>10} {'AnnRet':>10} {'Vol':>8} {'Sharpe':>8} {'Sortino':>8} {'MaxDD':>9} {'Trades':>7}"
    print(hdr)
    print("─" * 96)
    for r in results:
        s = r.stats()
        print(f"  {s['name']:<26} "
              f"{s['total_return']*100:>9.1f}% "
              f"{s['ann_return']*100:>9.1f}% "
              f"{s['ann_vol']*100:>7.1f}% "
              f"{s['sharpe']:>8.2f} "
              f"{s['sortino']:>8.2f} "
              f"{s['max_drawdown']*100:>8.1f}% "
              f"{s['n_trades']:>7}")
    print("═" * 96)

def print_final_nav(results, initial=INITIAL_CAPITAL):
    print(f"\n  STARTING WITH R{initial:,.0f}, ENDING NAV:")
    for r in results:
        s = r.stats()
        emoji = "🥇" if s["final_nav"] == max(rr.stats()["final_nav"] for rr in results) else "  "
        print(f"  {emoji} {s['name']:<26} R{s['final_nav']:>15,.2f}  "
              f"(×{s['final_nav']/initial:.2f})")

# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    LOG.info("Fetching real monthly gold prices (USD) from GitHub mirror...")
    df = fetch_real_gold()
    LOG.info(f"Got {len(df)} months: {df.index[0].strftime('%Y-%m')} → {df.index[-1].strftime('%Y-%m')}")
    LOG.info(f"Start price: ${df['Price'].iloc[0]:.2f}, End price: ${df['Price'].iloc[-1]:.2f}")

    prices = df["Price"].values

    LOG.info("Running 3 strategies on REAL gold data...")
    results = [
        backtest_buyhold(prices),
        backtest_trend(prices, fast=5, slow=20),         # ~roughly 100d/400d on daily
        backtest_trend_with_stop(prices, fast=5, slow=20, trailing_stop_pct=0.15),
        backtest_trend(prices, fast=3, slow=12),         # faster signals
        backtest_trend_with_stop(prices, fast=3, slow=12, trailing_stop_pct=0.20),
    ]

    print_results(results)
    print_final_nav(results)

    # Honest verdict
    bh_stats = results[0].stats()
    print(f"\n  HONEST VERDICT (vs Buy & Hold's Sharpe of {bh_stats['sharpe']:.2f}):")
    for r in results[1:]:
        s = r.stats()
        alpha = s['ann_return'] - bh_stats['ann_return']
        sharpe_diff = s['sharpe'] - bh_stats['sharpe']
        dd_improve = bh_stats['max_drawdown'] - s['max_drawdown']
        verdict_parts = []
        if alpha > 0.005:
            verdict_parts.append(f"+{alpha*100:.1f}%/yr alpha")
        elif alpha < -0.005:
            verdict_parts.append(f"{alpha*100:.1f}%/yr alpha")
        else:
            verdict_parts.append("~0 alpha")
        if sharpe_diff > 0.1:
            verdict_parts.append(f"+{sharpe_diff:.2f} Sharpe (better)")
        elif sharpe_diff < -0.1:
            verdict_parts.append(f"{sharpe_diff:+.2f} Sharpe (worse)")
        if dd_improve > 0.05:
            verdict_parts.append(f"smaller max DD (better by {dd_improve*100:.1f}%)")
        print(f"  • {s['name']:<26}: " + " | ".join(verdict_parts))

    print(f"\n  CONTEXT:")
    n_years = bh_stats['years']
    print(f"  Over {n_years:.1f} years of real gold data:")
    print(f"  - Just holding gold turned R{INITIAL_CAPITAL:,.0f} into "
          f"R{bh_stats['final_nav']:,.0f} (×{bh_stats['final_nav']/INITIAL_CAPITAL:.2f})")
    print(f"  - That is {bh_stats['ann_return']*100:.1f}% annual return at "
          f"{bh_stats['ann_vol']*100:.1f}% annual volatility")
    print(f"  - The biggest peak-to-trough loss along the way was {bh_stats['max_drawdown']*100:.1f}%")
    print()
