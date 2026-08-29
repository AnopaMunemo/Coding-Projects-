

from __future__ import annotations
import os, sys, json, time, signal, warnings, logging, traceback, argparse
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ──────────────────────────────────────────────────────────────────────────────
# OPTIONAL IMPORTS  (degrade gracefully)
# ──────────────────────────────────────────────────────────────────────────────
try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

try:
    from statsmodels.tsa.stattools import coint, adfuller
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools import add_constant
    STATSMODELS_OK = True
except ImportError:
    STATSMODELS_OK = False
    print("⚠  pip install statsmodels  — cointegration disabled")

try:
    from arch import arch_model
    GARCH_OK = True
except ImportError:
    GARCH_OK = False
    print("⚠  pip install arch  — using EWMA vol fallback")

try:
    from hmmlearn import hmm as _hmm
    HMM_OK = True
except ImportError:
    HMM_OK = False
    print("⚠  pip install hmmlearn  — regime detection via vol fallback")

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.ensemble import GradientBoostingClassifier
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False
    print("⚠  pip install scikit-learn  — heuristic signal only")

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    from colorama import Fore, Style, init as _cinit
    _cinit(autoreset=True)
    COL = True
except ImportError:
    COL = False
    class Fore:
        GREEN = RED = YELLOW = CYAN = MAGENTA = RESET = ""
    class Style:
        BRIGHT = RESET_ALL = ""

import schedule

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Config:
    # Primary asset (XAUUSD spot — Yahoo Finance ticker)
    xauusd_ticker: str        = "GC=F"          # COMEX gold futures (best proxy)
    xauusd_spot: str          = "GLD"           # ETF fallback
    start_date: str           = "2015-01-01"

    # JSE gold ETF (secondary / ZAR context)
    gld_jo_ticker: str        = "GLD.JO"

    # Macro basket
    zar_ticker: str           = "ZAR=X"
    dxy_ticker: str           = "DX-Y.NYB"
    vix_ticker: str           = "^VIX"
    us10y_ticker: str         = "^TNX"

    # Portfolio
    capital_usd: float        = 10_000.0        # USD capital for XAUUSD sizing
    max_position_pct: float   = 0.20
    kelly_scalar: float       = 0.25            # fractional Kelly
    stop_loss_atr_mult: float = 2.0             # SL = entry ± 2× ATR
    take_profit_rr: float     = 2.0             # TP = entry ± 2× SL distance (2:1 R:R)

    # Signal thresholds (probability space)
    p_strong_buy: float       = 0.63
    p_buy: float              = 0.55
    p_sell: float             = 0.45
    p_strong_sell: float      = 0.37

    # GARCH / CVaR
    cvar_confidence: float    = 0.95

    # HMM
    n_regimes: int            = 3
    regime_conf_floor: float  = 0.55

    # Walk-forward
    wf_min_train: int         = 504             # 2 years
    wf_oos_window: int        = 21

    # Monte Carlo
    n_simulations: int        = 5_000
    forecast_days: int        = 63             # ~3 months fan

    # Telegram (fill in your own)
    telegram_token: str       = ""
    telegram_chat_id: str     = ""
    news_api_key: str         = ""

    # Persistence
    weights_file: str         = "xauusd_weights_v8.json"
    history_file: str         = "xauusd_history_v8.json"
    model_file: str           = "xauusd_model_v8.json"

    # Scheduling
    run_time: str             = "17:30"


CFG = Config()
SEP  = "═" * 72
DSEP = "─" * 72

# ──────────────────────────────────────────────────────────────────────────────
# LOGGER
# ──────────────────────────────────────────────────────────────────────────────

def _make_logger() -> logging.Logger:
    lg = logging.getLogger("goldbot_v8")
    if not lg.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        lg.addHandler(h)
    lg.setLevel(logging.INFO)
    return lg

LOG = _make_logger()
_SHUTDOWN = False

def _sig_handler(s, f):
    global _SHUTDOWN
    LOG.info("Shutdown signal — exiting cleanly.")
    _SHUTDOWN = True

signal.signal(signal.SIGINT,  _sig_handler)
signal.signal(signal.SIGTERM, _sig_handler)

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 1 — DATA
# ──────────────────────────────────────────────────────────────────────────────

class DataFeed:

    @staticmethod
    def _download(ticker: str, start: str) -> Optional[np.ndarray]:
        """Download a single series, return numpy array or None."""
        try:
            df = yf.download(ticker, start=start, auto_adjust=True, progress=False)
            if df.empty:
                return None
            # Handle MultiIndex columns from newer yfinance
            if isinstance(df.columns, pd.MultiIndex):
                df = df["Close"]
                if isinstance(df, pd.DataFrame):
                    df = df.iloc[:, 0]
            else:
                df = df["Close"]
            series = df.dropna().to_numpy(dtype=float)
            return series if len(series) > 50 else None
        except Exception as e:
            LOG.warning(f"  Download {ticker}: {e}")
            return None

    @staticmethod
    def fetch_all() -> Dict:
        """Fetch XAUUSD + macro basket. Returns aligned numpy arrays."""
        LOG.info(f"📥  Fetching market data from {CFG.start_date}…")

        # Try primary XAUUSD ticker, fallback to spot ETF
        xau = DataFeed._download(CFG.xauusd_ticker, CFG.start_date)
        if xau is None or len(xau) < 200:
            LOG.warning("  GC=F unavailable — trying GLD ETF")
            xau = DataFeed._download(CFG.xauusd_spot, CFG.start_date)
        if xau is None:
            raise RuntimeError("Cannot fetch XAUUSD data")

        # Macro
        zar  = DataFeed._download(CFG.zar_ticker,  CFG.start_date)
        dxy  = DataFeed._download(CFG.dxy_ticker,  CFG.start_date)
        vix  = DataFeed._download(CFG.vix_ticker,  CFG.start_date)
        us10 = DataFeed._download(CFG.us10y_ticker, CFG.start_date)

        # Align all to shortest length
        n = len(xau)
        for arr in [zar, dxy, vix, us10]:
            if arr is not None:
                n = min(n, len(arr))

        xau  = xau[-n:]
        zar  = zar[-n:]  if zar  is not None else np.ones(n)
        dxy  = dxy[-n:]  if dxy  is not None else np.ones(n)
        vix  = vix[-n:]  if vix  is not None else np.ones(n) * 20
        us10 = us10[-n:] if us10 is not None else np.ones(n) * 4.5

        LOG.info(f"  ✓  {n} days | XAUUSD={xau[-1]:,.2f} | DXY={dxy[-1]:.2f}"
                 f" | VIX={vix[-1]:.1f} | ZAR={zar[-1]:.4f}")

        return {"xau": xau, "zar": zar, "dxy": dxy, "vix": vix, "us10": us10, "n": n}

    @staticmethod
    def fetch_news_sentiment() -> float:
        """Lightweight news sentiment via NewsAPI (optional)."""
        if not CFG.news_api_key or not REQUESTS_OK:
            return 0.0
        BULL = {"surge","rally","high","record","gain","rise","soar","bullish","strong"}
        BEAR = {"fall","drop","crash","plunge","weak","bearish","pressure","slump","lose"}
        try:
            url = (f"https://newsapi.org/v2/everything"
                   f"?q=gold+XAUUSD+price&sortBy=publishedAt"
                   f"&pageSize=20&language=en&apiKey={CFG.news_api_key}")
            arts = requests.get(url, timeout=6).json().get("articles", [])
            pos = neg = 0
            for a in arts:
                t = (a.get("title", "") + " " + a.get("description", "")).lower()
                pos += sum(1 for w in BULL if f" {w}" in f" {t}")
                neg += sum(1 for w in BEAR if f" {w}" in f" {t}")
            total = pos + neg
            score = float((pos - neg) / total) if total > 0 else 0.0
            LOG.info(f"📰  News sentiment: {score:+.2f}  ({pos}↑ {neg}↓)")
            return float(np.clip(score, -1, 1))
        except Exception:
            return 0.0

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 2 — FEATURE ENGINEERING
# ──────────────────────────────────────────────────────────────────────────────

