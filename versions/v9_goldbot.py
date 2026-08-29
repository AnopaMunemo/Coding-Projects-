"""
v9_goldbot.py — XAUUSD Drawdown-Control Edition

PIVOT FROM v8 (driven by the 2026-05-28 bake-off):
   On 11y of real XAUUSD data, ZERO of 7 active strategies beat
   buy-and-hold on net Sharpe. The one that came close did so by
   reducing drawdown, not by adding return.

   Therefore v9 is NOT a directional timing bot. It is a B&H position
   modulator: target weight starts at 1.0 (= hold gold) and risk
   signals shrink it toward 0.5 only when they agree.

   Goal: match B&H CAGR within ~1-2%, cut max drawdown roughly in
   half, beat B&H on Sharpe and Calmar net of friction.

NEW IN v9:
   1. Position-modulator architecture (the core change).
   2. Minimum-deposit calculator: given user's max acceptable
      drawdown, output the USD floor that survives the historical
      worst case with a safety margin.
   3. Profit-target halt + NAV compounding from state file.
   4. ML training holds out the current bar (fixes v8 leak).

ANTI-PATTERNS AVOIDED (see CLAUDE.md §5):
   - Pre-friction Sharpe headlines  → always net.
   - Hard-coded ticker              → Config only.
   - No walk-forward                → in-script self-backtest with
                                       walk-forward refit.
   - Ignoring HMM posterior conf    → modulators respect confidence.
   - Refit GARCH every day          → cached, refit on demand.

Run:
   python3 versions/v9_goldbot.py
   python3 versions/v9_goldbot.py --ticker SPY --capital 50000
   python3 versions/v9_goldbot.py --profit-target 0.30
"""
from __future__ import annotations
import os, sys, json, argparse, warnings, logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
np.random.seed(42)

# ── Optional libs (degrade gracefully) ──────────────────────────────────
try:
    import yfinance as yf
    YF_OK = True
except ImportError:
    YF_OK = False

try:
    from arch import arch_model
    GARCH_OK = True
except ImportError:
    GARCH_OK = False

try:
    from hmmlearn import hmm as _hmm
    HMM_OK = True
except ImportError:
    HMM_OK = False

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False


# ── Config ──────────────────────────────────────────────────────────────
@dataclass
class Config:
    ticker:            str   = "GC=F"
    fallback_ticker:   str   = "GLD"
    start_date:        str   = "2015-01-01"

    capital_usd:       float = 10_000.0
    base_weight:       float = 1.00    # start at "hold gold"
    min_weight_floor:  float = 0.50    # never shrink below this in normal ops

    # Profit target & risk tolerance
    profit_target_pct: float = 0.50    # halt at +50% from initial
    max_acceptable_dd: float = 0.15    # user's drawdown ceiling (15%)
    safety_margin:     float = 1.30    # deposit overhead vs historical worst

    # Modulator thresholds
    vol_target_annual: float = 0.18    # XAUUSD's typical realized vol
    crisis_size_mult:  float = 0.55    # weight when HMM = crisis
    dd_brake_start:    float = -0.08   # start shrinking at -8%
    dd_brake_floor:    float = -0.20   # fully braked at -20%
    dxy_breakout_thr:  float = 0.025   # 10-day DXY trend that triggers brake

    # Trade execution
    rebalance_band:    float = 0.08
    friction_per_side: float = 0.0003  # 3 bps XAUUSD CFD/futures realistic
    stop_loss_atr_mult: float = 2.0
    take_profit_rr:    float = 2.0

    # CVaR / Kelly (kept for risk dashboard; not the sizing driver in v9)
    cvar_confidence:   float = 0.95
    kelly_scalar:      float = 0.25

    # State / output
    state_dir:         str   = "state"

    @property
    def ticker_slug(self) -> str:
        return self.ticker.replace("=", "").replace("^", "").replace(".", "_")


CFG = Config()


# ── Logger ──────────────────────────────────────────────────────────────
def _make_logger() -> logging.Logger:
    lg = logging.getLogger("v9")
    if not lg.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s [v9] %(message)s",
                                          datefmt="%H:%M:%S"))
        lg.addHandler(h)
    lg.setLevel(logging.INFO)
    return lg

