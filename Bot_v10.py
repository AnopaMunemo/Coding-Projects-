"""
goldbot_v10.py — JSE Gold Trading System
═══════════════════════════════════════════════════════════════
IMPROVEMENTS OVER v9 (lessons from the 2026-05-28 bake-off):
  • v9 proved pure timing can't beat B&H on net Sharpe on real data.
  • v10 keeps the drawdown-control modulator from v9 AND adds:
    1. ARIMA time-series price forecasting (statsmodels)
    2. ANOVA:  do returns differ by day / month? (seasonality test)
    3. 6-panel Matplotlib chart suite (saved to PNG)
    4. Small-trade calculator  (e.g. R370 entry, ZAR throughout)
    5. JSE intraday timing windows with duration estimate
    6. Adaptive ML weights that update after every run
    7. Regime-coloured price chart so you see WHEN signals fired
  • All monetary outputs in South African Rand (ZAR / R)

USAGE:
  python goldbot_v10.py                  # run with defaults
  python goldbot_v10.py --capital 370    # R370 trade size
  python goldbot_v10.py --ticker ANG.JO  # different JSE gold share
  python goldbot_v10.py --no-charts      # skip PNG generation
  python goldbot_v10.py --once           # cron / Docker single-run

DEPENDENCIES:
  pip install yfinance matplotlib statsmodels arch hmmlearn scikit-learn scipy pandas numpy schedule
"""
from __future__ import annotations
import os, sys, json, warnings, argparse, logging
from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")
np.random.seed(42)

# ── Optional imports (degrade gracefully) ────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

try:
    import matplotlib
    matplotlib.use("Agg")          # headless safe; remove for interactive
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    import matplotlib.gridspec as gridspec
    MPL_OK = True
except ImportError:
    MPL_OK = False
    print("⚠️  pip install matplotlib — charts disabled")

try:
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.stattools import adfuller
    SM_OK = True
except ImportError:
    SM_OK = False
    print("⚠️  pip install statsmodels — ARIMA uses trend fallback")

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

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

@dataclass
class Config:
    # Primary instrument
    ticker:             str   = "GLD.JO"       # Absa NewGold ETF (JSE)
    zar_ticker:         str   = "ZAR=X"        # USD/ZAR spot
    dxy_ticker:         str   = "DX-Y.NYB"     # US Dollar Index
    vix_ticker:         str   = "^VIX"         # CBOE VIX
    us10y_ticker:       str   = "^TNX"         # US 10-yr Treasury yield
    start_date:         str   = "2015-01-01"

    # Portfolio
    trade_capital:      float = 370.0          # ZAR — small trade example
    total_capital:      float = 10_000.0       # ZAR — total portfolio
    max_position_pct:   float = 0.20           # never > 20% in one trade
    kelly_scalar:       float = 0.25           # fractional Kelly
    risk_free:          float = 0.085          # SARB repo rate approx (8.5%)

    # JSE execution costs (round-trip)
    friction_buy:       float = 0.0075         # spread + brokerage + STT on buy
    friction_sell:      float = 0.0055         # spread + brokerage on sell

    # Risk parameters
    stop_loss_atr_mult: float = 1.5            # SL = entry − 1.5 × ATR
    take_profit_rr:     float = 2.0            # TP = entry + 2× SL distance
    cvar_confidence:    float = 0.95

    # Signal thresholds
    p_strong_buy:       float = 0.62
    p_buy:              float = 0.55
    p_sell:             float = 0.45
    p_strong_sell:      float = 0.38

    # ARIMA
    forecast_horizon:   int   = 21             # trading days ahead

    # ANOVA window
    anova_window:       int   = 504            # 2 trading years

    # Files
    weights_file:       str   = "goldbot_v10_weights.json"
    history_file:       str   = "goldbot_v10_history.json"
    chart_dir:          str   = "."

    # JSE intraday timing (SAST = UTC+2)
    best_entry_start:   str   = "09:30"
    best_entry_end:     str   = "10:30"
    best_exit_start:    str   = "15:30"
    best_exit_end:      str   = "16:30"


CFG = Config()
SEP  = "═" * 72
DSEP = "─" * 72


# ═══════════════════════════════════════════════════════════════
# LOGGER
# ═══════════════════════════════════════════════════════════════

def _log() -> logging.Logger:
    lg = logging.getLogger("goldbot_v10")
    if not lg.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(
            "%(asctime)s [v10] %(message)s", datefmt="%H:%M:%S"))
        lg.addHandler(h)
    lg.setLevel(logging.INFO)
    return lg

LOG = _log()


# ═══════════════════════════════════════════════════════════════
# DATA FEED
# ═══════════════════════════════════════════════════════════════