def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty(len(prices), dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def _rsi(prices: np.ndarray, period: int = 14) -> float:
    data   = prices[-(period * 4):]
    deltas = np.diff(data)
    gains  = np.where(deltas > 0,  deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    alpha  = 1.0 / period
    ag = float(gains[:period].mean()) if period <= len(gains) else 50.0
    al = float(losses[:period].mean()) if period <= len(losses) else 50.0
    for g, l in zip(gains[period:], losses[period:]):
        ag = alpha * g + (1 - alpha) * ag
        al = alpha * l + (1 - alpha) * al
    return 100.0 if al < 1e-8 else 100.0 - 100.0 / (1.0 + ag / al)

def _atr(prices: np.ndarray, period: int = 14) -> float:
    """Average True Range (using close-to-close as proxy)."""
    diffs = np.abs(np.diff(prices[-period*2:]))
    return float(np.mean(diffs[-period:]))

def _momentum(prices: np.ndarray, period: int = 20) -> float:
    if len(prices) <= period:
        return 0.0
    return float((prices[-1] - prices[-period-1]) / (prices[-period-1] + 1e-8))

def _vol_of_vol(log_ret: np.ndarray, window: int = 20) -> float:
    if len(log_ret) < window * 3:
        return 0.0
    rolling_std = pd.Series(log_ret).rolling(window).std()
    return float(rolling_std.tail(window).std())

def _realized_vol(log_ret: np.ndarray, window: int = 20) -> float:
    return float(np.std(log_ret[-window:]) * np.sqrt(252))

def _cointegration_zscore(y: np.ndarray, x: np.ndarray, window: int = 252
                           ) -> Tuple[float, float]:
    """Engle-Granger z-score. Returns (zscore, pvalue)."""
    if not STATSMODELS_OK or len(y) < window or len(x) < window:
        return 0.0, 1.0
    try:
        yy, xx = y[-window:].astype(float), x[-window:].astype(float)
        res   = OLS(yy, add_constant(xx)).fit()
        spread = res.resid
        _, pval, _ = coint(yy, xx)
        z = (spread[-1] - spread.mean()) / (spread.std() + 1e-8)
        return float(z), float(pval)
    except Exception:
        return 0.0, 1.0

def _linreg_slope(prices: np.ndarray, period: int = 20) -> float:
    y = prices[-period:].astype(float)
    x = np.arange(period, dtype=float)
    slope, *_ = stats.linregress(x, y)
    return float(slope) / float(prices[-1] + 1e-8)

def _hurst_exponent(prices: np.ndarray, max_lag: int = 80) -> float:
    lags, rs_vals = [], []
    try:
        for lag in range(2, min(max_lag, len(prices) // 4)):
            chunks = len(prices) // lag
            if chunks < 2:
                continue
            rs_list = []
            for i in range(chunks):
                seg = prices[i*lag:(i+1)*lag].astype(float)
                dev = np.cumsum(seg - seg.mean())
                R   = dev.max() - dev.min()
                S   = seg.std(ddof=1)
                if S > 0:
                    rs_list.append(R / S)
            if rs_list:
                rs_vals.append(np.log(np.mean(rs_list)))
                lags.append(np.log(lag))
        if len(lags) < 4:
            return 0.5
        slope, *_ = stats.linregress(lags, rs_vals)
        return float(np.clip(slope, 0.0, 1.0))
    except Exception:
        return 0.5

def compute_all_features(data: Dict) -> Dict:
    """Compute every feature used in signal generation."""
    xau = data["xau"]
    zar = data["zar"]
    dxy = data["dxy"]
    vix = data["vix"]
    us10 = data["us10"]

    log_ret = np.log(xau[1:] / xau[:-1])

    e20 = _ema(xau, 20)
    e50 = _ema(xau, 50)
    e200 = _ema(xau, 200)

    ema_trend_short = (e20[-1] - e50[-1]) / (xau[-1] + 1e-8)
    ema_trend_long  = (e50[-1] - e200[-1]) / (xau[-1] + 1e-8)

    rsi_val   = _rsi(xau, 14)
    rsi_norm  = (rsi_val - 50) / 50           # –1 to +1
    atr_14    = _atr(xau, 14)
    mom_20    = _momentum(xau, 20)
    mom_60    = _momentum(xau, 60)
    rv_20     = _realized_vol(log_ret, 20)
    vov       = _vol_of_vol(log_ret, 20)
    linreg    = _linreg_slope(xau, 20)
    hurst     = _hurst_exponent(xau, 60)

    coint_z, coint_p = _cointegration_zscore(xau, zar, 252)

    dxy_trend = float((dxy[-1] - dxy[-10]) / (dxy[-10] + 1e-8))
    vix_trend = float((vix[-1] - vix[-10]) / (vix[-10] + 1e-8))
    us10_chg  = float(us10[-1] - us10[-5])    # 5-day change in yield (%)
    real_rate = float(us10[-1])               # nominal proxy

    # Volatility regime indicator (vol expanding = bearish for trend)
    vol_regime = 1.0 if rv_20 > 0.20 else 0.0  # high-vol flag

    return {
        "ema_trend_short": ema_trend_short,
        "ema_trend_long":  ema_trend_long,
        "rsi_norm":        rsi_norm,
        "mom_20":          mom_20,
        "mom_60":          mom_60,
        "rv_20":           rv_20,
        "vov":             vov,
        "linreg":          linreg,
        "hurst":           hurst,
        "coint_z":         coint_z,
        "coint_p":         coint_p,
        "dxy_trend":       dxy_trend,
        "vix_trend":       vix_trend,
        "us10_chg":        us10_chg,
        "real_rate":       real_rate,
        "vol_regime":      vol_regime,
        "atr_14":          atr_14,
        "price":           float(xau[-1]),
        "log_ret":         log_ret,
    }

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 3 — VOLATILITY & RISK
# ──────────────────────────────────────────────────────────────────────────────

class RiskEngine:

    @staticmethod
    def garch_vol(log_ret: np.ndarray) -> Tuple[float, bool]:
        """GARCH(1,1)-t forecast. Returns (daily_sigma, converged)."""
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
                    a = float(p.get("alpha[1]", 0))
                    b = float(p.get("beta[1]",  0))
                    if 0 < a + b < 1:
                        return sigma, True
            except Exception:
                pass

        # EWMA fallback (λ=0.94)
        lam, var = 0.94, float(np.var(log_ret[:30]))
        for r in log_ret:
            var = lam * var + (1 - lam) * r**2
        return float(np.sqrt(var)), False

    @staticmethod
    def evt_cvar(log_ret: np.ndarray, conf: float = 0.95) -> Tuple[float, bool]:
        """EVT-GPD CVaR. Returns (cvar_frac, evt_used)."""
        try:
            thr = np.percentile(log_ret, (1 - conf) * 100)
            exc = -(log_ret[log_ret < thr] - thr)
            if len(exc) >= 15:
                shape, _, scale = stats.genpareto.fit(exc, floc=0)
                nu  = len(exc) / len(log_ret)
                alp = 1 - conf
                u   = -thr
                if shape < 1 and scale > 0:
                    if abs(shape) < 1e-6:
                        cvar = u + scale * (1 + np.log(nu / alp))
                    else:
                        cvar = u + (scale / (1 - shape)) * (
                            (nu / alp)**shape - 1) / shape
                    return float(abs(cvar)), True
        except Exception:
            pass
        cut  = np.percentile(log_ret, (1 - conf) * 100)
        tail = log_ret[log_ret <= cut]
        return float(abs(tail.mean() if len(tail) > 0 else cut)), False

    @staticmethod
    def kelly(mu_annual: float, sigma_annual: float, rf: float = 0.05) -> float:
        if sigma_annual <= 0:
            return 0.0
        f = (mu_annual - rf) / sigma_annual**2 * CFG.kelly_scalar
        return float(np.clip(f, 0.0, CFG.max_position_pct))

    @staticmethod
    def position_size_usd(nav: float, kelly_f: float, cvar_1d: float,
                           regime_idx: int, regime_conf: float) -> float:
        """Final position size in USD after all risk gates."""
        kelly_usd = kelly_f * nav
        cvar_usd  = 2.0 * cvar_1d * nav
        hard_usd  = nav * CFG.max_position_pct
        size      = min(kelly_usd, cvar_usd, hard_usd)

        # Crisis discount
        if regime_idx == 2:
            size *= 0.60

        # Regime confidence scaling
        if regime_conf < CFG.regime_conf_floor:
            scale = (regime_conf - 0.33) / (CFG.regime_conf_floor - 0.33)
            size *= max(0.0, min(1.0, scale))

        return max(0.0, size)

    @staticmethod
    def performance_metrics(log_ret: np.ndarray, rf_daily: float = 0.05/252) -> Dict:
        r = log_ret[-252:]
        if len(r) < 30:
            return {}
        ann_ret = float(r.mean() * 252)
        ann_vol = float(r.std()  * np.sqrt(252))
        sharpe  = (ann_ret - rf_daily * 252) / ann_vol if ann_vol > 0 else 0.0
        neg_r   = r[r < 0]
        down    = float(neg_r.std() * np.sqrt(252)) if len(neg_r) > 1 else 1e-8
        sortino = (ann_ret - rf_daily * 252) / down
        cum     = np.cumprod(1 + r)
        peak    = np.maximum.accumulate(cum)
        mdd     = float(((cum - peak) / (peak + 1e-8)).min())
        calmar  = ann_ret / abs(mdd) if abs(mdd) > 1e-6 else 0.0
        return {
            "sharpe":      round(sharpe,  3),
            "sortino":     round(sortino, 3),
            "calmar":      round(calmar,  3),
            "ann_return":  round(ann_ret * 100, 2),
            "ann_vol":     round(ann_vol * 100, 2),
            "max_drawdown":round(mdd * 100, 2),
        }

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 4 — REGIME DETECTION
# ──────────────────────────────────────────────────────────────────────────────

class RegimeDetector:

    @staticmethod
    def detect(log_ret: np.ndarray, n: int = 3) -> Tuple[int, float, str]:
        """
        HMM regime detection. Returns (regime_idx, confidence, label).
        0 = Bull (high return, low vol)
        1 = Calm / sideways
        2 = Crisis (low return, high vol)
        """
        if HMM_OK and len(log_ret) >= 120:
            try:
                X     = log_ret.reshape(-1, 1)
                model = _hmm.GaussianHMM(n_components=n, covariance_type="full",
                                         n_iter=200, random_state=42)
                model.fit(X)
                states = model.predict(X)
                probs  = model.predict_proba(X)

                # Sort by mean return: 0=bull, 1=calm, 2=crisis
                order = np.argsort(model.means_.flatten())[::-1]
                remap = {raw: rank for rank, raw in enumerate(order)}
                cur_raw  = int(states[-1])
                cur_rank = remap[cur_raw]
                conf     = float(probs[-1][cur_raw])

                labels = {
                    0: "🟢 Bull (high return, low vol)",
                    1: "🟡 Calm (sideways)",
                    2: "🔴 Crisis (high vol, negative return)",
                }
                return cur_rank, conf, labels.get(cur_rank, "Unknown")
            except Exception:
                pass

        # Vol-based fallback
        rv = float(np.std(log_ret[-20:]) * np.sqrt(252))
        if rv > 0.28:
            return 2, 0.65, "🔴 Crisis (vol fallback)"
        elif rv < 0.12:
            return 0, 0.65, "🟢 Bull (vol fallback)"
        return 1, 0.65, "🟡 Calm (vol fallback)"

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 5 — MONTE CARLO
# ──────────────────────────────────────────────────────────────────────────────

class MonteCarlo:

    @staticmethod
    def run(S0: float, mu: float, sigma: float,
            log_ret: np.ndarray) -> np.ndarray:
        """Merton jump-diffusion paths. Returns (forecast_days+1, n_sims)."""
        std  = np.std(log_ret)
        jmp  = log_ret[np.abs(log_ret) > 3 * std]
        jfreq = len(jmp) / max(len(log_ret) / 252, 1)
        jmu   = float(jmp.mean()) if len(jmp) > 0 else 0.0
        jsig  = float(jmp.std())  if len(jmp) > 1 else 1e-4

        dt   = 1 / 252
        n    = CFG.n_simulations
        days = CFG.forecast_days
        paths = np.zeros((days + 1, n))
        paths[0] = S0

        Z1  = np.random.standard_normal((days, n))
        Z2  = np.random.standard_normal((days, n))
        Poi = np.random.poisson(jfreq * dt, (days, n))

        drift = (mu - 0.5 * sigma**2) * dt
        diff  = sigma * np.sqrt(dt)

        for t in range(1, days + 1):
            jump     = Poi[t-1] * (jmu + jsig * Z2[t-1])
            paths[t] = paths[t-1] * np.exp(drift + diff * Z1[t-1] + jump)

        return paths

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 6 — ADAPTIVE ML SIGNAL ENGINE
# ──────────────────────────────────────────────────────────────────────────────

# Feature vector names (must match order in _feature_vec)
FEATURE_NAMES = [
    "ema_trend_short", "ema_trend_long", "rsi_norm",
    "mom_20", "mom_60", "rv_20", "vov", "linreg",
    "hurst_centered",  # hurst - 0.5
    "coint_z_clipped", "dxy_trend", "vix_trend",
    "us10_chg", "real_rate_centered",  # real_rate - 4.5
]

def _feature_vec(feats: Dict) -> np.ndarray:
    return np.array([
        feats["ema_trend_short"],
        feats["ema_trend_long"],
        feats["rsi_norm"],
        feats["mom_20"],
        feats["mom_60"],
        feats["rv_20"],
        feats["vov"],
        feats["linreg"],
        feats["hurst"] - 0.5,
        np.clip(feats["coint_z"], -3, 3),
        feats["dxy_trend"],
        feats["vix_trend"],
        feats["us10_chg"],
        feats["real_rate"] - 4.5,
    ], dtype=float)


class AdaptiveSignalEngine:
    """
    Self-improving signal engine. Each run:
      1. Loads saved model weights / ML model
      2. Retrains logistic regression on all available history
      3. Generates probability-based signal
      4. Saves updated weights for next run
    """

    def __init__(self):
        self.weights = self._load_weights()
        self.history = self._load_history()
        self.model   = None
        self.scaler  = None

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_weights(self) -> Dict[str, float]:
        defaults = {
            "ema_trend_short": 1.2,
            "ema_trend_long":  1.0,
            "rsi":             0.8,
            "momentum":        1.0,
            "coint":           1.2,
            "dxy":             1.0,
            "vix":             0.8,
            "regime":          1.5,
            "hurst":           0.8,
            "linreg":          0.7,
            "us10y":           0.9,
            "mc":              1.3,
            "news":            0.6,
        }
        if os.path.exists(CFG.weights_file):
            try:
                with open(CFG.weights_file) as f:
                    saved = json.load(f)
                defaults.update(saved)
                LOG.info(f"  ✓  Loaded adaptive weights from {CFG.weights_file}")
            except Exception:
                pass
        # L1-normalise to sum=10
        total = sum(abs(v) for v in defaults.values())
        return {k: round(abs(v) / total * 10, 4) for k, v in defaults.items()}

    def _save_weights(self):
        try:
            with open(CFG.weights_file, "w") as f:
                json.dump(self.weights, f, indent=2)
        except Exception:
            pass

    def _load_history(self) -> List[Dict]:
        if os.path.exists(CFG.history_file):
            try:
                with open(CFG.history_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return []

    def _save_history(self):
        try:
            with open(CFG.history_file, "w") as f:
                json.dump(self.history[-500:], f, indent=2)
        except Exception:
            pass

    # ── Model Training ────────────────────────────────────────────────────────

    def fit_ml_model(self, xau_prices: np.ndarray):
        """
        Build and fit a logistic regression + GBT ensemble on rolling history.
        Uses all available historical data to predict next-day direction.
        This runs every single time so the model continually improves.
        """
        if not SKLEARN_OK or len(xau_prices) < 300:
            return

        LOG.info("🔁  Retraining ML model on full history…")
        X_rows, y_rows = [], []
        log_ret = np.log(xau_prices[1:] / xau_prices[:-1])

        for i in range(200, len(xau_prices) - 1):
            p_sl = xau_prices[:i+1]
            lr   = np.log(p_sl[1:] / p_sl[:-1])
            feats = {
                "ema_trend_short": (_ema(p_sl, 20)[-1] - _ema(p_sl, 50)[-1]) / p_sl[-1],
                "ema_trend_long":  (_ema(p_sl, 50)[-1] - _ema(p_sl, 200)[-1]) / p_sl[-1] if i >= 200 else 0.0,
                "rsi_norm":        (_rsi(p_sl) - 50) / 50,
                "mom_20":          _momentum(p_sl, 20),
                "mom_60":          _momentum(p_sl, 60) if i >= 60 else 0.0,
                "rv_20":           _realized_vol(lr, 20),
                "vov":             _vol_of_vol(lr, 20),
                "linreg":          _linreg_slope(p_sl, 20),
                "hurst":           0.5,   # skip per-row (expensive) — use global
                "coint_z":         0.0,   # skip per-row — use today's value
                "dxy_trend":       0.0,
                "vix_trend":       0.0,
                "us10_chg":        0.0,
                "real_rate":       4.5,
            }
            vec   = _feature_vec(feats)
            label = 1 if xau_prices[i+1] > xau_prices[i] else 0
            X_rows.append(vec)
            y_rows.append(label)

        if len(X_rows) < 100:
            return

        X = np.array(X_rows)
        y = np.array(y_rows)

        # Remove nan/inf
        mask = np.isfinite(X).all(axis=1)
        X, y = X[mask], y[mask]
        if len(np.unique(y)) < 2:
            return

        scaler = StandardScaler()
        Xs     = scaler.fit_transform(X)

        # Logistic regression (L2 regularised)
        lr_m = LogisticRegression(penalty="l2", C=0.5, max_iter=500,
                                   random_state=42, class_weight="balanced")
        lr_m.fit(Xs, y)

        # Gradient Boosting for weight update signal
        if len(X) >= 200:
            try:
                gb_m = GradientBoostingClassifier(
                    n_estimators=80, max_depth=2,
                    learning_rate=0.05, random_state=42)
                gb_m.fit(Xs, y)
                importances = gb_m.feature_importances_
            except Exception:
                importances = np.ones(len(FEATURE_NAMES)) / len(FEATURE_NAMES)
        else:
            importances = np.ones(len(FEATURE_NAMES)) / len(FEATURE_NAMES)

        in_sample_acc = lr_m.score(Xs, y)
        LOG.info(f"  ✓  LR in-sample acc: {in_sample_acc:.3f} "
                 f"on {len(X)} samples")

        self.model  = lr_m
        self.scaler = scaler

        # Update named weights using LR coefficients × GBT importance
        lr_coef = lr_m.coef_[0]
        weight_map = {
            "ema_trend_short": 0, "ema_trend_long": 1,
            "rsi": 2, "momentum": 3,   # rsi_norm→3, mom_20→4
            "coint": 9, "dxy": 10, "vix": 11,
            "us10y": 12, "linreg": 7,
        }
        lr_rate = 0.10
        for wname, feat_idx in weight_map.items():
            if feat_idx < len(lr_coef) and wname in self.weights:
                direction   = float(lr_coef[feat_idx])
                importance  = float(importances[feat_idx]) if feat_idx < len(importances) else 0.05
                self.weights[wname] = max(
                    0.05,
                    self.weights[wname] + lr_rate * direction * (1 + importance)
                )

        # Re-normalise
        total = sum(abs(v) for v in self.weights.values())
        self.weights = {k: round(abs(v) / total * 10, 4) for k, v in self.weights.items()}
        self._save_weights()
        LOG.info("  ✓  Adaptive weights updated and saved")

    def update_weights_from_yesterday(self, current_price: float):
        """
        If we have a prediction from last run, score it and update weights.
        This is the core self-improvement loop.
        """
        if len(self.history) < 2:
            return

        last = self.history[-1]
        if "actual_return" in last and last["actual_return"] is not None:
            return  # already updated

        if "price" not in last or "predicted_dir" not in last:
            return

        prev_price  = float(last["price"])
        actual_ret  = (current_price - prev_price) / prev_price
        went_up     = actual_ret > 0
        pred_dir    = int(last["predicted_dir"])  # +1 or -1
        correct     = (pred_dir > 0 and went_up) or (pred_dir < 0 and not went_up)

        # Record result
        self.history[-1]["actual_return"] = round(actual_ret, 6)
        self.history[-1]["correct"] = correct

        # Update individual feature weights
        feature_dirs = last.get("feature_dirs", {})
        lr = 0.08
        for feat, d in feature_dirs.items():
            if feat in self.weights and d != 0:
                feat_correct = (d > 0 and went_up) or (d < 0 and not went_up)
                self.weights[feat] = max(
                    0.05, self.weights[feat] + (lr if feat_correct else -lr))

        # Re-normalise
        total = sum(abs(v) for v in self.weights.values())
        self.weights = {k: round(abs(v) / total * 10, 4) for k, v in self.weights.items()}
        self._save_weights()
        self._save_history()

        emoji = "✅" if correct else "❌"
        LOG.info(f"  {emoji}  Yesterday's prediction: "
                 f"{'UP' if pred_dir>0 else 'DOWN'} | "
                 f"Actual: {actual_ret:+.2%} | "
                 f"{'Correct' if correct else 'Wrong'}")

    # ── Signal Generation ─────────────────────────────────────────────────────

    def signal_probability(self, feats: Dict, sentiment: float = 0.0) -> Tuple[float, Dict]:
        """
        Returns P(price up tomorrow) ∈ [0, 1] and feature directions.
        Uses ML model if available, otherwise disciplined heuristic.
        """
        dirs: Dict[str, int] = {}

        if self.model is not None and self.scaler is not None:
            try:
                vec = _feature_vec(feats).reshape(1, -1)
                p   = float(self.model.predict_proba(
                    self.scaler.transform(vec))[0, 1])
                # Blend with sentiment
                if abs(sentiment) > 0.1:
                    p = 0.85 * p + 0.15 * (0.5 + 0.5 * sentiment)
                p = float(np.clip(p, 0.01, 0.99))
                dirs = self._compute_dirs(feats)
                return p, dirs
            except Exception:
                pass

        # Heuristic fallback (domain-driven)
        raw = 0.0
        w   = self.weights

        # EMA trend
        ema_s = feats["ema_trend_short"]
        if ema_s > 0:
            raw += w.get("ema_trend_short", 1.0)
            dirs["ema_trend_short"] = 1
        else:
            raw -= w.get("ema_trend_short", 1.0)
            dirs["ema_trend_short"] = -1

        ema_l = feats["ema_trend_long"]
        if ema_l > 0:
            raw += w.get("ema_trend_long", 1.0)
            dirs["ema_trend_long"] = 1
        else:
            raw -= w.get("ema_trend_long", 1.0)
            dirs["ema_trend_long"] = -1

        # RSI
        rsi = feats["rsi_norm"]
        if rsi < -0.3:
            raw += w.get("rsi", 0.8); dirs["rsi"] = 1
        elif rsi > 0.3:
            raw -= w.get("rsi", 0.8); dirs["rsi"] = -1
        else:
            dirs["rsi"] = 0

        # Momentum
        if feats["mom_20"] > 0.005:
            raw += w.get("momentum", 1.0); dirs["momentum"] = 1
        elif feats["mom_20"] < -0.005:
            raw -= w.get("momentum", 1.0); dirs["momentum"] = -1
        else:
            dirs["momentum"] = 0

        # Cointegration (ZAR/USD)
        cz = feats["coint_z"]
        if cz < -1.5:
            raw += w.get("coint", 1.2); dirs["coint"] = 1
        elif cz > 1.5:
            raw -= w.get("coint", 1.2); dirs["coint"] = -1
        else:
            dirs["coint"] = 0

        # DXY (gold inverse to dollar)
        if feats["dxy_trend"] < -0.005:
            raw += w.get("dxy", 1.0); dirs["dxy"] = 1
        elif feats["dxy_trend"] > 0.005:
            raw -= w.get("dxy", 1.0); dirs["dxy"] = -1
        else:
            dirs["dxy"] = 0

        # VIX (safe haven)
        if feats["vix_trend"] > 0.05:
            raw += w.get("vix", 0.8); dirs["vix"] = 1
        elif feats["vix_trend"] < -0.05:
            raw -= w.get("vix", 0.8) * 0.5; dirs["vix"] = -1
        else:
            dirs["vix"] = 0

        # Real rates (rising real rates = headwind for gold)
        if feats["us10_chg"] > 0.10:
            raw -= w.get("us10y", 0.9); dirs["us10y"] = -1
        elif feats["us10_chg"] < -0.10:
            raw += w.get("us10y", 0.9); dirs["us10y"] = 1
        else:
            dirs["us10y"] = 0

        # Vol-of-vol dampening (regime uncertainty → scale down)
        raw *= max(0.3, 1.0 - feats["vov"] * 40)

        # Sentiment
        raw += sentiment * w.get("news", 0.6)
        dirs["news"] = 1 if sentiment > 0.1 else (-1 if sentiment < -0.1 else 0)

        p = float(1 / (1 + np.exp(-raw)))
        return float(np.clip(p, 0.01, 0.99)), dirs

    def _compute_dirs(self, feats: Dict) -> Dict[str, int]:
        dirs = {}
        dirs["ema_trend_short"] = 1 if feats["ema_trend_short"] > 0 else -1
        dirs["ema_trend_long"]  = 1 if feats["ema_trend_long"]  > 0 else -1
        dirs["rsi"]             = (1 if feats["rsi_norm"] < -0.3 else
                                   -1 if feats["rsi_norm"] > 0.3 else 0)
        dirs["momentum"]        = (1 if feats["mom_20"] > 0.005 else
                                   -1 if feats["mom_20"] < -0.005 else 0)
        dirs["coint"]           = (1 if feats["coint_z"] < -1.5 else
                                   -1 if feats["coint_z"] > 1.5 else 0)
        dirs["dxy"]             = (1 if feats["dxy_trend"] < -0.005 else
                                   -1 if feats["dxy_trend"] > 0.005 else 0)
        dirs["vix"]             = (1 if feats["vix_trend"] > 0.05 else
                                   -1 if feats["vix_trend"] < -0.05 else 0)
        dirs["us10y"]           = (1 if feats["us10_chg"] < -0.10 else
                                   -1 if feats["us10_chg"] > 0.10 else 0)
        return dirs

    def record_prediction(self, price: float, prob: float,
                          action: str, dirs: Dict):
        """Save today's prediction for tomorrow's accuracy check."""
        entry = {
            "ts":            datetime.now().isoformat(),
            "price":         round(price, 4),
            "probability":   round(prob, 4),
            "action":        action,
            "predicted_dir": 1 if prob > 0.5 else -1,
            "feature_dirs":  dirs,
            "actual_return": None,
            "correct":       None,
        }
        self.history.append(entry)
        self._save_history()

    def accuracy_summary(self) -> Dict:
        """Rolling accuracy of past predictions."""
        scored = [h for h in self.history if h.get("correct") is not None]
        if not scored:
            return {"n": 0, "accuracy": None}
        n   = len(scored)
        acc = sum(1 for h in scored if h["correct"]) / n
        last_10 = scored[-10:]
        acc_10  = sum(1 for h in last_10 if h["correct"]) / len(last_10)
        return {"n": n, "accuracy": round(acc, 3), "last_10_acc": round(acc_10, 3)}

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 7 — WALK-FORWARD VALIDATION
# ──────────────────────────────────────────────────────────────────────────────

class WalkForward:

    @staticmethod
    def run(prices: np.ndarray) -> Dict:
        n = len(prices)
        if n < CFG.wf_min_train + CFG.wf_oos_window + 10:
            return {}

        oos_rets = []
        step     = max(5, CFG.wf_oos_window // 3)

        for t in range(CFG.wf_min_train, n - CFG.wf_oos_window, step):
            p_is   = prices[:t]
            lr_is  = np.log(p_is[1:] / p_is[:-1])
            e20    = _ema(p_is, 20)
            e50    = _ema(p_is, 50)
            rsi    = _rsi(p_is)
            lr_sl  = _linreg_slope(p_is, 20)
            score  = (
                (1 if e20[-1] > e50[-1] else -1) +
                (1 if rsi < 35 else (-1 if rsi > 65 else 0)) +
                (1 if lr_sl > 0.001 else (-1 if lr_sl < -0.001 else 0)) +
                (1 if _momentum(p_is, 20) > 0 else -1)
            )
            future = prices[t:t + CFG.wf_oos_window]
            if len(future) < 2:
                continue
            oos_r = np.log(future[-1] / future[0])
            oos_rets.append(oos_r * (1 if score > 0 else -1))

        if len(oos_rets) < 5:
            return {}

        arr    = np.array(oos_rets)
        sharpe = float(arr.mean() / (arr.std() + 1e-8) * np.sqrt(252 / CFG.wf_oos_window))
        win_rt = float((arr > 0).mean())
        cum    = np.cumprod(1 + arr)
        peak   = np.maximum.accumulate(cum)
        mdd    = float(((cum - peak) / (peak + 1e-8)).min())

        return {
            "oos_sharpe":   round(sharpe, 3),
            "oos_win_rate": round(win_rt, 3),
            "oos_max_dd":   round(mdd * 100, 2),
            "oos_n":        len(oos_rets),
        }

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 8 — LEVELS (Entry / Stop / Target)
# ──────────────────────────────────────────────────────────────────────────────

def compute_trade_levels(price: float, feats: Dict, action: str,
                          sigma_daily: float) -> Dict:
    """
    Compute actionable levels:
    - Entry: realistic fill (spread + slippage)
    - Stop Loss: ATR-based
    - Take Profit: fixed R:R ratio
    - Position size in USD lots
    """
    spread_frac  = 0.0002   # ~$0.20 on $1000 gold = 0.02%
    slippage     = 0.0001
    entry_cost   = spread_frac + slippage

    if "BUY" in action:
        entry  = price * (1 + entry_cost)
        sl     = entry - feats["atr_14"] * CFG.stop_loss_atr_mult
        tp     = entry + (entry - sl) * CFG.take_profit_rr
        side   = "LONG"
    elif "SELL" in action:
        entry  = price * (1 - entry_cost)
        sl     = entry + feats["atr_14"] * CFG.stop_loss_atr_mult
        tp     = entry - (sl - entry) * CFG.take_profit_rr
        side   = "SHORT"
    else:
        return {"entry": price, "sl": None, "tp": None, "side": "FLAT",
                "risk_per_unit": 0, "units": 0, "notional_usd": 0}

    risk_per_unit = abs(entry - sl)
    risk_budget   = CFG.capital_usd * CFG.max_position_pct * 0.02  # 2% portfolio risk
    units         = max(0, int(risk_budget / risk_per_unit)) if risk_per_unit > 0 else 0
    notional      = units * entry
    rr_ratio      = abs(tp - entry) / abs(sl - entry) if abs(sl - entry) > 0 else 0

    return {
        "side":          side,
        "entry":         round(entry, 2),
        "stop_loss":     round(sl, 2),
        "take_profit":   round(tp, 2),
        "risk_per_unit": round(risk_per_unit, 2),
        "rr_ratio":      round(rr_ratio, 2),
        "units":         units,
        "notional_usd":  round(notional, 2),
        "risk_usd":      round(risk_budget, 2),
        "breakeven_pct": round(entry_cost * 100, 4),
    }

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 9 — TELEGRAM
# ──────────────────────────────────────────────────────────────────────────────

def send_telegram(msg: str):
    if not CFG.telegram_token or not CFG.telegram_chat_id or not REQUESTS_OK:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage",
            json={"chat_id": CFG.telegram_chat_id, "text": msg,
                  "parse_mode": "Markdown"},
            timeout=8)
    except Exception:
        pass

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 10 — DISPLAY
# ──────────────────────────────────────────────────────────────────────────────

def _c(text: str, color: str) -> str:
    if not COL:
        return text
    return color + text + Style.RESET_ALL

def _action_color(action: str) -> str:
    if "BUY" in action:
        return Fore.GREEN
    if "SELL" in action:
        return Fore.RED
    return Fore.YELLOW

def print_signal_output(
    feats: Dict,
    prob: float,
    action: str,
    regime_idx: int,
    regime_conf: float,
    regime_label: str,
    sigma_d: float,
    cvar_1d: float,
    kelly_f: float,
    pos_usd: float,
    levels: Dict,
    mc_paths: np.ndarray,
    perf: Dict,
    wf: Dict,
    dirs: Dict,
    sentiment: float,
    reasons: List[str],
    acc_summary: Dict,
):
    now  = datetime.now().strftime("%Y-%m-%d %H:%M")
    S0   = feats["price"]
    atr  = feats["atr_14"]
    garch_str  = f"GARCH(1,1) σ={sigma_d*100:.3f}%/day  ({sigma_d*np.sqrt(252)*100:.1f}%/yr)"
    regime_str = regime_label

    ac = _action_color(action)

    print(f"\n{SEP}")
    print(f"  📡  XAUUSD GOLD QUANT v8  ·  {now}")
    print(f"  🔬  Adaptive ML • HMM Regimes • GARCH-EVT • Walk-Forward")
    print(SEP)

    # ── Main Signal ──────────────────────────────────────────────────────────
    prob_pct   = prob * 100
    conf_label = ("STRONG" if abs(prob - 0.5) > 0.13 else
                  "MODERATE" if abs(prob - 0.5) > 0.05 else "WEAK")
    bar_len    = 40
    filled     = int(prob * bar_len)
    bar        = "█" * filled + "░" * (bar_len - filled)

    print(f"\n  ▶  SIGNAL:  {_c(action, ac)}   ({conf_label} confidence)")
    print(f"  ▶  Probability (price up):  {prob_pct:.1f}%")
    print(f"     [{bar}]  {prob_pct:.0f}%")
    print(f"     ◄ SELL ─────────────────────────────── BUY ►")
    print(DSEP)

    # ── Price + Levels ───────────────────────────────────────────────────────
    print(f"\n  XAUUSD current:   ${S0:>12,.2f}")
    print(f"  ATR (14-day):     ${atr:>12,.2f}")

    if levels["side"] != "FLAT":
        entry_fmt = f"${levels['entry']:>12,.2f}"
        sl_fmt    = f"${levels['stop_loss']:>12,.2f}"
        tp_fmt    = f"${levels['take_profit']:>12,.2f}"
        sl_dist   = abs(levels['entry'] - levels['stop_loss'])
        tp_dist   = abs(levels['take_profit'] - levels['entry'])

        print(f"\n  ┌── TRADE LEVELS ({levels['side']}) ─────────────────────────────")
        print(f"  │  Entry (realistic fill): {_c(entry_fmt, Fore.CYAN)}")
        print(f"  │  Stop Loss:              {_c(sl_fmt, Fore.RED)}"
              f"   (−${sl_dist:,.2f}  /{sl_dist/S0*100:.2f}%)")
        print(f"  │  Take Profit:            {_c(tp_fmt, Fore.GREEN)}"
              f"   (+${tp_dist:,.2f}  /{tp_dist/S0*100:.2f}%)")
        print(f"  │  Risk:Reward ratio:      1 : {levels['rr_ratio']:.1f}")
        print(f"  │  Breakeven (costs):      {levels['breakeven_pct']:.3f}%")
        print(f"  │  Suggested position:     {levels['units']} units"
              f"  ≈ ${levels['notional_usd']:,.0f} notional")
        print(f"  │  Max risk per trade:     ${levels['risk_usd']:,.2f}"
              f"  (2% of ${CFG.capital_usd:,.0f} capital)")
        print(f"  └{'─'*62}")
    else:
        print(f"\n  ⚪  HOLD — no trade. Wait for clearer signal.")

    # ── Risk Dashboard ───────────────────────────────────────────────────────
    print(f"\n{DSEP}")
    print("  📊  RISK DASHBOARD")
    print(DSEP)
    print(f"  {garch_str}")
    print(f"  CVaR (95%, 1-day):    {cvar_1d*100:.3f}%  of capital")
    print(f"  Kelly fraction:       {kelly_f*100:.2f}%  (×{CFG.kelly_scalar} fractional)")
    print(f"  Position size:        ${pos_usd:,.2f}")
    print(f"  Hurst exponent:       {feats['hurst']:.4f}"
          f"  ({'trending' if feats['hurst']>0.55 else 'mean-rev' if feats['hurst']<0.45 else 'random walk'})")
    print(f"  Regime:               {regime_str}  (confidence {regime_conf:.0%})")
    print(f"  EG coint z-score:     {feats['coint_z']:.3f}"
          f"  (p={feats['coint_p']:.3f})")
    if perf:
        print(DSEP)
        print(f"  252-day performance on XAUUSD:")
        print(f"  Sharpe={perf.get('sharpe','—')}  "
              f"Sortino={perf.get('sortino','—')}  "
              f"Calmar={perf.get('calmar','—')}")
        print(f"  Ann.Ret={perf.get('ann_return','—')}%  "
              f"Vol={perf.get('ann_vol','—')}%  "
              f"MaxDD={perf.get('max_drawdown','—')}%")

    # ── Monte Carlo Fan ──────────────────────────────────────────────────────
    print(DSEP)
    h21   = mc_paths[min(21,  len(mc_paths)-1)]
    h63   = mc_paths[min(63,  len(mc_paths)-1)]
    print(f"  📈  Monte Carlo ({CFG.n_simulations:,} Merton jump-diffusion paths):")
    print(f"  1-month (21d)  p5=${np.percentile(h21,5):>8,.2f}  "
          f"median=${np.percentile(h21,50):>8,.2f}  "
          f"p95=${np.percentile(h21,95):>8,.2f}")
    print(f"  3-month (63d)  p5=${np.percentile(h63,5):>8,.2f}  "
          f"median=${np.percentile(h63,50):>8,.2f}  "
          f"p95=${np.percentile(h63,95):>8,.2f}")
    up_1m = float((h21 > S0).mean())
    print(f"  P(above current price in 1m): {up_1m:.1%}")

    # ── Walk-Forward ─────────────────────────────────────────────────────────
    if wf:
        print(DSEP)
        print(f"  📐  Walk-Forward OOS Validation:")
        print(f"  Sharpe={wf.get('oos_sharpe','—')}  "
              f"Win rate={wf.get('oos_win_rate',0)*100:.1f}%  "
              f"MaxDD={wf.get('oos_max_dd','—')}%  "
              f"Periods={wf.get('oos_n','—')}")

    # ── Signal Breakdown ─────────────────────────────────────────────────────
    print(DSEP)
    print(f"  📋  SIGNAL BREAKDOWN:")
    for r in reasons:
        print(f"    • {r}")
    if sentiment != 0.0:
        print(f"    • News sentiment: {sentiment:+.2f}"
              f" {'↑' if sentiment>0 else '↓'}")

    # ── Model Accuracy ───────────────────────────────────────────────────────
    if acc_summary.get("n", 0) >= 5:
        print(DSEP)
        print(f"  🧠  Self-Learning Stats:")
        print(f"  All-time accuracy:  {acc_summary['accuracy']:.1%}"
              f"  (n={acc_summary['n']})")
        print(f"  Last-10 accuracy:   {acc_summary.get('last_10_acc',0):.1%}")

    print(f"\n  ⚠️  DISCLAIMER: Educational / research use only."
          f" NOT financial advice.")
    print(SEP)

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 11 — BUILD REASONS LIST
# ──────────────────────────────────────────────────────────────────────────────

def build_reasons(feats: Dict, dirs: Dict, regime_label: str,
                  regime_conf: float, mc_prob_up: float) -> List[str]:
    reasons = []

    # EMA
    ema_s = feats["ema_trend_short"]
    ema_l = feats["ema_trend_long"]
    reasons.append(f"EMA20/50: {'above' if ema_s>0 else 'below'} "
                   f"({'↑ bullish' if ema_s>0 else '↓ bearish'})")
    reasons.append(f"EMA50/200: {'above' if ema_l>0 else 'below'} "
                   f"({'↑ major uptrend' if ema_l>0 else '↓ major downtrend'})")

    # RSI
    rsi_val = (feats["rsi_norm"] * 50) + 50
    if rsi_val < 35:
        reasons.append(f"RSI {rsi_val:.1f} — oversold ↑ (mean-reversion buy signal)")
    elif rsi_val > 65:
        reasons.append(f"RSI {rsi_val:.1f} — overbought ↓ (mean-reversion sell signal)")
    else:
        reasons.append(f"RSI {rsi_val:.1f} — neutral zone")

    # Momentum
    m20 = feats["mom_20"]
    reasons.append(f"20-day momentum: {m20:+.2%}"
                   f" ({'↑' if m20>0 else '↓'})")

    # DXY
    dxy_t = feats["dxy_trend"]
    reasons.append(f"USD (DXY) 10-day trend: {dxy_t:+.2%}"
                   f" → gold {'tailwind ↑' if dxy_t<0 else 'headwind ↓'}")

    # VIX
    vix_t = feats["vix_trend"]
    if abs(vix_t) > 0.03:
        reasons.append(f"VIX 10-day trend: {vix_t:+.1%}"
                       f" ({'safe-haven demand ↑' if vix_t>0 else 'risk-on ↓'})")

    # US10Y
    u10 = feats["us10_chg"]
    if abs(u10) > 0.05:
        reasons.append(f"US 10Y yield 5-day Δ: {u10:+.2f}%"
                       f" → gold {'headwind ↓' if u10>0 else 'tailwind ↑'}")

    # Cointegration
    cz = feats["coint_z"]
    cp = feats["coint_p"]
    if abs(cz) > 1.5:
        sig = "★ cointegrated" if cp < 0.05 else "(not significant)"
        reasons.append(f"ZAR coint z-score: {cz:.2f} {sig}"
                       f" → {'undervalued ↑' if cz<0 else 'overvalued ↓'}")

    # Hurst
    h = feats["hurst"]
    if h > 0.55:
        reasons.append(f"Hurst={h:.3f} → trending (follow the trend)")
    elif h < 0.45:
        reasons.append(f"Hurst={h:.3f} → mean-reverting (fade extremes)")

    # Regime
    reasons.append(f"HMM Regime: {regime_label} (conf {regime_conf:.0%})")

    # MC
    reasons.append(f"MC 1-month: P(up)={mc_prob_up:.1%}")

    return reasons

# ──────────────────────────────────────────────────────────────────────────────
# SECTION 12 — MAIN JOB
# ──────────────────────────────────────────────────────────────────────────────

def job():
    LOG.info("=" * 60)
    LOG.info("  GOLD QUANT v8 — starting analysis…")
    LOG.info("=" * 60)

    try:
        # 1. Fetch data
        data = DataFeed.fetch_all()
        xau  = data["xau"]
        S0   = float(xau[-1])

        # 2. Compute features
        feats   = compute_all_features(data)
        log_ret = feats["log_ret"]

        # 3. Signal engine (adaptive / self-improving)
        engine = AdaptiveSignalEngine()
        engine.update_weights_from_yesterday(S0)

        # 4. Retrain ML model every run (self-improving core loop)
        engine.fit_ml_model(xau)

        # 5. News sentiment (optional)
        sentiment = DataFeed.fetch_news_sentiment()

        # 6. Signal probability
        prob, dirs = engine.signal_probability(feats, sentiment)

        # 7. Action from probability
        if prob >= CFG.p_strong_buy:
            action = "🟢 STRONG BUY"
        elif prob >= CFG.p_buy:
            action = "🟡 BUY"
        elif prob <= CFG.p_strong_sell:
            action = "🔴 STRONG SELL"
        elif prob <= CFG.p_sell:
            action = "🟠 SELL"
        else:
            action = "⚪ HOLD"

        # 8. Volatility & risk
        sigma_d, garch_ok = RiskEngine.garch_vol(log_ret)
        cvar_frac, evt_ok = RiskEngine.evt_cvar(log_ret, CFG.cvar_confidence)
        cvar_1d           = cvar_frac  # daily CVaR as fraction of capital
        mu                = float(log_ret[-252:].mean())
        sigma_a           = sigma_d * np.sqrt(252)
        mu_a              = mu * 252
        kelly_f           = RiskEngine.kelly(mu_a, sigma_a, rf=0.05)
        perf              = RiskEngine.performance_metrics(log_ret)

        LOG.info(f"  σ_daily={sigma_d*100:.3f}%  CVaR={cvar_frac*100:.3f}%"
                 f"  Kelly={kelly_f*100:.1f}%"
                 f"  {'[GARCH]' if garch_ok else '[EWMA]'}"
                 f"  {'[EVT]' if evt_ok else '[Hist]'}")

        # 9. Regime
        reg_idx, reg_conf, reg_label = RegimeDetector.detect(log_ret)

        # 10. Position size
        pos_usd = RiskEngine.position_size_usd(
            CFG.capital_usd, kelly_f, cvar_1d, reg_idx, reg_conf)

        # 11. Trade levels
        levels = compute_trade_levels(S0, feats, action, sigma_d)

        # 12. Monte Carlo
        LOG.info(f"🎲  Running {CFG.n_simulations:,} MC paths…")
        mc_paths = MonteCarlo.run(S0, mu, sigma_d, log_ret)
        mc_prob_up = float((mc_paths[21] > S0).mean())

        # 13. Walk-forward
        LOG.info("📐  Walk-forward validation…")
        wf = WalkForward.run(xau)

        # 14. Reasons
        reasons = build_reasons(feats, dirs, reg_label, reg_conf, mc_prob_up)

        # 15. Accuracy tracker
        acc = engine.accuracy_summary()

        # 16. Record today's prediction (for tomorrow's accuracy update)
        engine.record_prediction(S0, prob, action, dirs)

        # 17. Display
        print_signal_output(
            feats=feats, prob=prob, action=action,
            regime_idx=reg_idx, regime_conf=reg_conf, regime_label=reg_label,
            sigma_d=sigma_d, cvar_1d=cvar_1d, kelly_f=kelly_f,
            pos_usd=pos_usd, levels=levels, mc_paths=mc_paths,
            perf=perf, wf=wf, dirs=dirs, sentiment=sentiment,
            reasons=reasons, acc_summary=acc,
        )

        # 18. Telegram
        side_emoji = ("🟢" if "BUY" in action else
                      "🔴" if "SELL" in action else "⚪")
        msg = (
            f"🏅 *XAUUSD Gold Quant v8*\n"
            f"{side_emoji} *{action}*  (p={prob:.2f})\n\n"
            f"Price: *${S0:,.2f}*\n"
            f"Regime: {reg_label}\n"
        )
        if levels["side"] != "FLAT":
            msg += (
                f"Entry: ${levels['entry']:,.2f}\n"
                f"SL:    ${levels['stop_loss']:,.2f}\n"
                f"TP:    ${levels['take_profit']:,.2f}\n"
                f"R:R   1:{levels['rr_ratio']}\n"
            )
        msg += "\n" + "\n".join(f"• {r}" for r in reasons[:6])
        send_telegram(msg)

    except Exception as e:
        traceback.print_exc()
        send_telegram(f"❌ GoldBot v8 error: {e}")

# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gold Quant v8 — XAUUSD Signal Bot")
    parser.add_argument("--once",    action="store_true",
                        help="Run once and exit (for cron / Docker)")
    parser.add_argument("--capital", type=float, default=CFG.capital_usd,
                        help=f"Capital in USD (default {CFG.capital_usd:,.0f})")
    parser.add_argument("--time",    type=str, default=CFG.run_time,
                        help=f"Daily run time HH:MM (default {CFG.run_time})")
    args = parser.parse_args()

    CFG.capital_usd = args.capital
    CFG.run_time    = args.time

    # Run immediately
    job()

    if args.once:
        sys.exit(0)

    # Then schedule daily
    def _scheduled():
        if datetime.now().weekday() < 5:  # Mon–Fri
            job()
        else:
            LOG.info("📅  Weekend — markets closed.")

    schedule.every().day.at(CFG.run_time).do(_scheduled)
    LOG.info(f"\n⏰  Scheduled for {CFG.run_time} daily (Mon–Fri).")
    LOG.info("   Press Ctrl+C to stop.\n")

    while not _SHUTDOWN:
        schedule.run_pending()
        time.sleep(30)

    LOG.info("👋  Gold Quant v8 shut down cleanly.")