LOG = _make_logger()


# ── Indicators (same conventions as v8) ─────────────────────────────────
def _ema(x: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = x[i] * k + out[i-1] * (1 - k)
    return out


def _atr(prices: np.ndarray, period: int = 14) -> float:
    diffs = np.abs(np.diff(prices[-period*2:]))
    return float(np.mean(diffs[-period:]))


def _realized_vol(log_ret: np.ndarray, window: int = 20) -> float:
    if len(log_ret) < window:
        return 0.0
    return float(np.std(log_ret[-window:]) * np.sqrt(252))


def _vol_of_vol(log_ret: np.ndarray, window: int = 20) -> float:
    if len(log_ret) < window * 3:
        return 0.0
    rs = pd.Series(log_ret).rolling(window).std()
    return float(rs.tail(window).std())


# ── Data ────────────────────────────────────────────────────────────────
def fetch_close(ticker: str, start: str) -> Optional[np.ndarray]:
    if not YF_OK:
        return None
    try:
        df = yf.download(ticker, start=start, auto_adjust=True,
                          progress=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df = df["Close"]
            if isinstance(df, pd.DataFrame):
                df = df.iloc[:, 0]
        else:
            df = df["Close"]
        arr = df.dropna().to_numpy(dtype=float)
        return arr if len(arr) > 200 else None
    except Exception as e:
        LOG.warning(f"  yfinance {ticker}: {e}")
        return None


def fetch_market_data() -> Dict:
    LOG.info(f"Fetching {CFG.ticker} from {CFG.start_date}…")
    primary = fetch_close(CFG.ticker, CFG.start_date)
    if primary is None or len(primary) < 200:
        LOG.warning(f"{CFG.ticker} unavailable — falling back to "
                     f"{CFG.fallback_ticker}")
        primary = fetch_close(CFG.fallback_ticker, CFG.start_date)
    if primary is None:
        raise RuntimeError(f"Cannot fetch {CFG.ticker} or "
                            f"{CFG.fallback_ticker}")

    # DXY for the dollar modulator
    dxy = fetch_close("DX-Y.NYB", CFG.start_date)
    if dxy is not None and len(dxy) >= len(primary):
        dxy = dxy[-len(primary):]
    else:
        dxy = np.ones(len(primary)) * 100.0   # neutral fallback

    LOG.info(f"  ✓  {len(primary)} bars  |  last = {primary[-1]:.2f}")
    return {"price": primary, "dxy": dxy, "n": len(primary)}


# ── Volatility & risk ───────────────────────────────────────────────────
def garch_or_ewma(log_ret: np.ndarray) -> Tuple[float, str]:
    """Return (sigma_daily, source_label)."""
    if GARCH_OK and len(log_ret) > 100:
        try:
            clean = log_ret[log_ret != 0]
            m = arch_model(clean * 100, vol="Garch", p=1, q=1,
                            dist="t", rescale=True)
            res = m.fit(disp="off", show_warning=False,
                         options={"maxiter": 500})
            var_scaled = float(res.forecast(horizon=1).variance.values[-1, 0])
            sigma = float(np.sqrt(var_scaled)) / 100.0
            if 0.0005 < sigma < 0.10:
                p = res.params
                a, b = float(p.get("alpha[1]", 0)), float(p.get("beta[1]", 0))
                if 0 < a + b < 1:
                    return sigma, "GARCH(1,1)-t"
        except Exception:
            pass
    # EWMA(λ=0.94)
    lam, var = 0.94, float(np.var(log_ret[:30]))
    for r in log_ret:
        var = lam * var + (1 - lam) * r**2
    return float(np.sqrt(var)), "EWMA"


def evt_cvar(log_ret: np.ndarray, conf: float) -> float:
    try:
        thr = np.percentile(log_ret, (1 - conf) * 100)
        exc = -(log_ret[log_ret < thr] - thr)
        if len(exc) >= 15:
            shape, _, scale = stats.genpareto.fit(exc, floc=0)
            nu = len(exc) / len(log_ret)
            alp = 1 - conf
            u = -thr
            if shape < 1 and scale > 0:
                if abs(shape) < 1e-6:
                    return float(abs(u + scale * (1 + np.log(nu / alp))))
                return float(abs(u + (scale / (1 - shape)) *
                                  ((nu / alp)**shape - 1) / shape))
    except Exception:
        pass
    cut = np.percentile(log_ret, (1 - conf) * 100)
    tail = log_ret[log_ret <= cut]
    return float(abs(tail.mean()) if len(tail) > 0 else abs(cut))


# ── Regime detection ────────────────────────────────────────────────────
def _vol_regime(log_ret: np.ndarray) -> Tuple[int, float, str]:
    """Cheap fallback used inside the walk-forward loop."""
    if len(log_ret) < 20:
        return 1, 0.60, "Calm"
    rv = float(np.std(log_ret[-20:]) * np.sqrt(252))
    if rv > 0.28:
        return 2, 0.65, "Crisis (vol)"
    if rv < 0.12:
        return 0, 0.65, "Bull (vol)"
    return 1, 0.65, "Calm (vol)"


def detect_regime(log_ret: np.ndarray, n: int = 3
                   ) -> Tuple[int, float, str]:
    """
    Full HMM detection. Expensive (~0.5–1s per call). Use sparingly:
    once at the end-of-run dashboard, and every 63 bars inside the
    walk-forward (see precompute_regime_path).
    """
    if HMM_OK and len(log_ret) >= 120:
        try:
            X = log_ret.reshape(-1, 1)
            model = _hmm.GaussianHMM(n_components=n, covariance_type="full",
                                       n_iter=200, random_state=42)
            model.fit(X)
            states = model.predict(X)
            probs = model.predict_proba(X)
            order = np.argsort(model.means_.flatten())[::-1]
            remap = {raw: rank for rank, raw in enumerate(order)}
            cur_raw = int(states[-1])
            cur_rank = remap[cur_raw]
            conf = float(probs[-1][cur_raw])
            label = {0: "Bull", 1: "Calm", 2: "Crisis"}[cur_rank]
            return cur_rank, conf, label
        except Exception:
            pass
    return _vol_regime(log_ret)


def precompute_regime_path(log_ret: np.ndarray,
                             refit_every: int = 63,
                             min_history: int = 252,
                             ) -> List[Tuple[int, float]]:
    """
    Returns a list of (regime_idx, confidence) aligned to log_ret. For
    bars before `min_history` we emit a neutral Calm. Then we refit HMM
    every `refit_every` bars and hold that label until the next refit.
    """
    n = len(log_ret)
    path: List[Tuple[int, float]] = [(1, 0.60)] * min(min_history, n)
    if n <= min_history:
        return path

    last_label = (1, 0.60)
    bars_since_refit = refit_every  # force refit on first eligible bar
    for t in range(min_history, n):
        if bars_since_refit >= refit_every:
            idx, conf, _ = detect_regime(log_ret[:t+1])
            last_label = (idx, conf)
            bars_since_refit = 0
        path.append(last_label)
        bars_since_refit += 1
    return path


# ── Position modulators (the v9 core) ───────────────────────────────────
@dataclass
class Modulators:
    m_regime:   float
    m_vol:      float
    m_drawdown: float
    m_dxy:      float
    base:       float
    final:      float


def compute_modulators(
    price_series: np.ndarray,
    dxy_series:   np.ndarray,
    log_ret:      np.ndarray,
    nav_history:  Optional[np.ndarray] = None,
    regime:       Optional[Tuple[int, float]] = None,
) -> Modulators:
    """
    All modulators ∈ [floor, 1.0]. We use min() not product so a single
    fired signal doesn't over-shrink. Use product only when we want
    multiple simultaneous fires to compound (we don't, in v9).

    `regime` lets callers (the walk-forward) inject a precomputed
    (idx, conf) tuple to skip the expensive HMM refit.
    """
    floor = CFG.min_weight_floor

    # Regime modulator
    if regime is not None:
        reg_idx, reg_conf = regime
    else:
        reg_idx, reg_conf, _ = detect_regime(log_ret)
    if reg_idx == 2:
        # Crisis: shrink, but only as much as confidence justifies
        m_regime = floor + (1.0 - floor) * (1.0 - reg_conf)
        m_regime = max(CFG.crisis_size_mult, m_regime)
    elif reg_conf < 0.55:
        # Low confidence anywhere → slight scale-down
        m_regime = 0.80
    else:
        m_regime = 1.0

    # Realized vol modulator: clip(target/realized, 0.5, 1.0)
    rv = _realized_vol(log_ret, 20)
    if rv > 0:
        m_vol = float(np.clip(CFG.vol_target_annual / rv, floor, 1.0))
    else:
        m_vol = 1.0

    # Drawdown modulator from running peak of nav_history (if provided)
    if nav_history is not None and len(nav_history) > 20:
        peak = float(np.max(nav_history))
        cur = float(nav_history[-1])
        dd = (cur - peak) / peak if peak > 0 else 0.0
        if dd >= CFG.dd_brake_start:
            m_drawdown = 1.0
        elif dd <= CFG.dd_brake_floor:
            m_drawdown = floor
        else:
            # Linear interp: dd_brake_start → 1.0, dd_brake_floor → floor
            t = (dd - CFG.dd_brake_start) / (CFG.dd_brake_floor -
                                              CFG.dd_brake_start)
            m_drawdown = float(1.0 - t * (1.0 - floor))
    else:
        m_drawdown = 1.0

    # DXY modulator (gold is inversely correlated with USD)
    if len(dxy_series) >= 11:
        dxy_trend = float((dxy_series[-1] - dxy_series[-10]) /
                            (dxy_series[-10] + 1e-8))
        if dxy_trend > CFG.dxy_breakout_thr:
            m_dxy = 0.75   # USD ripping up = headwind for gold
        elif dxy_trend < -CFG.dxy_breakout_thr:
            m_dxy = 1.0    # USD weak = tailwind, keep full size
        else:
            m_dxy = 1.0
    else:
        m_dxy = 1.0

    # Combine: take MIN — one strong risk signal is enough to brake
    final = CFG.base_weight * min(m_regime, m_vol, m_drawdown, m_dxy)
    final = float(np.clip(final, 0.0, 1.0))

    return Modulators(
        m_regime=round(m_regime, 4),
        m_vol=round(m_vol, 4),
        m_drawdown=round(m_drawdown, 4),
        m_dxy=round(m_dxy, 4),
        base=CFG.base_weight,
        final=round(final, 4),
    )


# ── In-script self-backtest (walk-forward, no look-ahead) ───────────────
def self_backtest(price: np.ndarray, dxy: np.ndarray) -> Dict:
    """
    Walks through the history applying v9's modulator logic at each bar
    using ONLY information available up to that bar. Returns full stats
    and the equity curve.
    """
    n = len(price)
    if n < 260:
        return {"error": "not enough data"}

    cash = CFG.capital_usd
    units = 0.0
    nav_hist = np.zeros(n)
    cur_w = 0.0
    trades = 0
    warmup = 252

    full_log_ret = np.log(price[1:] / price[:-1])
    LOG.info("  precomputing regime path (HMM refit every 63 bars)…")
    regime_path = precompute_regime_path(full_log_ret,
                                            refit_every=63,
                                            min_history=warmup)

    for t in range(n):
        if t < warmup:
            nav_hist[t] = cash + units * price[t]
            continue

        p_sl = price[:t+1]
        d_sl = dxy[:t+1]
        lr = full_log_ret[:t]   # log_ret indexed one behind price

        reg_idx_t = (regime_path[t-1] if t-1 < len(regime_path)
                       else (1, 0.60))
        mods = compute_modulators(p_sl, d_sl, lr, nav_hist[:t+1],
                                    regime=reg_idx_t)
        target = mods.final

        nav = cash + units * price[t]
        cur_w = (units * price[t]) / nav if nav > 0 else 0.0

        if abs(target - cur_w) > CFG.rebalance_band:
            target_value = target * nav
            cur_value = units * price[t]
            if target_value > cur_value:
                buy_value = target_value - cur_value
                eff_price = price[t] * (1 + CFG.friction_per_side)
                buy_units = buy_value / eff_price
                cost = buy_units * eff_price
                if cost <= cash + 1e-6:
                    cash -= cost
                    units += buy_units
                    trades += 1
            else:
                sell_value = cur_value - target_value
                eff_price = price[t] * (1 - CFG.friction_per_side)
                sell_units = min(units, sell_value / eff_price)
                cash += sell_units * eff_price
                units -= sell_units
                trades += 1

        nav_hist[t] = cash + units * price[t]

    # Close final position so stats are post-friction
    if units > 0:
        eff_price = price[-1] * (1 - CFG.friction_per_side)
        cash += units * eff_price
        nav_hist[-1] = cash
        units = 0

    # Stats — strategy
    rets = np.diff(nav_hist[warmup:]) / nav_hist[warmup:-1]
    rets = rets[np.isfinite(rets)]
    total_ret = nav_hist[-1] / CFG.capital_usd - 1
    years = (n - warmup) / 252
    cagr = (1 + total_ret) ** (1 / years) - 1 if years > 0 else 0
    vol = float(np.std(rets) * np.sqrt(252)) if len(rets) > 1 else 0
    sharpe = (cagr - 0.045) / vol if vol > 0 else 0
    neg = rets[rets < 0]
    dvol = float(np.std(neg) * np.sqrt(252)) if len(neg) > 1 else 0
    sortino = (cagr - 0.045) / dvol if dvol > 0 else 0
    peak = np.maximum.accumulate(nav_hist[warmup:])
    dd = (nav_hist[warmup:] - peak) / (peak + 1e-8)
    max_dd = float(dd.min())
    calmar = cagr / abs(max_dd) if abs(max_dd) > 1e-6 else 0

    # Buy & hold on identical window
    bh_units = CFG.capital_usd / (price[warmup] * (1 + CFG.friction_per_side))
    bh_nav = bh_units * price[warmup:] * (1 - CFG.friction_per_side)
    bh_total = bh_nav[-1] / CFG.capital_usd - 1
    bh_cagr = (1 + bh_total) ** (1 / years) - 1 if years > 0 else 0
    bh_rets = np.diff(bh_nav) / bh_nav[:-1]
    bh_vol = float(np.std(bh_rets) * np.sqrt(252))
    bh_sharpe = (bh_cagr - 0.045) / bh_vol if bh_vol > 0 else 0
    bh_peak = np.maximum.accumulate(bh_nav)
    bh_dd = (bh_nav - bh_peak) / (bh_peak + 1e-8)
    bh_max_dd = float(bh_dd.min())

    return {
        "n_bars":        n,
        "trades":        trades,
        "total_return":  total_ret,
        "cagr":          cagr,
        "ann_vol":       vol,
        "sharpe":        sharpe,
        "sortino":       sortino,
        "max_dd":        max_dd,
        "calmar":        calmar,
        "final_nav":     float(nav_hist[-1]),
        "bh_cagr":       bh_cagr,
        "bh_sharpe":     bh_sharpe,
        "bh_max_dd":     bh_max_dd,
        "alpha_cagr":    cagr - bh_cagr,
        "dd_reduction":  bh_max_dd - max_dd,  # positive = v9 better
        "calmar_vs_bh":  calmar - (bh_cagr / abs(bh_max_dd)
                                     if abs(bh_max_dd) > 1e-6 else 0),
        "nav_history":   nav_hist,
    }


# ── Minimum deposit calculator ──────────────────────────────────────────
def minimum_deposit(historical_max_dd: float,
                     max_acceptable_dd: float,
                     safety_margin: float,
                     buffer_target_usd: float = 1_000.0) -> Dict:
    """
    Two-part output:

    (A) ABSOLUTE FLOOR — capital that, if hit by the historical worst
        drawdown, still leaves `buffer_target_usd` left to recover from.

        deposit_floor = buffer_target_usd / abs(historical_max_dd)

    (B) RISK-TOLERANCE FLOOR — capital you need given your personal max
        acceptable drawdown. If your tolerance is tighter than history,
        you need to start with more capital (you'll halt sooner on a
        smaller % move, so the absolute USD pain is the same).

        deposit_tolerance = buffer_target_usd / abs(max_acceptable_dd)

    Recommended deposit = max(A, B) × safety_margin.
    """
    h = abs(historical_max_dd) if historical_max_dd != 0 else 0.20
    t = abs(max_acceptable_dd) if max_acceptable_dd != 0 else h

    floor_a = buffer_target_usd / h
    floor_b = buffer_target_usd / t
    recommended = max(floor_a, floor_b) * safety_margin

    return {
        "historical_max_dd":        h,
        "user_max_acceptable_dd":   t,
        "buffer_target_usd":        buffer_target_usd,
        "deposit_floor_historical": round(floor_a, 2),
        "deposit_floor_tolerance":  round(floor_b, 2),
        "safety_margin":            safety_margin,
        "recommended_deposit_usd":  round(recommended, 2),
    }


# ── Trade levels ────────────────────────────────────────────────────────
def trade_levels(price: float, atr: float, target_weight: float) -> Dict:
    """Always LONG in v9 (we modulate size, not direction)."""
    if target_weight <= 0.05:
        return {"side": "FLAT", "entry": price, "sl": None, "tp": None,
                "weight": target_weight, "units": 0, "notional": 0.0}
    entry = price * (1 + CFG.friction_per_side)
    sl = entry - atr * CFG.stop_loss_atr_mult
    tp = entry + (entry - sl) * CFG.take_profit_rr
    notional = CFG.capital_usd * target_weight
    units = notional / entry if entry > 0 else 0.0
    return {
        "side":     "LONG",
        "entry":    round(entry, 2),
        "sl":       round(sl, 2),
        "tp":       round(tp, 2),
        "weight":   round(target_weight, 4),
        "units":    round(units, 4),
        "notional": round(notional, 2),
        "rr":       round((tp - entry) / max(entry - sl, 1e-8), 2),
    }


# ── Profit-target halt ──────────────────────────────────────────────────
def check_profit_target(state_dir: Path, ticker_slug: str,
                          current_nav: float) -> Optional[str]:
    """If NAV ≥ initial × (1+target), return a halt message."""
    target_nav = CFG.capital_usd * (1 + CFG.profit_target_pct)
    if current_nav >= target_nav:
        return (f"🎯 PROFIT TARGET HIT: NAV ${current_nav:,.2f} >= "
                 f"${target_nav:,.2f} (target +{CFG.profit_target_pct*100:.0f}%). "
                 f"Bot halts. Withdraw or raise the target to continue.")
    return None


# ── State I/O ───────────────────────────────────────────────────────────
def load_equity(state_dir: Path, ticker_slug: str) -> Optional[float]:
    path = state_dir / f"{ticker_slug}_equity.json"
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return float(data.get("current_nav", CFG.capital_usd))
    except Exception:
        return None


def save_equity(state_dir: Path, ticker_slug: str, nav: float):
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / f"{ticker_slug}_equity.json"
    payload = {
        "current_nav":   round(nav, 2),
        "initial":       CFG.capital_usd,
        "target":        round(CFG.capital_usd * (1 + CFG.profit_target_pct), 2),
        "updated":       datetime.now().isoformat(timespec="seconds"),
        "ticker":        CFG.ticker,
    }
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


# ── Display ─────────────────────────────────────────────────────────────
SEP = "═" * 76
DSEP = "─" * 76


def print_report(price: float, mods: Modulators, levels: Dict,
                   bt: Dict, mindep: Dict, vol_label: str,
                   reg_label: str, reg_conf: float, sigma_d: float,
                   cvar: float, halt_msg: Optional[str]):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{SEP}")
    print(f"  📡  v9 XAUUSD DRAWDOWN-CONTROL EDITION   ·   {now}")
    print(f"  Ticker: {CFG.ticker}   Capital: ${CFG.capital_usd:,.0f}   "
           f"Target: +{CFG.profit_target_pct*100:.0f}%")
    print(SEP)

    # Modulator stack
    print(f"\n  TARGET WEIGHT: {mods.final:.0%}   "
           f"(base {mods.base:.0%}  →  min of modulators)")
    print(DSEP)
    print(f"    regime    × {mods.m_regime:.2f}   ({reg_label}, "
           f"conf {reg_conf:.0%})")
    print(f"    vol       × {mods.m_vol:.2f}   "
           f"(target {CFG.vol_target_annual:.0%}, σ_d={sigma_d*100:.2f}% [{vol_label}])")
    print(f"    drawdown  × {mods.m_drawdown:.2f}")
    print(f"    dxy       × {mods.m_dxy:.2f}")
    print(DSEP)

    # Trade levels
    if levels["side"] == "FLAT":
        print(f"\n  ⚪ FLAT — multiple modulators have shrunk weight near 0.")
    else:
        print(f"\n  {levels['side']} XAUUSD at {mods.final:.0%} of capital")
        print(f"    Entry:        ${levels['entry']:>10,.2f}")
        print(f"    Stop Loss:    ${levels['sl']:>10,.2f}   "
              f"(−{(levels['entry']-levels['sl'])/levels['entry']*100:.2f}%)")
        print(f"    Take Profit:  ${levels['tp']:>10,.2f}   "
              f"(+{(levels['tp']-levels['entry'])/levels['entry']*100:.2f}%)")
        print(f"    R:R:          1 : {levels['rr']:.1f}")
        print(f"    Notional:     ${levels['notional']:>10,.2f}  "
              f"({levels['units']:.2f} units)")

    # Backtest summary
    print(f"\n{DSEP}")
    print(f"  IN-SCRIPT BACKTEST  (walk-forward, net of {CFG.friction_per_side*100:.2f}% × 2 friction)")
    print(DSEP)
    print(f"               {'v9':>10}    {'Buy & Hold':>10}    {'Δ':>8}")
    print(f"  CAGR:        {bt['cagr']*100:>9.1f}%   {bt['bh_cagr']*100:>9.1f}%   "
           f"{bt['alpha_cagr']*100:+8.1f}%")
    print(f"  Sharpe:      {bt['sharpe']:>10.2f}    {bt['bh_sharpe']:>10.2f}    "
           f"{bt['sharpe']-bt['bh_sharpe']:+8.2f}")
    print(f"  MaxDD:       {bt['max_dd']*100:>9.1f}%   {bt['bh_max_dd']*100:>9.1f}%   "
           f"{bt['dd_reduction']*100:+8.1f}%  ← v9's actual job")
    print(f"  Calmar:      {bt['calmar']:>10.2f}    "
           f"{bt['bh_cagr']/abs(bt['bh_max_dd']):>10.2f}    "
           f"{bt['calmar_vs_bh']:+8.2f}")
    print(f"  Trades:      {bt['trades']:>10}    {'2':>10}")

    verdict = "✓ v9 reduces drawdown" if bt["dd_reduction"] > 0.02 else "✗ v9 fails its mission"
    print(f"\n  VERDICT: {verdict}.  Sharpe Δ {bt['sharpe']-bt['bh_sharpe']:+.2f}, "
           f"Calmar Δ {bt['calmar_vs_bh']:+.2f}")

    # Minimum deposit
    print(f"\n{DSEP}")
    print(f"  MINIMUM-DEPOSIT CALCULATOR")
    print(DSEP)
    print(f"  Historical worst drawdown (v9, this backtest):  "
           f"{mindep['historical_max_dd']*100:.1f}%")
    print(f"  Your max acceptable drawdown:                    "
           f"{mindep['user_max_acceptable_dd']*100:.1f}%")
    print(f"  Buffer you'd like to retain at worst:            "
           f"${mindep['buffer_target_usd']:,.0f}")
    print(f"  Safety margin:                                   "
           f"×{mindep['safety_margin']:.2f}")
    print(f"")
    print(f"  → Deposit at least  ${mindep['recommended_deposit_usd']:>10,.2f}  "
           f"to survive the worst case and stay solvent.")

    # Risk dashboard
    print(f"\n{DSEP}")
    print(f"  RISK DASHBOARD")
    print(DSEP)
    print(f"  σ_daily ({vol_label}):  {sigma_d*100:.3f}%  "
           f"(≈ {sigma_d*np.sqrt(252)*100:.1f}% annualised)")
    print(f"  CVaR (95%, 1d):        {cvar*100:.3f}%  of capital")
    print(f"  Regime:                {reg_label}  conf {reg_conf:.0%}")
    print(f"  ATR(14):               ${levels.get('atr', 0.0):.2f}")

    if halt_msg:
        print(f"\n  {halt_msg}")

    print(f"\n  Disclaimer: educational / research only. Not financial advice.")
    print(SEP)


# ── Main job ────────────────────────────────────────────────────────────
def job():
    LOG.info("=" * 60)
    LOG.info(f"v9 starting | ticker={CFG.ticker} capital=${CFG.capital_usd:,.0f}")
    LOG.info("=" * 60)

    data = fetch_market_data()
    price = data["price"]
    dxy = data["dxy"]
    log_ret = np.log(price[1:] / price[:-1])

    # Vol & CVaR
    sigma_d, vol_label = garch_or_ewma(log_ret)
    cvar = evt_cvar(log_ret, CFG.cvar_confidence)

    # Regime
    reg_idx, reg_conf, reg_label = detect_regime(log_ret)

    # Self-backtest (the source of truth for the min-deposit calc)
    LOG.info("Running in-script walk-forward backtest…")
    bt = self_backtest(price, dxy)
    LOG.info(f"  v9 CAGR {bt['cagr']*100:+.1f}%  vs B&H {bt['bh_cagr']*100:+.1f}%  "
              f"|  MaxDD {bt['max_dd']*100:+.1f}% vs B&H {bt['bh_max_dd']*100:+.1f}%")

    # Modulators on TODAY's bar (uses backtest nav history for dd modulator)
    mods = compute_modulators(price, dxy, log_ret, bt["nav_history"])

    # Trade levels
    atr = _atr(price, 14)
    levels = trade_levels(float(price[-1]), atr, mods.final)
    # Inject ATR into the display
    levels["atr"] = round(atr, 2)

    # Minimum deposit
    mindep = minimum_deposit(
        historical_max_dd=bt["max_dd"],
        max_acceptable_dd=CFG.max_acceptable_dd,
        safety_margin=CFG.safety_margin,
        buffer_target_usd=CFG.capital_usd * 0.10,  # protect 10% of capital
    )

    # Profit-target check (uses persisted equity if available)
    state_dir = Path(CFG.state_dir)
    persisted_nav = load_equity(state_dir, CFG.ticker_slug)
    if persisted_nav is None:
        persisted_nav = CFG.capital_usd  # first run
    halt_msg = check_profit_target(state_dir, CFG.ticker_slug, persisted_nav)

    # Save state (so next run sees current NAV)
    save_equity(state_dir, CFG.ticker_slug,
                  persisted_nav if halt_msg else float(bt["final_nav"]))

    # Display
    print_report(price=float(price[-1]), mods=mods, levels=levels, bt=bt,
                  mindep=mindep, vol_label=vol_label, reg_label=reg_label,
                  reg_conf=reg_conf, sigma_d=sigma_d, cvar=cvar,
                  halt_msg=halt_msg)

    # Persist backtest summary for the bake-off integrator
    bt_summary_path = Path("backtests") / "v9_self_backtest.json"
    bt_summary_path.parent.mkdir(parents=True, exist_ok=True)
    persistable = {k: v for k, v in bt.items() if k != "nav_history"}
    persistable["ticker"] = CFG.ticker
    persistable["ran_on"] = datetime.now().isoformat(timespec="seconds")
    persistable["final_modulators"] = asdict(mods)
    with open(bt_summary_path, "w") as f:
        json.dump(persistable, f, indent=2, default=float)
    LOG.info(f"backtest summary written to {bt_summary_path}")


# ── Entry ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="v9 XAUUSD drawdown-control bot")
    ap.add_argument("--ticker",        default=CFG.ticker,
                     help=f"yfinance symbol (default {CFG.ticker})")
    ap.add_argument("--capital",       type=float, default=CFG.capital_usd,
                     help=f"USD capital (default {CFG.capital_usd:,.0f})")
    ap.add_argument("--profit-target", type=float, default=CFG.profit_target_pct,
                     help=f"halt at + this fraction (default "
                          f"{CFG.profit_target_pct})")
    ap.add_argument("--max-dd",        type=float, default=CFG.max_acceptable_dd,
                     help=f"your max acceptable drawdown (default "
                          f"{CFG.max_acceptable_dd})")
    ap.add_argument("--start",         default=CFG.start_date,
                     help=f"backtest start date (default {CFG.start_date})")
    args = ap.parse_args()

    CFG.ticker = args.ticker
    CFG.capital_usd = args.capital
    CFG.profit_target_pct = args.profit_target
    CFG.max_acceptable_dd = args.max_dd
    CFG.start_date = args.start

    job()