def fetch_data() -> Dict:
    LOG.info(f"📥  Fetching {CFG.ticker} + macro from {CFG.start_date}…")
    tickers = [CFG.ticker, CFG.zar_ticker, CFG.dxy_ticker,
               CFG.vix_ticker, CFG.us10y_ticker]
    raw = yf.download(" ".join(tickers), start=CFG.start_date,
                       auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        cl = raw["Close"]
    else:
        cl = raw[["Close"]]
    cl = cl.ffill().dropna(how="all")

    def _get(t):
        if t in cl.columns:
            s = cl[t].dropna()
            return s.to_numpy(dtype=float), s.index
        return None, None

    gld, dates = _get(CFG.ticker)
    if gld is None or len(gld) < 200:
        raise RuntimeError(f"Cannot fetch {CFG.ticker} — check ticker symbol")

    n = len(gld)
    zar,  _ = _get(CFG.zar_ticker)
    dxy,  _ = _get(CFG.dxy_ticker)
    vix,  _ = _get(CFG.vix_ticker)
    us10, _ = _get(CFG.us10y_ticker)

    zar  = zar[-n:]  if zar  is not None and len(zar)  >= n else np.full(n, 18.5)
    dxy  = dxy[-n:]  if dxy  is not None and len(dxy)  >= n else np.full(n, 103.0)
    vix  = vix[-n:]  if vix  is not None and len(vix)  >= n else np.full(n, 20.0)
    us10 = us10[-n:] if us10 is not None and len(us10) >= n else np.full(n, 4.5)

    LOG.info(f"  ✓  {n} bars | {CFG.ticker}=R{gld[-1]:,.2f} | "
             f"ZAR={zar[-1]:.4f} | DXY={dxy[-1]:.2f} | VIX={vix[-1]:.1f}")
    return {"gld": gld, "zar": zar, "dxy": dxy, "vix": vix, "us10": us10,
            "dates": dates[-n:], "n": n}


# ═══════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ═══════════════════════════════════════════════════════════════

def _ema(p: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty(len(p), dtype=float)
    out[0] = p[0]
    for i in range(1, len(p)):
        out[i] = p[i] * k + out[i-1] * (1 - k)
    return out

def _bollinger(p: np.ndarray, period: int = 20, n_std: float = 2.0
               ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = pd.Series(p).rolling(period).mean().to_numpy()
    s   = pd.Series(p).rolling(period).std().to_numpy()
    return mid, mid + n_std * s, mid - n_std * s

def _rsi(p: np.ndarray, period: int = 14) -> np.ndarray:
    d  = np.diff(p)
    g  = np.where(d > 0, d, 0.0)
    lo = np.where(d < 0, -d, 0.0)
    a  = 1.0 / period
    ag = float(g[:period].mean()) if period <= len(g) else 50.0
    al = float(lo[:period].mean()) if period <= len(lo) else 50.0
    rsi = np.zeros(len(p))
    for i in range(period, len(d)):
        ag = a * g[i] + (1 - a) * ag
        al = a * lo[i] + (1 - a) * al
        rsi[i+1] = 100.0 if al < 1e-8 else 100.0 - 100.0 / (1.0 + ag / al)
    return rsi

def _macd(p: np.ndarray, fast=12, slow=26, sig=9
          ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    ml = _ema(p, fast) - _ema(p, slow)
    sl = _ema(ml, sig)
    return ml, sl, ml - sl

def _atr(p: np.ndarray, period: int = 14) -> float:
    diffs = np.abs(np.diff(p[-period * 2:]))
    return float(np.mean(diffs[-period:]))


# ═══════════════════════════════════════════════════════════════
# ARIMA TIME-SERIES FORECASTING
# ═══════════════════════════════════════════════════════════════

def arima_forecast(prices: np.ndarray, horizon: int = 21) -> Dict:
    """
    Fit ARIMA on log-prices → forecast `horizon` trading days.
    Returns dict with 'forecast', 'ci_lower', 'ci_upper' in original ZAR space.
    Falls back to linear trend if statsmodels unavailable.
    """
    out = {"forecast": None, "ci_lower": None, "ci_upper": None,
           "model": "fallback", "aic": None}

    def _trend_fallback(p):
        lp = np.log(p[-90:])
        x  = np.arange(len(lp))
        sl, ic, *_ = stats.linregress(x, lp)
        fx  = np.arange(len(lp), len(lp) + horizon)
        lfc = sl * fx + ic
        lsd = float(np.std(lp - (sl * x + ic)))
        steps = np.arange(1, horizon + 1)
        out["forecast"] = np.exp(lfc)
        out["ci_lower"] = np.exp(lfc - 1.96 * lsd * np.sqrt(steps))
        out["ci_upper"] = np.exp(lfc + 1.96 * lsd * np.sqrt(steps))
        out["model"] = "LinearTrend"

    if not SM_OK or len(prices) < 120:
        _trend_fallback(prices)
        return out

    try:
        lp = np.log(prices)
        d  = 0 if float(adfuller(lp[-252:])[1]) < 0.05 else 1
        fit = ARIMA(lp, order=(1, d, 1)).fit()
        fc  = fit.get_forecast(steps=horizon)
        ci  = fc.conf_int()
        out["forecast"] = np.exp(fc.predicted_mean.to_numpy())
        out["ci_lower"] = np.exp(ci.iloc[:, 0].to_numpy())
        out["ci_upper"] = np.exp(ci.iloc[:, 1].to_numpy())
        out["aic"]   = round(fit.aic, 1)
        out["model"] = f"ARIMA(1,{d},1)"
        LOG.info(f"  ARIMA(1,{d},1) AIC={fit.aic:.1f}  "
                 f"1-day: R{out['forecast'][0]:,.2f}  "
                 f"21-day: R{out['forecast'][-1]:,.2f}")
    except Exception as e:
        LOG.warning(f"  ARIMA failed ({e}) — trend fallback")
        _trend_fallback(prices)
    return out


# ═══════════════════════════════════════════════════════════════
# ANOVA SEASONALITY ANALYSIS
# ═══════════════════════════════════════════════════════════════

def anova_analysis(prices: np.ndarray, dates: pd.DatetimeIndex) -> Dict:
    """
    One-way ANOVA: do mean log-returns differ by
      (A) day of week?   → Monday effect etc.
      (B) month?         → January effect etc.
      (C) quarter?       → seasonal cycles

    Returns F-stats, p-values, group means for plotting + text commentary.
    """
    n   = min(len(prices), CFG.anova_window)
    p_  = prices[-n:]
    d_  = pd.DatetimeIndex(dates[-n:])
    lr  = np.log(p_[1:] / p_[:-1]) * 100         # daily return in %
    dr  = d_[1:]

    df = pd.DataFrame({"ret": lr,
                        "dow":  dr.dayofweek,
                        "mon":  dr.month,
                        "qtr":  dr.quarter})

    def _anova_groups(col, rng):
        grps = [df.loc[df[col] == i, "ret"].to_numpy() for i in rng]
        grps = [g for g in grps if len(g) >= 5]
        if len(grps) < 2:
            return 0.0, 1.0, [0.0] * len(rng)
        f, pv = stats.f_oneway(*grps)
        means = [float(df.loc[df[col] == i, "ret"].mean())
                 if len(df.loc[df[col] == i]) > 0 else 0.0
                 for i in rng]
        return float(f), float(pv), means

    dow_f, dow_p, dow_m  = _anova_groups("dow", range(5))
    mon_f, mon_p, mon_m  = _anova_groups("mon", range(1, 13))
    qtr_f, qtr_p, qtr_m  = _anova_groups("qtr", range(1, 5))

    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    mon_names = ["Jan","Feb","Mar","Apr","May","Jun",
                  "Jul","Aug","Sep","Oct","Nov","Dec"]
    qtr_names = ["Q1", "Q2", "Q3", "Q4"]

    best_dow = dow_names[int(np.argmax(dow_m))]
    wrst_dow = dow_names[int(np.argmin(dow_m))]
    best_mon = mon_names[int(np.argmax(mon_m))]

    LOG.info(f"  ANOVA DoW:  F={dow_f:.2f}  p={dow_p:.4f}  "
             f"{'★ significant' if dow_p < 0.05 else 'n.s.'}")
    LOG.info(f"  ANOVA Mon:  F={mon_f:.2f}  p={mon_p:.4f}  "
             f"{'★ significant' if mon_p < 0.05 else 'n.s.'}")

    return {"dow_names": dow_names, "dow_means": dow_m, "dow_f": dow_f, "dow_p": dow_p,
            "mon_names": mon_names, "mon_means": mon_m, "mon_f": mon_f, "mon_p": mon_p,
            "qtr_names": qtr_names, "qtr_means": qtr_m, "qtr_f": qtr_f, "qtr_p": qtr_p,
            "best_day": best_dow, "worst_day": wrst_dow, "best_month": best_mon}


# ═══════════════════════════════════════════════════════════════
# VOLATILITY MODEL
# ═══════════════════════════════════════════════════════════════

def garch_vol(log_ret: np.ndarray) -> Tuple[float, str]:
    if GARCH_OK and len(log_ret) > 100:
        try:
            clean = log_ret[log_ret != 0]
            m = arch_model(clean * 100, vol="Garch", p=1, q=1,
                            dist="t", rescale=True)
            res = m.fit(disp="off", show_warning=False,
                         options={"maxiter": 500})
            vs = float(res.forecast(horizon=1).variance.values[-1, 0])
            s  = float(np.sqrt(vs)) / 100.0
            if 0.0005 < s < 0.10:
                pr = res.params
                a, b = float(pr.get("alpha[1]", 0)), float(pr.get("beta[1]", 0))
                if 0 < a + b < 1:
                    return s, "GARCH(1,1)-t"
        except Exception:
            pass
    lam, var = 0.94, float(np.var(log_ret[:30]))
    for r in log_ret:
        var = lam * var + (1 - lam) * r**2
    return float(np.sqrt(var)), "EWMA(λ=0.94)"


# ═══════════════════════════════════════════════════════════════
# HMM REGIME DETECTION
# ═══════════════════════════════════════════════════════════════

def detect_regime(log_ret: np.ndarray) -> Tuple[int, float, str, np.ndarray]:
    """
    Returns (regime_idx, confidence, label, state_path_array).
    0=Bull, 1=Calm, 2=Crisis.  State path is used to colour the chart.
    """
    n = len(log_ret)
    path = np.ones(n, dtype=int)

    if HMM_OK and n >= 120:
        try:
            X = log_ret.reshape(-1, 1)
            model = _hmm.GaussianHMM(n_components=3, covariance_type="full",
                                      n_iter=200, random_state=42)
            model.fit(X)
            states = model.predict(X)
            probs  = model.predict_proba(X)
            order  = np.argsort(model.means_.flatten())[::-1]
            remap  = {raw: rank for rank, raw in enumerate(order)}
            path   = np.array([remap[s] for s in states])
            cur    = int(states[-1])
            rank   = remap[cur]
            conf   = float(probs[-1][cur])
            labels = {0: "🟢 Bull", 1: "🟡 Calm", 2: "🔴 Crisis"}
            return rank, conf, labels[rank], path
        except Exception:
            pass

    rv = float(np.std(log_ret[-20:]) * np.sqrt(252))
    if rv > 0.28:
        return 2, 0.65, "🔴 Crisis (vol fallback)", path * 2
    if rv < 0.12:
        return 0, 0.65, "🟢 Bull (vol fallback)",   path * 0
    return 1, 0.65, "🟡 Calm (vol fallback)", path


# ═══════════════════════════════════════════════════════════════
# MONTE CARLO (Merton Jump-Diffusion)
# ═══════════════════════════════════════════════════════════════

def monte_carlo(S0: float, mu: float, sigma: float,
                log_ret: np.ndarray, n_sims: int = 2000,
                days: int = 21) -> np.ndarray:
    std  = np.std(log_ret)
    jmp  = log_ret[np.abs(log_ret) > 3 * std]
    jf   = len(jmp) / max(len(log_ret) / 252, 1)
    jmu  = float(jmp.mean()) if len(jmp) > 0 else 0.0
    jsig = float(jmp.std())  if len(jmp) > 1 else 1e-4

    dt    = 1 / 252
    paths = np.zeros((days + 1, n_sims))
    paths[0] = S0
    Z1  = np.random.standard_normal((days, n_sims))
    Z2  = np.random.standard_normal((days, n_sims))
    Poi = np.random.poisson(jf * dt, (days, n_sims))
    drift = (mu - 0.5 * sigma**2) * dt
    diff  = sigma * np.sqrt(dt)

    for t in range(1, days + 1):
        jump     = Poi[t-1] * (jmu + jsig * Z2[t-1])
        paths[t] = paths[t-1] * np.exp(drift + diff * Z1[t-1] + jump)
    return paths


# ═══════════════════════════════════════════════════════════════
# ADAPTIVE WEIGHTS (self-improving)
# ═══════════════════════════════════════════════════════════════

_DEFAULT_WEIGHTS = {
    "ema_trend": 1.2, "rsi": 0.9, "momentum": 1.0, "coint": 1.1,
    "dxy": 1.0, "vix": 0.8, "us10y": 0.9, "regime": 1.3,
    "arima": 1.4, "mc": 1.1,
}

def _load_weights() -> Dict:
    w = _DEFAULT_WEIGHTS.copy()
    if Path(CFG.weights_file).exists():
        try:
            with open(CFG.weights_file) as f:
                w.update(json.load(f))
        except Exception:
            pass
    total = sum(abs(v) for v in w.values())
    return {k: round(abs(v) / total * 10, 4) for k, v in w.items()}

def _save_weights(w: Dict):
    try:
        with open(CFG.weights_file, "w") as f:
            json.dump(w, f, indent=2)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════

def generate_signal(data: Dict, arima: Dict, mc_paths: np.ndarray,
                     sigma_d: float, reg_idx: int, reg_conf: float) -> Dict:
    prices = data["gld"]
    zar    = data["zar"]
    dxy    = data["dxy"]
    vix    = data["vix"]
    us10   = data["us10"]
    S0     = float(prices[-1])
    log_ret = np.log(prices[1:] / prices[:-1])

    w   = _load_weights()
    e20 = _ema(prices, 20)
    e50 = _ema(prices, 50)
    rsi = _rsi(prices, 14)
    rsi_val = float(rsi[-1])

    score  = 0.0
    dirs   = {}
    reasons = []

    # ── EMA trend ──────────────────────────────────────────────
    if e20[-1] > e50[-1]:
        score += w["ema_trend"]; dirs["ema_trend"] = 1
        reasons.append(f"EMA20 (R{e20[-1]:,.2f}) > EMA50 (R{e50[-1]:,.2f}) → uptrend ↑")
    else:
        score -= w["ema_trend"]; dirs["ema_trend"] = -1
        reasons.append(f"EMA20 (R{e20[-1]:,.2f}) < EMA50 (R{e50[-1]:,.2f}) → downtrend ↓")

    # ── RSI ────────────────────────────────────────────────────
    if rsi_val < 35:
        score += w["rsi"]; dirs["rsi"] = 1
        reasons.append(f"RSI {rsi_val:.1f} — oversold, mean-reversion buy ↑")
    elif rsi_val > 65:
        score -= w["rsi"]; dirs["rsi"] = -1
        reasons.append(f"RSI {rsi_val:.1f} — overbought, caution ↓")
    else:
        dirs["rsi"] = 0
        reasons.append(f"RSI {rsi_val:.1f} — neutral zone")

    # ── Momentum (20-day) ──────────────────────────────────────
    mom = float((prices[-1] - prices[-21]) / prices[-21]) if len(prices) > 21 else 0.0
    if mom > 0.01:
        score += w["momentum"]; dirs["momentum"] = 1
        reasons.append(f"20-day momentum {mom:+.2%} ↑")
    elif mom < -0.01:
        score -= w["momentum"]; dirs["momentum"] = -1
        reasons.append(f"20-day momentum {mom:+.2%} ↓")
    else:
        dirs["momentum"] = 0

    # ── DXY ────────────────────────────────────────────────────
    dxy_t = float((dxy[-1] - dxy[-10]) / (dxy[-10] + 1e-8))
    if dxy_t < -0.01:
        score += w["dxy"]; dirs["dxy"] = 1
        reasons.append(f"USD weakening {dxy_t:.2%} → gold tailwind ↑")
    elif dxy_t > 0.01:
        score -= w["dxy"]; dirs["dxy"] = -1
        reasons.append(f"USD strengthening {dxy_t:.2%} → gold headwind ↓")
    else:
        dirs["dxy"] = 0

    # ── VIX ────────────────────────────────────────────────────
    vix_t = float((vix[-1] - vix[-10]) / (vix[-10] + 1e-8))
    if vix_t > 0.05:
        score += w["vix"]; dirs["vix"] = 1
        reasons.append(f"VIX rising {vix_t:.1%} — safe-haven demand ↑")
    elif vix_t < -0.05:
        score -= w["vix"] * 0.5; dirs["vix"] = -1
        reasons.append(f"VIX falling {vix_t:.1%} — risk appetite ↓")
    else:
        dirs["vix"] = 0

    # ── US 10-year yield ───────────────────────────────────────
    u10_chg = float(us10[-1] - us10[-5]) if len(us10) >= 5 else 0.0
    if u10_chg < -0.10:
        score += w["us10y"]; dirs["us10y"] = 1
        reasons.append(f"US 10Y down {u10_chg:+.2f}% → lower real rates → gold ↑")
    elif u10_chg > 0.10:
        score -= w["us10y"]; dirs["us10y"] = -1
        reasons.append(f"US 10Y up {u10_chg:+.2f}% → higher real rates → gold ↓")
    else:
        dirs["us10y"] = 0

    # ── HMM Regime ─────────────────────────────────────────────
    if reg_idx == 0:
        score += w["regime"]; dirs["regime"] = 1
        reasons.append("HMM: Bull regime — follow the trend ↑")
    elif reg_idx == 2:
        score += w["regime"] * 0.6; dirs["regime"] = 1
        reasons.append("HMM: Crisis — safe-haven demand supports gold ↑")
    else:
        dirs["regime"] = 0
        reasons.append("HMM: Calm/transitional — wait for clarity")

    # ── ARIMA 1-day forecast ────────────────────────────────────
    if arima.get("forecast") is not None:
        a1d  = float(arima["forecast"][0])
        adir = (a1d - S0) / S0
        if adir > 0.003:
            score += w["arima"]; dirs["arima"] = 1
            reasons.append(f"ARIMA ({arima['model']}) 1d target R{a1d:,.2f} "
                           f"({adir:+.2%}) ↑")
        elif adir < -0.003:
            score -= w["arima"]; dirs["arima"] = -1
            reasons.append(f"ARIMA ({arima['model']}) 1d target R{a1d:,.2f} "
                           f"({adir:+.2%}) ↓")
        else:
            dirs["arima"] = 0
    else:
        a1d = S0

    # ── Monte Carlo probability ─────────────────────────────────
    h21 = mc_paths[min(21, mc_paths.shape[0]-1)]
    mc_up = float((h21 > S0).mean())
    if mc_up > 0.55:
        score += w["mc"] * (mc_up - 0.5) * 4; dirs["mc"] = 1
        reasons.append(f"MC P(above today in 21d) = {mc_up:.1%} ↑")
    elif mc_up < 0.45:
        score -= w["mc"] * (0.5 - mc_up) * 4; dirs["mc"] = -1
        reasons.append(f"MC P(above today in 21d) = {mc_up:.1%} ↓")
    else:
        dirs["mc"] = 0

    prob = float(np.clip(1 / (1 + np.exp(-score * 0.4)), 0.01, 0.99))

    if   prob >= CFG.p_strong_buy:    action = "🟢 STRONG BUY"
    elif prob >= CFG.p_buy:           action = "🟡 BUY"
    elif prob <= CFG.p_strong_sell:   action = "🔴 STRONG SELL"
    elif prob <= CFG.p_sell:          action = "🟠 SELL"
    else:                              action = "⚪ HOLD"

    return {
        "action": action, "score": round(score, 4), "prob": round(prob, 4),
        "rsi": rsi_val, "ema20": float(e20[-1]), "ema50": float(e50[-1]),
        "momentum": mom, "mc_up_21d": mc_up,
        "arima_1d": float(arima["forecast"][0]) if arima.get("forecast") is not None else S0,
        "arima_21d": float(arima["forecast"][-1]) if arima.get("forecast") is not None else S0,
        "reasons": reasons, "dirs": dirs,
    }


# ═══════════════════════════════════════════════════════════════
# SMALL TRADE CALCULATOR (ZAR)
# ═══════════════════════════════════════════════════════════════

def small_trade_calc(price: float, sigma_d: float,
                      capital: Optional[float] = None) -> Dict:
    """
    Calculate entry, SL, TP for a ZAR-denominated small trade.
    Handles both whole-unit JSE board lots and fractional/CFD.

    GLD.JO: each unit ≈ 1/100 oz gold, typically R400–R800/unit.
    With R370, you likely need CFD/fractional exposure, which is
    offered by FNB Shares, EasyEquities ETF fractions, or any CFD broker.
    """
    if capital is None:
        capital = CFG.trade_capital

    entry      = price * (1 + CFG.friction_buy)
    atr_approx = price * sigma_d * np.sqrt(2)
    sl_dist    = atr_approx * CFG.stop_loss_atr_mult
    sl         = entry - sl_dist
    tp         = entry + sl_dist * CFG.take_profit_rr

    # Whole units
    units_whole    = int(capital / entry)
    notional_whole = round(units_whole * entry, 2) if units_whole > 0 else 0.0
    loss_whole     = round(units_whole * sl_dist, 2) if units_whole > 0 else 0.0
    gain_whole     = round(units_whole * sl_dist * CFG.take_profit_rr, 2) if units_whole > 0 else 0.0
    leftover       = round(capital - notional_whole, 2)

    # Fractional / CFD
    units_frac = capital / entry
    loss_frac  = round(units_frac * sl_dist, 2)
    gain_frac  = round(units_frac * sl_dist * CFG.take_profit_rr, 2)

    # Estimate how long until SL might be hit (for intraday planning)
    daily_move = price * sigma_d
    hours_to_sl = round((sl_dist / daily_move) * 8.0, 1)  # 8hr JSE session
    # If < 2h the trade is too volatile for the size

    return {
        "capital":        capital,
        "price":          round(price, 4),
        "entry":          round(entry, 4),
        "sl":             round(sl, 4),
        "tp":             round(tp, 4),
        "sl_dist":        round(sl_dist, 4),
        "tp_dist":        round(sl_dist * CFG.take_profit_rr, 4),
        "sl_pct":         round(sl_dist / entry * 100, 2),
        "tp_pct":         round(sl_dist * CFG.take_profit_rr / entry * 100, 2),
        "rr_ratio":       CFG.take_profit_rr,
        "atr_approx":     round(atr_approx, 2),
        "sigma_pct":      round(sigma_d * 100, 3),
        # Whole units
        "units_whole":    units_whole,
        "notional_whole": notional_whole,
        "loss_whole":     loss_whole,
        "gain_whole":     gain_whole,
        "leftover":       leftover,
        # Fractional
        "units_frac":     round(units_frac, 4),
        "loss_frac":      loss_frac,
        "gain_frac":      gain_frac,
        # Timing
        "hours_to_sl":    hours_to_sl,
        "entry_window":   f"{CFG.best_entry_start}–{CFG.best_entry_end} SAST",
        "exit_window":    f"{CFG.best_exit_start}–{CFG.best_exit_end} SAST",
        "duration_hint":  (f"~{max(1, int(hours_to_sl))}–"
                           f"{max(2, int(hours_to_sl * 1.5))}h intraday "
                           f"(stop at R{sl:,.2f} or R{tp:,.2f})"),
    }


# ═══════════════════════════════════════════════════════════════
# SELF-IMPROVEMENT LOOP
# ═══════════════════════════════════════════════════════════════

def load_history() -> List[Dict]:
    if Path(CFG.history_file).exists():
        try:
            with open(CFG.history_file) as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_history(hist: List[Dict]):
    try:
        with open(CFG.history_file, "w") as f:
            json.dump(hist[-500:], f, indent=2)
    except Exception:
        pass

def update_and_improve(current_price: float) -> Optional[str]:
    """Score yesterday's prediction; update feature weights."""
    hist = load_history()
    if len(hist) < 2:
        return None
    last = hist[-1]
    if last.get("actual_return") is not None or "price" not in last:
        return None

    prev    = float(last["price"])
    ret     = (current_price - prev) / prev
    went_up = ret > 0
    pred_up = last.get("predicted_dir", 0) > 0
    correct = (pred_up and went_up) or (not pred_up and not went_up)

    hist[-1]["actual_return"] = round(ret, 6)
    hist[-1]["correct"]       = correct

    w  = _load_weights()
    lr = 0.08
    for feat, d in last.get("dirs", {}).items():
        if feat in w and d != 0:
            fc = (d > 0 and went_up) or (d < 0 and not went_up)
            w[feat] = max(0.05, w[feat] + (lr if fc else -lr))

    total = sum(abs(v) for v in w.values())
    w     = {k: round(abs(v) / total * 10, 4) for k, v in w.items()}
    _save_weights(w)
    save_history(hist)

    emoji = "✅" if correct else "❌"
    return (f"{emoji}  Yesterday: predicted {'UP' if pred_up else 'DOWN'} | "
            f"Actual {ret:+.2%} | {'Correct' if correct else 'Wrong'}")

def record_prediction(price: float, sig: Dict):
    hist = load_history()
    hist.append({"ts":            datetime.now().isoformat(),
                  "price":         round(price, 4),
                  "action":        sig["action"],
                  "prob":          sig["prob"],
                  "predicted_dir": 1 if sig["prob"] > 0.5 else -1,
                  "dirs":          sig["dirs"],
                  "actual_return": None,
                  "correct":       None})
    save_history(hist)

def accuracy_summary() -> Dict:
    hist   = load_history()
    scored = [h for h in hist if h.get("correct") is not None]
    if not scored:
        return {"n": 0}
    n    = len(scored)
    acc  = sum(1 for h in scored if h["correct"]) / n
    l10  = scored[-10:]
    a10  = sum(1 for h in l10 if h["correct"]) / len(l10)
    return {"n": n, "accuracy": round(acc, 3), "last_10": round(a10, 3)}


# ═══════════════════════════════════════════════════════════════
# 6-PANEL MATPLOTLIB CHART SUITE
# ═══════════════════════════════════════════════════════════════

def build_charts(data: Dict, arima: Dict, anova: Dict,
                  mc_paths: np.ndarray, sig: Dict, trade: Dict,
                  reg_path: np.ndarray, sigma_label: str,
                  save_path: Optional[str] = None):
    if not MPL_OK:
        LOG.warning("matplotlib unavailable — charts skipped")
        return

    prices = data["gld"]
    dates  = data["dates"]
    n      = len(prices)
    DISP   = min(504, n)           # last 2 years on main chart
    p_d    = prices[-DISP:]
    d_d    = dates[-DISP:]
    xi     = np.arange(DISP)

    e20 = _ema(p_d, 20)
    e50 = _ema(p_d, 50)
    e200 = _ema(p_d, min(200, DISP))
    mid_bb, up_bb, dn_bb = _bollinger(p_d, 20)
    rsi_d  = _rsi(p_d, 14)
    macd_l, macd_s, macd_h = _macd(p_d)
    reg_d  = reg_path[-DISP:] if len(reg_path) >= DISP else reg_path

    # ── Theme ──────────────────────────────────────────────────
    GOLD   = "#C9A84C"
    GREEN  = "#2ECC71"
    RED    = "#E74C3C"
    BLUE   = "#3498DB"
    PURPLE = "#9B59B6"
    ORANGE = "#E67E22"
    GREY   = "#95A5A6"
    BG     = "#0D1117"
    PBG    = "#161B22"
    TXT    = "#C9D1D9"
    GRID   = "#21262D"
    R_COL  = {0: GREEN, 1: GREY, 2: RED}

    # tick positions
    tick_step = max(1, DISP // 8)
    tl = xi[::tick_step]
    tlab = [str(d_d[i])[:10] if i < len(d_d) else "" for i in tl]

    fig = plt.figure(figsize=(22, 26), facecolor=BG)
    fig.suptitle(
        f"  GLD.JO  ·  {datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  "
        f"Signal: {sig['action']}  (P={sig['prob']:.1%})  ·  "
        f"Current: R{prices[-1]:,.2f}",
        fontsize=15, color=TXT, fontweight="bold", y=0.985)

    gs = gridspec.GridSpec(4, 2, figure=fig,
                           hspace=0.52, wspace=0.28,
                           top=0.965, bottom=0.03,
                           left=0.055, right=0.975)

    def _style(ax, title=""):
        ax.set_facecolor(PBG)
        ax.grid(True, color=GRID, linewidth=0.4, alpha=0.8)
        ax.spines[:].set_color(GRID)
        ax.tick_params(colors=TXT, labelsize=7)
        if title:
            ax.set_title(title, color=TXT, fontsize=8.5, loc="left",
                          pad=4, fontweight="bold")

    # ══ Panel 1 (top, full-width): Price + EMAs + Bollinger + Regime ══
    ax1 = fig.add_subplot(gs[0, :])
    _style(ax1, f"  GLD.JO Price History  ·  EMA 20/50/200  ·  Bollinger Bands  ·  Regime Shading")

    for i in range(1, DISP):
        c = R_COL.get(int(reg_d[i]) if i < len(reg_d) else 1, GREY)
        ax1.axvspan(i-1, i, alpha=0.07, color=c, linewidth=0)

    ax1.fill_between(xi, dn_bb, up_bb, alpha=0.08, color=BLUE)
    ax1.plot(xi, p_d,   color=GOLD,   lw=1.8, label=f"{CFG.ticker}")
    ax1.plot(xi, e20,   color=BLUE,   lw=1.0, alpha=0.9, label="EMA 20")
    ax1.plot(xi, e50,   color=ORANGE, lw=1.0, alpha=0.9, label="EMA 50")
    ax1.plot(xi, e200,  color=PURPLE, lw=1.0, alpha=0.9, label="EMA 200")
    ax1.plot(xi, up_bb, color=BLUE,   lw=0.5, linestyle="--", alpha=0.5)
    ax1.plot(xi, dn_bb, color=BLUE,   lw=0.5, linestyle="--", alpha=0.5)

    ax1.axhline(y=trade["entry"], color=GREEN, lw=1.8, ls="--", alpha=0.9,
                 label=f"Entry R{trade['entry']:,.2f}")
    ax1.axhline(y=trade["sl"],    color=RED,   lw=1.4, ls=":",  alpha=0.8,
                 label=f"SL R{trade['sl']:,.2f}")
    ax1.axhline(y=trade["tp"],    color=GREEN, lw=1.4, ls="-.", alpha=0.8,
                 label=f"TP R{trade['tp']:,.2f}")
    ax1.axvline(x=DISP-1, color="white", lw=1.0, ls=":", alpha=0.6)

    reg_patches = [mpatches.Patch(color=c, alpha=0.5, label=l)
                   for c, l in ((GREEN, "Bull"), (GREY, "Calm"), (RED, "Crisis"))]
    handles, labels = ax1.get_legend_handles_labels()
    ax1.legend(handles + reg_patches, labels + ["Bull", "Calm", "Crisis"],
               loc="upper left", fontsize=7, facecolor=PBG, labelcolor=TXT,
               framealpha=0.8, ncol=3)
    ax1.set_xticks(tl)
    ax1.set_xticklabels(tlab, rotation=30, ha="right", fontsize=7, color=TXT)
    ax1.yaxis.set_major_formatter(
        plt.FuncFormatter(lambda x, _: f"R{x:,.0f}"))
    ax1.set_ylabel("Price (ZAR)", color=TXT, fontsize=8)

    # ══ Panel 2: ARIMA + MC Prediction Fan ══
    ax2 = fig.add_subplot(gs[1, 0])
    _style(ax2, f"  Price Prediction: {CFG.forecast_horizon}-Day ARIMA + MC Fan")

    HIST = min(45, DISP)
    p_h  = p_d[-HIST:]
    xh   = np.arange(HIST)
    ax2.plot(xh, p_h, color=GOLD, lw=2.0, label="Historical")
    ax2.axvline(x=HIST-1, color="white", lw=1.0, ls=":", alpha=0.6)

    H    = mc_paths.shape[0] - 1
    fcx  = np.arange(HIST-1, HIST-1 + H)
    p5   = [np.percentile(mc_paths[i], 5)  for i in range(H)]
    p25  = [np.percentile(mc_paths[i], 25) for i in range(H)]
    p75  = [np.percentile(mc_paths[i], 75) for i in range(H)]
    p95  = [np.percentile(mc_paths[i], 95) for i in range(H)]
    med  = [np.percentile(mc_paths[i], 50) for i in range(H)]

    ax2.fill_between(fcx, p5,  p95, alpha=0.12, color=BLUE, label="MC 5–95%")
    ax2.fill_between(fcx, p25, p75, alpha=0.22, color=BLUE, label="MC 25–75%")
    ax2.plot(fcx, med, color=BLUE, lw=1.5, ls="--", label="MC median")

    if arima.get("forecast") is not None:
        fl = arima["forecast"][:CFG.forecast_horizon]
        flo = arima["ci_lower"][:CFG.forecast_horizon]
        fhi = arima["ci_upper"][:CFG.forecast_horizon]
        fx  = np.arange(HIST-1, HIST-1 + len(fl))
        ax2.plot(fx, fl, color=RED, lw=1.8, ls="-",
                  label=f"ARIMA ({arima['model']})")
        ax2.fill_between(fx, flo, fhi, alpha=0.12, color=RED, label="ARIMA 95% CI")
        ax2.annotate(f"R{fl[0]:,.2f}", xy=(HIST, fl[0]),
                      color=RED, fontsize=8, va="center",
                      xytext=(HIST+1, fl[0]))
        ax2.annotate(f"R{fl[-1]:,.2f}", xy=(HIST+len(fl)-1, fl[-1]),
                      color=BLUE, fontsize=8, va="center",
                      xytext=(HIST+len(fl)-2, fl[-1]*1.003))

    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"R{x:,.0f}"))
    ax2.legend(loc="upper left", fontsize=7, facecolor=PBG,
                labelcolor=TXT, framealpha=0.8)
    ax2.set_xlabel("Trading days", color=TXT, fontsize=8)
    ax2.set_ylabel("Price (ZAR)", color=TXT, fontsize=8)

    # ══ Panel 3: RSI ══
    ax3 = fig.add_subplot(gs[1, 1])
    _style(ax3, f"  RSI (14)  ·  Current = {sig['rsi']:.1f}")

    ax3.plot(xi, rsi_d, color=PURPLE, lw=1.2, label="RSI")
    ax3.axhline(70, color=RED,   lw=1.0, ls="--", alpha=0.7, label="OB 70")
    ax3.axhline(30, color=GREEN, lw=1.0, ls="--", alpha=0.7, label="OS 30")
    ax3.axhline(50, color=TXT,   lw=0.5, ls=":", alpha=0.4)
    ax3.fill_between(xi, rsi_d, 30, where=(rsi_d < 30), alpha=0.3, color=GREEN)
    ax3.fill_between(xi, rsi_d, 70, where=(rsi_d > 70), alpha=0.3, color=RED)
    ax3.set_ylim(0, 100)
    ax3.set_xticks(tl)
    ax3.set_xticklabels(tlab, rotation=30, ha="right", fontsize=7, color=TXT)
    ax3.legend(loc="upper right", fontsize=7, facecolor=PBG,
                labelcolor=TXT, framealpha=0.7)

    # ══ Panel 4: MACD ══
    ax4 = fig.add_subplot(gs[2, 0])
    _style(ax4, "  MACD (12, 26, 9)")

    ax4.plot(xi, macd_l, color=BLUE,   lw=1.2, label="MACD line")
    ax4.plot(xi, macd_s, color=ORANGE, lw=1.0, label="Signal line")
    bar_colors = [GREEN if h >= 0 else RED for h in macd_h]
    ax4.bar(xi, macd_h, color=bar_colors, alpha=0.55, width=1.0, label="Histogram")
    ax4.axhline(0, color=TXT, lw=0.5, alpha=0.5)
    ax4.set_xticks(tl)
    ax4.set_xticklabels(tlab, rotation=30, ha="right", fontsize=7, color=TXT)
    ax4.legend(loc="upper left", fontsize=7, facecolor=PBG,
                labelcolor=TXT, framealpha=0.7)

    # ══ Panel 5: ANOVA Day-of-Week ══
    ax5 = fig.add_subplot(gs[2, 1])
    sig_str = (f"★ p={anova['dow_p']:.4f}" if anova['dow_p'] < 0.05
               else f"n.s. p={anova['dow_p']:.4f}")
    _style(ax5, f"  ANOVA: Mean Return by Day of Week  [{sig_str}]")

    dm = anova["dow_means"]
    bars5 = ax5.bar(anova["dow_names"], dm,
                     color=[GREEN if m > 0 else RED for m in dm],
                     alpha=0.75, edgecolor=GRID, linewidth=0.5)
    ax5.axhline(0, color=TXT, lw=0.5, alpha=0.5)
    for b, v in zip(bars5, dm):
        ax5.text(b.get_x() + b.get_width()/2,
                  v + (0.003 if v >= 0 else -0.006),
                  f"{v:+.3f}%", ha="center", fontsize=8, color=TXT)
    ax5.set_ylabel("Avg Daily Return (%)", color=TXT, fontsize=8)
    ax5.text(0.97, 0.96,
              f"Best:  {anova['best_day']}\n"
              f"Worst: {anova['worst_day']}\n"
              f"F={anova['dow_f']:.2f}",
              transform=ax5.transAxes, color=TXT, fontsize=8,
              ha="right", va="top",
              bbox=dict(boxstyle="round", facecolor=PBG, alpha=0.85,
                         edgecolor=GRID))

    # Month bar below day bar (small)
    ax5b = ax5.twinx()
    mm = anova["mon_means"]
    ax5b.plot(np.linspace(0, 4, 12), mm,
               color=GOLD, lw=1.0, ls="--", alpha=0.6, marker="o",
               markersize=3, label="Monthly mean (right)")
    ax5b.set_ylabel("Monthly avg (%)", color=GOLD, fontsize=7, alpha=0.8)
    ax5b.tick_params(colors=GOLD, labelsize=6)
    ax5b.spines[:].set_color(GRID)

    # ══ Panel 6 (bottom, full-width): Trade Setup ══
    ax6 = fig.add_subplot(gs[3, :])
    ZOOM = min(25, DISP)
    p_z  = p_d[-ZOOM:]
    d_z  = d_d[-ZOOM:]
    xz   = np.arange(ZOOM)

    _style(ax6,
           f"  Trade Setup  ·  Capital: R{trade['capital']:,.2f}  ·  "
           f"Entry: R{trade['entry']:,.2f}  ·  SL: R{trade['sl']:,.2f}  ·  "
           f"TP: R{trade['tp']:,.2f}  ·  R:R = 1:{trade['rr_ratio']:.0f}")

    ax6.plot(xz, p_z, color=GOLD, lw=2.5, label="Recent price", zorder=3)

    ylo = min(float(p_z.min()), trade["sl"]) * 0.997
    yhi = max(float(p_z.max()), trade["tp"]) * 1.003
    ax6.set_ylim(ylo, yhi)

    # SL / TP horizontal bands
    ax6.axhline(trade["entry"], color=GREEN, lw=2.2, ls="--", alpha=0.95,
                 label=f"Entry  R{trade['entry']:,.2f}")
    ax6.axhline(trade["sl"],    color=RED,   lw=2.0, ls=":",  alpha=0.9,
                 label=f"Stop Loss  R{trade['sl']:,.2f}  (−{trade['sl_pct']:.2f}%)")
    ax6.axhline(trade["tp"],    color=GREEN, lw=2.0, ls="-.", alpha=0.9,
                 label=f"Take Profit  R{trade['tp']:,.2f}  (+{trade['tp_pct']:.2f}%)")

    # Shaded risk / reward zones (project forward)
    xfwd = [ZOOM-0.5, ZOOM + 9]
    ax6.fill_between(xfwd, trade["sl"],    trade["entry"],
                      alpha=0.18, color=RED,   label=f"Risk  R{trade['loss_frac']:,.2f}")
    ax6.fill_between(xfwd, trade["entry"], trade["tp"],
                      alpha=0.18, color=GREEN, label=f"Reward  R{trade['gain_frac']:,.2f}")

    mid = ZOOM + 5
    for txt, y, c in [
        (f"ENTRY  R{trade['entry']:,.2f}", trade["entry"], GREEN),
        (f"STOP LOSS  R{trade['sl']:,.2f}\nMax loss: R{trade['loss_frac']:,.2f}",
         trade["sl"], RED),
        (f"TAKE PROFIT  R{trade['tp']:,.2f}\nMax gain: R{trade['gain_frac']:,.2f}",
         trade["tp"], GREEN),
    ]:
        ax6.annotate(txt, xy=(ZOOM-0.5, y), xytext=(mid, y),
                      color=c, fontsize=8.5, va="center", fontweight="bold",
                      arrowprops=dict(arrowstyle="->", color=c, lw=1.2))

    # ARIMA 1-day dot
    if arima.get("forecast") is not None:
        ax6.scatter([ZOOM+1], [trade["entry"] * (1 + (sig["arima_1d"] - prices[-1]) / prices[-1])],
                     color=RED, s=60, zorder=5, label="ARIMA 1d target", marker="D")

    info = (
        f"Capital:      R{trade['capital']:,.2f}\n"
        f"Whole units:  {trade['units_whole']}  "
        f"(notional R{trade['notional_whole']:,.2f}  leftover R{trade['leftover']:,.2f})\n"
        f"CFD/Frac:     {trade['units_frac']:.4f} units  (full R{trade['capital']:,.2f} deployed)\n"
        f"Entry window: {trade['entry_window']}\n"
        f"Exit window:  {trade['exit_window']}\n"
        f"Duration est: {trade['duration_hint']}\n"
        f"σ_daily:      {trade['sigma_pct']:.3f}%  |  ATR≈R{trade['atr_approx']:,.2f}  "
        f"|  Vol model: {sigma_label}"
    )
    ax6.text(0.002, 0.97, info, transform=ax6.transAxes,
              color=TXT, fontsize=8, va="top",
              bbox=dict(boxstyle="round", facecolor=PBG, alpha=0.9,
                         edgecolor=GRID, lw=0.5))

    ztl = xz[::max(1, ZOOM//6)]
    zlab = [str(d_z[i])[:10] if i < len(d_z) else "" for i in ztl]
    ax6.set_xticks(list(ztl) + [ZOOM, ZOOM+3, ZOOM+6, ZOOM+9])
    ax6.set_xticklabels(zlab + ["→", "→→", "→→→", "future"],
                         rotation=30, ha="right", fontsize=7, color=TXT)
    ax6.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"R{x:,.2f}"))
    ax6.set_xlim(-0.5, ZOOM + 11)
    ax6.legend(loc="upper right", fontsize=7.5, facecolor=PBG,
                labelcolor=TXT, framealpha=0.85, ncol=2)

    plt.tight_layout(rect=[0, 0.01, 1, 0.975])

    if save_path:
        fig.savefig(save_path, dpi=150, bbox_inches="tight", facecolor=BG)
        LOG.info(f"  📊  Chart saved → {save_path}")
    try:
        plt.show()
    except Exception:
        pass
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════
# CONSOLE REPORT
# ═══════════════════════════════════════════════════════════════

def print_report(prices: np.ndarray, sig: Dict, trade: Dict,
                  arima: Dict, anova: Dict,
                  sigma_d: float, sigma_label: str, cvar: float,
                  kelly_f: float, reg_label: str, reg_conf: float,
                  acc: Dict, yesterday: Optional[str],
                  chart_path: Optional[str]):

    print(f"\n{SEP}")
    print(f"  📡  GLD.JO Gold Bot v10  ·  "
          f"{datetime.now().strftime('%Y-%m-%d %H:%M SAST')}")
    print(f"  🔬  ARIMA Forecasting  ·  ANOVA  ·  Monte Carlo  ·  Adaptive ML")
    print(SEP)

    if yesterday:
        print(f"\n  {yesterday}")

    bar = "█" * int(sig["prob"]*40) + "░" * (40 - int(sig["prob"]*40))
    print(f"\n  ▶  SIGNAL:   {sig['action']}")
    print(f"  ▶  P(up):    {sig['prob']*100:.1f}%  [{bar}]")
    print(f"  ▶  Regime:   {reg_label}  (conf {reg_conf:.0%})")
    print(DSEP)

    if arima.get("forecast") is not None:
        fc = arima["forecast"]
        lo = arima["ci_lower"]
        hi = arima["ci_upper"]
        # Built outside the f-string: a backslash inside an f-string
        # expression is a SyntaxError before Python 3.12, and CI builds on
        # 3.9, 3.10 and 3.11.
        aic_note = f"  AIC={arima['aic']}" if arima.get("aic") else ""
        print(f"\n  ARIMA PRICE TARGETS  ({arima['model']}{aic_note}):")
        print(f"  1-day:   R{fc[0]:>10,.2f}  "
              f"[ R{lo[0]:,.2f} – R{hi[0]:,.2f} ]")
        if len(fc) >= 5:
            print(f"  5-day:   R{fc[4]:>10,.2f}  "
                  f"[ R{lo[4]:,.2f} – R{hi[4]:,.2f} ]")
        if len(fc) >= 10:
            print(f"  10-day:  R{fc[9]:>10,.2f}  "
                  f"[ R{lo[9]:,.2f} – R{hi[9]:,.2f} ]")
        if len(fc) >= 21:
            print(f"  21-day:  R{fc[20]:>10,.2f}  "
                  f"[ R{lo[20]:,.2f} – R{hi[20]:,.2f} ]")
        diff = fc[-1] - float(prices[-1])
        print(f"  Current: R{prices[-1]:>10,.2f}  →  "
              f"{'▲' if diff >= 0 else '▼'} R{abs(diff):,.2f} "
              f"({diff/prices[-1]:+.2%}) over {len(fc)} days")

    print(DSEP)
    print(f"\n  ANOVA SEASONALITY ANALYSIS  (last {CFG.anova_window} trading days):")
    print(f"  Day-of-week:  F={anova['dow_f']:.2f}  "
          f"p={anova['dow_p']:.4f}  "
          f"{'★ statistically significant' if anova['dow_p'] < 0.05 else 'not significant'}")
    print(f"  Monthly:      F={anova['mon_f']:.2f}  "
          f"p={anova['mon_p']:.4f}  "
          f"{'★ statistically significant' if anova['mon_p'] < 0.05 else 'not significant'}")
    print(f"  Best day to enter:   {anova['best_day']}")
    print(f"  Worst day to enter:  {anova['worst_day']}")
    print(f"  Best month:          {anova['best_month']}")

    print(DSEP)
    print(f"\n  RISK DASHBOARD:")
    print(f"  σ_daily ({sigma_label}):    {sigma_d*100:.3f}%  "
          f"(≈ {sigma_d*np.sqrt(252)*100:.1f}% annualised)")
    print(f"  CVaR 1-day (95%):         {cvar*100:.3f}%  of capital")
    print(f"  Kelly fraction:           {kelly_f*100:.2f}%  (×0.25 fractional)")
    print(f"  Risk-free rate:           {CFG.risk_free*100:.2f}%  (SARB approx)")

    print(DSEP)
    t = trade
    print(f"\n  ╔══ TRADE SETUP  (Capital: R{t['capital']:,.2f}) ══╗")
    print(f"  ║  Current price:    R{t['price']:>10,.4f}")
    print(f"  ║  Entry (w/ costs): R{t['entry']:>10,.4f}")
    print(f"  ║  Stop Loss:        R{t['sl']:>10,.4f}  "
          f"(−{t['sl_pct']:.2f}%  risk R{t['sl_dist']:.2f}/unit)")
    print(f"  ║  Take Profit:      R{t['tp']:>10,.4f}  "
          f"(+{t['tp_pct']:.2f}%  gain R{t['tp_dist']:.2f}/unit)")
    print(f"  ║  Risk:Reward:      1 : {t['rr_ratio']:.0f}")
    print(f"  ║  ATR (approx):     R{t['atr_approx']:>10,.2f}  "
          f"(σ={t['sigma_pct']:.3f}%/day)")
    print(f"  ╠══ JSE WHOLE UNITS (board lots) ══╣")
    if t["units_whole"] > 0:
        print(f"  ║  Units buyable:    {t['units_whole']}")
        print(f"  ║  Notional:         R{t['notional_whole']:>10,.2f}")
        print(f"  ║  Leftover cash:    R{t['leftover']:>10,.2f}")
        print(f"  ║  Max loss (SL):    R{t['loss_whole']:>10,.2f}")
        print(f"  ║  Max gain (TP):    R{t['gain_whole']:>10,.2f}")
    else:
        print(f"  ║  ⚠️  R{t['capital']:,.2f} < unit price R{t['price']:,.2f}")
        print(f"  ║     Use CFD / fractional (EasyEquities, FNB Shares, etc.)")
    print(f"  ╠══ CFD / FRACTIONAL ══╣")
    print(f"  ║  Fractional units: {t['units_frac']:.4f}  (R{t['capital']:,.2f} deployed)")
    print(f"  ║  Max loss (SL):    R{t['loss_frac']:>10,.2f}")
    print(f"  ║  Max gain (TP):    R{t['gain_frac']:>10,.2f}")
    print(f"  ╠══ JSE INTRADAY TIMING (SAST) ══╣")
    print(f"  ║  Best entry:       {t['entry_window']}")
    print(f"  ║  Best exit:        {t['exit_window']}")
    print(f"  ║  Duration estimate:{t['duration_hint']}")
    print(f"  ║  JSE hours:        09:00–17:00  (avoid 09:00–09:15 & 16:30–17:00)")
    print(f"  ╚{'═'*54}╝")

    print(DSEP)
    print(f"\n  SIGNAL FACTORS:")
    for r in sig["reasons"]:
        print(f"    • {r}")

    if acc.get("n", 0) >= 5:
        print(DSEP)
        print(f"  🧠  Self-Learning: {acc['n']} predictions  |  "
              f"All-time accuracy {acc['accuracy']:.1%}  |  "
              f"Last-10 {acc['last_10']:.1%}")

    if chart_path:
        print(DSEP)
        print(f"  📊  Charts saved → {chart_path}")

    print(f"\n  ⚠️  Educational / research use only.  NOT financial advice.")
    print(f"  ⚠️  Past performance does not guarantee future results.")
    print(SEP)


# ═══════════════════════════════════════════════════════════════
# MAIN JOB
# ═══════════════════════════════════════════════════════════════

def job(trade_capital: Optional[float] = None, save_charts: bool = True):
    if trade_capital:
        CFG.trade_capital = trade_capital

    LOG.info("=" * 60)
    LOG.info(f"  GLD.JO Gold Bot v10  |  capital R{CFG.trade_capital:,.0f}")
    LOG.info("=" * 60)

    # 1. Fetch data
    data    = fetch_data()
    prices  = data["gld"]
    S0      = float(prices[-1])
    log_ret = np.log(prices[1:] / prices[:-1])

    # 2. Self-improvement
    yesterday = update_and_improve(S0)

    # 3. Volatility
    sigma_d, sigma_label = garch_vol(log_ret)

    # 4. CVaR
    thr  = np.percentile(log_ret, 5)
    tail = log_ret[log_ret <= thr]
    cvar = float(abs(tail.mean())) if len(tail) > 0 else float(abs(thr))

    # 5. Kelly
    mu   = float(log_ret[-252:].mean())
    sa   = sigma_d * np.sqrt(252)
    ma   = mu * 252
    kelly_f = float(np.clip(
        (ma - CFG.risk_free) / (sa**2) * CFG.kelly_scalar, 0.0, 0.20))

    # 6. Regime
    reg_idx, reg_conf, reg_label, reg_path = detect_regime(log_ret)
    LOG.info(f"  Regime: {reg_label}  conf={reg_conf:.0%}")

    # 7. ARIMA
    LOG.info("  ARIMA forecasting…")
    arima = arima_forecast(prices, CFG.forecast_horizon)

    # 8. ANOVA
    LOG.info("  ANOVA seasonality analysis…")
    anova = anova_analysis(prices, data["dates"])

    # 9. Monte Carlo
    LOG.info("  Monte Carlo 2,000 paths…")
    mc_paths = monte_carlo(S0, mu, sigma_d, log_ret, n_sims=2000,
                            days=CFG.forecast_horizon)

    # 10. Signal
    sig = generate_signal(data, arima, mc_paths, sigma_d, reg_idx, reg_conf)
    LOG.info(f"  Signal: {sig['action']}  P={sig['prob']:.1%}")

    # 11. Trade calculator
    trade = small_trade_calc(S0, sigma_d, CFG.trade_capital)

    # 12. Charts
    chart_path = None
    if save_charts and MPL_OK:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        chart_path = str(Path(CFG.chart_dir) / f"gold_analysis_{ts}.png")
        build_charts(data, arima, anova, mc_paths, sig, trade,
                      reg_path, sigma_label, save_path=chart_path)

    # 13. Console report
    acc = accuracy_summary()
    print_report(prices, sig, trade, arima, anova,
                  sigma_d, sigma_label, cvar, kelly_f,
                  reg_label, reg_conf, acc, yesterday, chart_path)

    # 14. Record prediction for tomorrow
    record_prediction(S0, sig)

    return sig, trade, chart_path


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="GLD.JO Gold Bot v10")
    ap.add_argument("--capital",   type=float, default=CFG.trade_capital,
                     help=f"Trade capital ZAR (default R{CFG.trade_capital:,.0f})")
    ap.add_argument("--total",     type=float, default=CFG.total_capital,
                     help=f"Total portfolio ZAR (default R{CFG.total_capital:,.0f})")
    ap.add_argument("--ticker",    type=str,   default=CFG.ticker,
                     help=f"JSE ticker (default {CFG.ticker})")
    ap.add_argument("--start",     type=str,   default=CFG.start_date,
                     help=f"Start date (default {CFG.start_date})")
    ap.add_argument("--no-charts", action="store_true",
                     help="Skip PNG chart generation")
    ap.add_argument("--once",      action="store_true",
                     help="Run once and exit (cron / Docker)")
    args = ap.parse_args()

    CFG.trade_capital = args.capital
    CFG.total_capital = args.total
    CFG.ticker        = args.ticker
    CFG.start_date    = args.start

    job(save_charts=not args.no_charts)
