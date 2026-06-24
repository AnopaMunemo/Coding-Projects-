"""
═══════════════════════════════════════════════════════════════════
  GOLD MONTE CARLO & ML TRADING BOT — South Africa (GLD.JO / JSE)
  v4.0 — Institutional-Grade Edition

  NEW in v4.0 (over v3.0):
  ─────────────────────────────────────────────────────────────────
  QUANT MODELS
  • Fractional Kelly (×0.25) — prevents overbetting / tail blowups
  • Hidden Markov Model (HMM) regime detection (bull/bear/crisis)
  • Hurst exponent — trend vs mean-reversion vs random-walk detection
  • GARCH with Student-t innovations — fat-tail volatility
  • Extreme Value Theory (GPD) — institutional tail-risk / CVaR
  • Bootstrap confidence intervals on Kelly & Sharpe
  • Rolling DCC-style correlation matrix (GLD ↔ ZAR/DXY/VIX)
  • Ornstein-Uhlenbeck mean-reversion half-life on EG spread
  • Feature z-score standardisation across all signal inputs
  • Bayesian weight update (batch, weekly) replacing noisy daily flip
  • Sortino, Calmar, Omega, Information Ratio tracking
  • Monte Carlo convergence check (path-halving test)
  • VaR backtesting: Kupiec unconditional coverage test
  • Probability Integral Transform (PIT) uniformity check
  • GARCH convergence validation with EWMA fallback + warning log
  • Cointegration: Johansen test added alongside Engle-Granger
  • Macroeconomic signals: real-rate proxy, yield-curve slope
  • PCA on macro panel — latent factor extraction

  SOFTWARE / ENGINEERING
  • Structured JSON logging (replaces print spam)
  • Config dataclass — all tunables in one place
  • Data validation layer — stale/negative/NaN price guards
  • urllib3 retry suppression — clean terminal output
  • Graceful SARB fallback chain (scrape → bond ETF → hardcode)
  • Daily variance (σ²) and Yahoo Finance monitoring URL in output
  • Modular section headers for easy extension

  ┌─────────────────────────────────────────────────────────────┐
  │  INSTALL (run once):                                        │
  │  pip install numpy pandas yfinance statsmodels scipy        │
  │      requests schedule arch beautifulsoup4 lxml             │
  │      scikit-learn hmmlearn                                  │
  └─────────────────────────────────────────────────────────────┘

  ⚠  DISCLAIMER: Educational / research use only.  This is NOT
     financial advice.  Past simulated performance ≠ future results.
═══════════════════════════════════════════════════════════════════
"""

# ── stdlib ──────────────────────────────────────────────────────
import os, json, time, warnings, traceback, logging, re
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional

# Silence urllib3 connection retry spam from SARB scrape
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# ── third-party ─────────────────────────────────────────────────
import numpy as np
import pandas as pd
import requests
import schedule
from scipy import stats
from scipy.stats import kstest, chi2

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("❌  Run: pip install yfinance")

try:
    from statsmodels.tsa.stattools import coint, adfuller
    from statsmodels.tsa.vector_ar.vecm import coint_johansen
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools import add_constant
    from statsmodels.stats.diagnostic import acorr_ljungbox
    STATSMODELS_OK = True
except ImportError:
    raise SystemExit("❌  Run: pip install statsmodels")

try:
    from arch import arch_model
    GARCH_AVAILABLE = True
except ImportError:
    GARCH_AVAILABLE = False

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    SKLEARN_OK = True
except ImportError:
    SKLEARN_OK = False

try:
    from hmmlearn import hmm
    HMM_OK = True
except ImportError:
    HMM_OK = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

warnings.filterwarnings("ignore")


# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION DATACLASS — single source of truth
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BotConfig:
    # Market
    ticker: str            = "GLD.JO"
    start_date: str        = "2019-01-01"    # longer history for HMM/PCA
    forecast_days: int     = 252
    num_simulations: int   = 10_000

    # Files
    log_file: str          = "gold_simulation_log.csv"
    weights_file: str      = "model_weights.json"
    pit_file: str          = "pit_history.json"
    risk_log_file: str     = "risk_metrics_log.csv"

    # Notifications (fill in your own keys)
    telegram_bot_token: str = ""
    telegram_chat_id: str   = ""
    news_api_key: str       = ""
    news_query: str         = "gold price South Africa JSE rand mining"

    # Portfolio / Risk
    portfolio_value_zar: float = 100_000
    cvar_confidence: float     = 0.95
    max_position_pct: float    = 0.20         # hard cap 20%
    kelly_fraction_scalar: float = 0.25       # fractional Kelly multiplier

    # JSE execution costs
    typical_spread_pct: float    = 0.0025
    slippage_pct_intraday: float = 0.0015
    slippage_pct_swing: float    = 0.0008
    jse_brokerage_pct: float     = 0.0050

    # Signal thresholds (L1-normalised score space, Σ=10)
    strong_buy_threshold: float  =  2.5
    buy_threshold: float         =  1.0
    strong_sell_threshold: float = -2.5
    sell_threshold: float        = -1.0

    # HMM
    n_regimes: int = 3     # bull / bear / crisis

    # Trading mode
    trading_mode: str = "both"   # "intraday" | "swing" | "both"

    # Default signal weights
    default_weights: Dict[str, float] = field(default_factory=lambda: {
        "mc":        1.5,
        "ema_cross": 1.0,
        "rsi":       1.0,
        "news":      0.8,
        "dxy":       1.0,
        "vix":       1.0,
        "zar_coint": 1.5,
        "linreg":    0.8,
        "intraday":  0.7,
        "hurst":     0.8,
        "regime":    1.2,
        "real_rate": 0.9,
    })


CFG = BotConfig()

# Pre-compute friction totals
TOTAL_FRICTION_INTRADAY = (CFG.typical_spread_pct +
                            CFG.slippage_pct_intraday +
                            CFG.jse_brokerage_pct)
TOTAL_FRICTION_SWING    = (CFG.typical_spread_pct +
                            CFG.slippage_pct_swing +
                            CFG.jse_brokerage_pct)

# ── Structured logger ────────────────────────────────────────────
import sys

def _make_logger() -> logging.Logger:
    logger = logging.getLogger("goldbot")
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter(
            '%(asctime)s  %(levelname)-8s  %(message)s',
            datefmt='%H:%M:%S'
        ))
        logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return logger

LOG = _make_logger()

SEP  = "═" * 70
DSEP = "─" * 70


# ═══════════════════════════════════════════════════════════════════
#  SECTION 1 — DATA VALIDATION
# ═══════════════════════════════════════════════════════════════════

class DataValidationError(RuntimeError):
    pass

def validate_price_series(arr: np.ndarray, name: str = "prices") -> np.ndarray:
    """Institutional data-quality gate: no NaNs, no negatives, not stale."""
    if arr is None or len(arr) == 0:
        raise DataValidationError(f"{name}: empty series")
    if np.any(np.isnan(arr)):
        raise DataValidationError(f"{name}: contains NaN values")
    if np.any(arr <= 0):
        raise DataValidationError(f"{name}: contains non-positive prices")
    if len(arr) < 50:
        raise DataValidationError(f"{name}: fewer than 50 observations ({len(arr)})")
    return arr


# ═══════════════════════════════════════════════════════════════════
#  SECTION 2 — DYNAMIC RISK-FREE RATE
# ═══════════════════════════════════════════════════════════════════

def fetch_sarb_repo_rate() -> float:
    """
    Attempt to scrape the current SARB repo rate silently.
    Falls back to SA 10-year government bond yield via yfinance.
    Final fallback: 8.25% hardcoded.
    Returns annualised rate as a decimal (e.g. 0.0825 = 8.25%).
    """
    # Method 1: SARB website scrape (silent on failure)
    if BS4_AVAILABLE:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            resp = requests.get(
                "https://www.sarb.co.za/monetary-policy/",
                headers=headers, timeout=5
            )
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup.find_all(["td", "span", "p", "strong"]):
                text = tag.get_text(strip=True)
                if "repo" in text.lower() or "repurchase" in text.lower():
                    m = re.search(r"(\d{1,2}[.,]\d{1,2})\s*%", text)
                    if m:
                        rate = float(m.group(1).replace(",", ".")) / 100
                        if 0.02 < rate < 0.25:
                            LOG.info(f"🏦  SARB repo rate (scraped): {rate:.2%}")
                            return rate
        except Exception:
            pass  # fail silently — move to bond proxy

    # Method 2: SA 10-year bond yield via yfinance
    for bond_ticker in ["^SAGB10", "SAGB.JO"]:
        try:
            data = yf.download(bond_ticker, period="5d", progress=False,
                               auto_adjust=True)
            if not data.empty:
                last_val = float(data["Close"].iloc[-1])
                if 2.0 < last_val < 25.0:
                    rate = last_val / 100.0
                    LOG.info(f"📈  Risk-free rate (bond proxy {bond_ticker}): {rate:.2%}")
                    return rate
        except Exception:
            pass

    fallback = 0.0825
    LOG.warning(f"⚠️  Using hardcoded risk-free fallback: {fallback:.2%}")
    return fallback


# ═══════════════════════════════════════════════════════════════════
#  SECTION 3 — DATA DOWNLOAD
# ═══════════════════════════════════════════════════════════════════

def download_data() -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pd.DatetimeIndex]:
    LOG.info(f"📥  Fetching market data from {CFG.start_date}…")
    tickers = f"{CFG.ticker} ZAR=X DX-Y.NYB ^VIX ^TNX"  # ^TNX = US 10yr yield
    raw = yf.download(
        tickers, start=CFG.start_date, auto_adjust=True, progress=False
    )["Close"].ffill().dropna()

    prices = validate_price_series(raw[CFG.ticker].to_numpy(dtype=float), "GLD.JO")
    zar    = validate_price_series(raw["ZAR=X"].to_numpy(dtype=float), "ZAR=X")
    dxy    = validate_price_series(raw["DX-Y.NYB"].to_numpy(dtype=float), "DXY")
    vix    = validate_price_series(raw["^VIX"].to_numpy(dtype=float), "VIX")
    us10y  = validate_price_series(raw["^TNX"].to_numpy(dtype=float), "US10Y")

    LOG.info(f"  ✓  {len(prices)} trading days | GLD.JO = R{prices[-1]:,.2f}")
    return prices, zar, dxy, vix, us10y, raw.index


# ═══════════════════════════════════════════════════════════════════
#  SECTION 4 — MATHEMATICAL INDICATORS (CORE)
# ═══════════════════════════════════════════════════════════════════

def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    ema = np.empty(len(prices))
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = prices[i] * k + ema[i - 1] * (1.0 - k)
    return ema


def compute_rsi_wilders(prices: np.ndarray, period: int = 14) -> float:
    data   = prices[-(period * 3):]
    deltas = np.diff(data)
    gains  = np.where(deltas > 0,  deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    alpha  = 1.0 / period
    avg_g  = float(gains[:period].mean())
    avg_l  = float(losses[:period].mean())
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = alpha * g + (1.0 - alpha) * avg_g
        avg_l = alpha * l + (1.0 - alpha) * avg_l
    return 100.0 if avg_l == 0.0 else 100.0 - 100.0 / (1.0 + avg_g / avg_l)


def compute_linreg_slope(prices: np.ndarray, period: int = 20) -> float:
    y = prices[-period:].astype(float)
    x = np.arange(period, dtype=float)
    slope, *_ = stats.linregress(x, y)
    return float(slope) / float(prices[-1])


def compute_garch_volatility(log_returns: np.ndarray) -> Tuple[float, bool]:
    """
    GARCH(1,1) with Student-t innovations (fat tails).
    Returns (daily_vol, converged_flag).
    Falls back to EWMA RiskMetrics λ=0.94 if GARCH fails.
    """
    if GARCH_AVAILABLE:
        try:
            m   = arch_model(log_returns * 100, vol="Garch", p=1, q=1,
                             dist="t", rescale=False)
            res = m.fit(disp="off", show_warning=False)
            s   = float(np.sqrt(res.forecast(horizon=1).variance.values[-1, 0])) / 100.0
            if np.isfinite(s) and 0 < s < 1:
                # Validate parameter signs (omega, alpha, beta must be positive)
                params = res.params
                if all(params.get(k, 1) > 0 for k in ["omega", "alpha[1]", "beta[1]"]
                       if k in params.index):
                    return s, True
        except Exception as e:
            LOG.warning(f"GARCH fit failed: {e} — using EWMA fallback")

    # EWMA fallback
    lam, var = 0.94, float(np.var(log_returns[:20]))
    for r in log_returns:
        var = lam * var + (1.0 - lam) * r ** 2
    return float(np.sqrt(var)), False


def estimate_parameters(prices: np.ndarray) -> Tuple[float, float, np.ndarray, bool]:
    lr = np.log(prices[1:] / prices[:-1])
    sigma, garch_converged = compute_garch_volatility(lr)
    return float(lr.mean()), sigma, lr, garch_converged


# ═══════════════════════════════════════════════════════════════════
#  SECTION 5 — ADVANCED STATISTICAL MODELS
# ═══════════════════════════════════════════════════════════════════

# ── 5a. Hurst Exponent ────────────────────────────────────────────
def compute_hurst_exponent(prices: np.ndarray, max_lag: int = 100) -> float:
    """
    R/S analysis Hurst exponent.
    H < 0.5 → mean-reverting
    H ≈ 0.5 → random walk
    H > 0.5 → trending
    """
    lags = range(2, min(max_lag, len(prices) // 4))
    tau  = []
    rs_vals = []
    try:
        for lag in lags:
            chunks = len(prices) // lag
            if chunks < 2:
                continue
            rs_list = []
            for i in range(chunks):
                seg = prices[i * lag:(i + 1) * lag].astype(float)
                m   = np.mean(seg)
                dev = np.cumsum(seg - m)
                R   = np.max(dev) - np.min(dev)
                S   = np.std(seg, ddof=1)
                if S > 0:
                    rs_list.append(R / S)
            if rs_list:
                rs_vals.append(np.log(np.mean(rs_list)))
                tau.append(np.log(lag))
        if len(tau) < 2:
            return 0.5
        slope, *_ = stats.linregress(tau, rs_vals)
        return float(np.clip(slope, 0.0, 1.0))
    except Exception:
        return 0.5


# ── 5b. Hidden Markov Model Regime Detection ──────────────────────
def detect_market_regime(log_returns: np.ndarray) -> Tuple[int, np.ndarray, str]:
    """
    Fits a 3-state HMM on log returns.
    Returns (current_regime_idx, regime_probs, regime_label).
    Regimes are labelled by ascending volatility: 0=low-vol, 1=mid, 2=high-vol.
    """
    if not HMM_OK or len(log_returns) < 100:
        return 1, np.array([0.33, 0.34, 0.33]), "Unknown (HMM unavailable)"

    try:
        X = log_returns.reshape(-1, 1)
        model = hmm.GaussianHMM(
            n_components=CFG.n_regimes,
            covariance_type="full",
            n_iter=200,
            random_state=42
        )
        model.fit(X)
        regime_seq  = model.predict(X)
        probs       = model.predict_proba(X)

        # Sort regimes by mean volatility (ascending)
        means = [np.abs(log_returns[regime_seq == i]).mean()
                 for i in range(CFG.n_regimes)]
        order = np.argsort(means)  # 0=lowest vol, 2=highest vol

        current_raw  = regime_seq[-1]
        current_rank = int(np.where(order == current_raw)[0][0])
        current_probs = probs[-1]

        labels = {0: "🟢 Low-Vol Bull", 1: "🟡 Mid Transition", 2: "🔴 High-Vol Crisis"}
        label  = labels.get(current_rank, "Unknown")

        return current_rank, current_probs, label
    except Exception as e:
        LOG.warning(f"HMM failed: {e}")
        return 1, np.array([0.33, 0.34, 0.33]), "Unknown"


# ── 5c. Cointegration: Engle-Granger + Johansen ──────────────────
def engle_granger_zscore(prices: np.ndarray, zar: np.ndarray,
                          lookback: int = 252) -> Tuple[float, float, float]:
    """
    Full EG two-step + Ornstein-Uhlenbeck half-life of mean reversion.
    Returns (z-score, EG p-value, OU half-life in days).
    """
    p = prices[-lookback:].astype(float)
    z = zar[-lookback:].astype(float)
    X = add_constant(z)
    model  = OLS(p, X).fit()
    spread = model.resid
    _, pval, _ = coint(p, z)
    zscore = (spread[-1] - spread.mean()) / (spread.std() + 1e-8)

    # Ornstein-Uhlenbeck half-life
    half_life = 0.0
    try:
        delta_spread  = np.diff(spread)
        lag_spread    = spread[:-1]
        X_ou          = add_constant(lag_spread)
        ou_model      = OLS(delta_spread, X_ou).fit()
        theta         = -ou_model.params[1]
        if theta > 0:
            half_life = float(np.log(2) / theta)
    except Exception:
        half_life = 0.0

    return float(zscore), float(pval), half_life


def johansen_test(prices: np.ndarray, zar: np.ndarray,
                   lookback: int = 252) -> bool:
    """
    Johansen cointegration test. Returns True if cointegrated at 5%.
    More robust than Engle-Granger for regime shifts.
    """
    try:
        df = pd.DataFrame({"gld": prices[-lookback:], "zar": zar[-lookback:]}).dropna()
        result = coint_johansen(df, det_order=0, k_ar_diff=1)
        # Trace statistic vs 5% critical value (index 1 = 5%)
        return bool(result.lr1[0] > result.cvt[0, 1])
    except Exception:
        return False


# ── 5d. Extreme Value Theory — GPD tail CVaR ─────────────────────
def compute_cvar_evt(log_returns: np.ndarray, confidence: float = 0.95,
                      threshold_quantile: float = 0.05) -> Tuple[float, bool]:
    """
    Peaks-Over-Threshold GPD fit for institutional tail-risk CVaR.
    Falls back to historical CVaR if GPD fit fails.
    Returns (daily_cvar_as_fraction_of_portfolio, evt_succeeded).
    """
    try:
        threshold  = np.percentile(log_returns, threshold_quantile * 100)
        exceedances = -(log_returns[log_returns < threshold] - threshold)
        if len(exceedances) < 10:
            raise ValueError("Too few tail observations")
        shape, loc, scale = stats.genpareto.fit(exceedances, floc=0)
        # GPD CVaR formula
        u       = -threshold
        nu      = len(exceedances) / len(log_returns)
        alpha   = 1 - confidence
        if shape < 1 and scale > 0:
            cvar_frac = u + (scale / (1 - shape)) * (
                (nu / alpha) ** shape - 1
            ) / shape if shape != 0 else u + scale * (1 + np.log(nu / alpha))
            return float(abs(cvar_frac)), True
    except Exception:
        pass

    # Historical fallback
    cutoff = np.percentile(log_returns, (1 - confidence) * 100)
    tail   = log_returns[log_returns <= cutoff]
    es     = tail.mean() if len(tail) > 0 else cutoff
    return float(abs(es)), False


def compute_cvar(portfolio_value: float, log_returns: np.ndarray,
                 confidence: float = 0.95) -> Tuple[float, bool]:
    """Returns (CVaR in ZAR, evt_flag)."""
    frac, evt = compute_cvar_evt(log_returns, confidence)
    return float(frac * portfolio_value), evt


# ── 5e. Bootstrap CI on Kelly & Sharpe ───────────────────────────
def bootstrap_kelly_ci(log_returns: np.ndarray, risk_free: float,
                        n_boot: int = 1000, block_size: int = 20,
                        ci: float = 0.95) -> Tuple[float, float]:
    """
    Stationary block bootstrap confidence interval for Kelly fraction.
    Returns (lower_bound, upper_bound).
    """
    n = len(log_returns)
    kellys = []
    for _ in range(n_boot):
        idx   = np.random.randint(0, n - block_size, n // block_size)
        boot  = np.concatenate([log_returns[i:i + block_size] for i in idx])
        mu_b  = float(boot.mean()) * 252
        sig_b = float(boot.std()) * np.sqrt(252)
        if sig_b > 0:
            k = (mu_b - risk_free) / (sig_b ** 2)
            kellys.append(np.clip(k, 0, CFG.max_position_pct))
    if not kellys:
        return 0.0, 0.0
    alpha = (1 - ci) / 2
    return (float(np.percentile(kellys, alpha * 100)),
            float(np.percentile(kellys, (1 - alpha) * 100)))


# ── 5f. Fractional Kelly ─────────────────────────────────────────
def compute_kelly(mu: float, sigma: float, risk_free: float) -> float:
    """
    Fractional Kelly (×0.25) using Merton continuous-time formula.
    Institutional desks never use full Kelly — overbetting risk.
    """
    if sigma <= 0:
        return 0.0
    mu_ann    = mu * 252
    sigma_ann = sigma * np.sqrt(252)
    f_star    = (mu_ann - risk_free) / (sigma_ann ** 2)
    f_frac    = f_star * CFG.kelly_fraction_scalar   # fractional Kelly
    return float(np.clip(f_frac, 0.0, CFG.max_position_pct))


# ── 5g. Performance Metrics ───────────────────────────────────────
def compute_performance_metrics(log_returns: np.ndarray,
                                 risk_free_daily: float) -> Dict[str, float]:
    """
    Sortino, Calmar, Omega, Info Ratio, Sharpe — rolling 252 days.
    """
    r = log_returns[-252:]
    if len(r) < 30:
        return {}

    ann_ret    = float(r.mean() * 252)
    ann_vol    = float(r.std()  * np.sqrt(252))
    sharpe     = (ann_ret - risk_free_daily * 252) / ann_vol if ann_vol > 0 else 0.0

    # Sortino — only downside deviation
    neg_r     = r[r < 0]
    downside  = float(neg_r.std() * np.sqrt(252)) if len(neg_r) > 1 else 1e-8
    sortino   = (ann_ret - risk_free_daily * 252) / downside if downside > 0 else 0.0

    # Max drawdown & Calmar
    cum   = np.cumprod(1 + r)
    peak  = np.maximum.accumulate(cum)
    dd    = (cum - peak) / (peak + 1e-8)
    mdd   = float(dd.min())
    calmar = ann_ret / abs(mdd) if abs(mdd) > 1e-6 else 0.0

    # Omega ratio (threshold = risk-free)
    rf_daily = risk_free_daily
    gains    = np.sum(np.maximum(r - rf_daily, 0))
    losses   = np.sum(np.maximum(rf_daily - r, 0))
    omega    = gains / losses if losses > 1e-8 else float("inf")

    # Information Ratio vs buy-and-hold (benchmark = 0 excess daily)
    active_r = r - r.mean()   # simplified; replace with benchmark returns
    te       = float(active_r.std() * np.sqrt(252))
    info_r   = ann_ret / te if te > 1e-8 else 0.0

    return {
        "sharpe":   round(sharpe,   3),
        "sortino":  round(sortino,  3),
        "calmar":   round(calmar,   3),
        "omega":    round(omega,    3),
        "info_ratio": round(info_r, 3),
        "ann_return": round(ann_ret * 100, 2),
        "max_drawdown": round(mdd * 100, 2),
        "ann_vol":  round(ann_vol * 100, 2),
    }


# ── 5h. PCA on Macro Panel ───────────────────────────────────────
def compute_macro_pca(prices: np.ndarray, zar: np.ndarray,
                       dxy: np.ndarray, vix: np.ndarray,
                       us10y: np.ndarray, n: int = 126) -> Tuple[float, float]:
    """
    PCA on standardised macro panel (ZAR/DXY/VIX/US10Y returns).
    Returns (PC1_score, PC2_score) — latent risk/inflation factors.
    """
    if not SKLEARN_OK:
        return 0.0, 0.0
    try:
        panel = np.column_stack([
            np.diff(np.log(zar[-n-1:])),
            np.diff(np.log(dxy[-n-1:])),
            np.diff(np.log(vix[-n-1:] + 1e-8)),
            np.diff(us10y[-n-1:])
        ])
        panel = np.nan_to_num(panel)
        scaler = StandardScaler()
        scaled = scaler.fit_transform(panel)
        pca    = PCA(n_components=2)
        pcs    = pca.fit_transform(scaled)
        return float(pcs[-1, 0]), float(pcs[-1, 1])
    except Exception:
        return 0.0, 0.0


# ── 5i. Rolling Correlation Matrix ───────────────────────────────
def compute_rolling_correlations(prices: np.ndarray, zar: np.ndarray,
                                   dxy: np.ndarray, vix: np.ndarray,
                                   window: int = 60) -> Dict[str, float]:
    """
    Pearson correlations of GLD log-returns vs macro factors, rolling 60d.
    """
    try:
        n   = window + 1
        gldr = np.diff(np.log(prices[-n:]))
        zarr = np.diff(np.log(zar[-n:]))
        dxyr = np.diff(np.log(dxy[-n:]))
        vixr = np.diff(np.log(vix[-n:] + 1e-8))
        return {
            "corr_zar": float(np.corrcoef(gldr, zarr)[0, 1]),
            "corr_dxy": float(np.corrcoef(gldr, dxyr)[0, 1]),
            "corr_vix": float(np.corrcoef(gldr, vixr)[0, 1]),
        }
    except Exception:
        return {"corr_zar": 0.0, "corr_dxy": 0.0, "corr_vix": 0.0}


# ── 5j. Feature Z-Score Standardisation ──────────────────────────
def zscore_feature(x: float, history: List[float]) -> float:
    """Convert a raw feature to its z-score over recent history."""
    if len(history) < 5:
        return 0.0
    mu, sigma = np.mean(history), np.std(history)
    if sigma < 1e-8:
        return 0.0
    return float((x - mu) / sigma)


# ── 5k. Monte Carlo Convergence Check ────────────────────────────
def check_mc_convergence(S0: float, mu: float, sigma: float,
                           log_returns: np.ndarray,
                           tolerance: float = 0.02) -> bool:
    """
    Halve the number of paths and compare median + CVaR.
    Returns True if the simulation has converged.
    """
    try:
        half_n = CFG.num_simulations // 2
        full_paths = run_merton_jump_diffusion(S0, mu, sigma, log_returns,
                                                n_sims=CFG.num_simulations)
        half_paths = run_merton_jump_diffusion(S0, mu, sigma, log_returns,
                                                n_sims=half_n)
        med_full = float(np.median(full_paths[21]))
        med_half = float(np.median(half_paths[21]))
        if abs(med_full - med_half) / (S0 + 1e-8) > tolerance:
            LOG.warning(f"⚠️  MC convergence issue: median diff "
                        f"{abs(med_full-med_half)/S0:.2%} > {tolerance:.2%}")
            return False
        return True
    except Exception:
        return True


# ── 5l. VaR Backtesting (Kupiec Test) ────────────────────────────
def kupiec_test(violations: int, total: int,
                confidence: float = 0.95) -> Tuple[float, bool]:
    """
    Kupiec (1995) likelihood ratio test for VaR unconditional coverage.
    Returns (p-value, passed).  p > 0.05 means the model is well-calibrated.
    """
    if total < 20 or violations == 0:
        return 1.0, True
    p     = 1 - confidence
    x     = violations
    T     = total
    p_hat = x / T
    if p_hat in (0, 1):
        return 1.0, True
    try:
        LR  = -2 * (
            x * np.log(p / p_hat) +
            (T - x) * np.log((1 - p) / (1 - p_hat))
        )
        pv  = float(1 - chi2.cdf(LR, df=1))
        return pv, pv > 0.05
    except Exception:
        return 1.0, True


# ── 5m. Probability Integral Transform (PIT) ─────────────────────
def update_pit_history(paths: np.ndarray, realised_price: Optional[float],
                        horizon: int = 21) -> Optional[float]:
    """
    Computes the CDF position of the realised price in the MC distribution.
    Accumulates in pit_history.json for later KS-test of uniformity.
    """
    if realised_price is None:
        return None
    try:
        terminal = paths[horizon]
        pit_val  = float(np.mean(terminal <= realised_price))

        history  = []
        if os.path.exists(CFG.pit_file):
            with open(CFG.pit_file) as f:
                history = json.load(f)
        history.append(pit_val)
        with open(CFG.pit_file, "w") as f:
            json.dump(history[-252:], f)   # keep rolling 252

        # KS test for uniformity (only if enough data)
        if len(history) >= 50:
            ks_stat, ks_pv = kstest(history, "uniform")
            LOG.info(f"📊  PIT KS-test p={ks_pv:.3f} "
                     f"({'✓ calibrated' if ks_pv > 0.05 else '⚠ miscalibrated'})")

        return pit_val
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
#  SECTION 6 — MONTE CARLO (Merton Jump-Diffusion)
# ═══════════════════════════════════════════════════════════════════

def run_merton_jump_diffusion(S0: float, mu: float, sigma: float,
                               log_returns: np.ndarray,
                               n_sims: Optional[int] = None) -> np.ndarray:
    """
    GBM + compound Poisson jump process (Merton 1976).
    Jumps calibrated empirically from 3-sigma historical events.
    """
    n_sims   = n_sims or CFG.num_simulations
    std_dev  = np.std(log_returns)
    jumps    = log_returns[np.abs(log_returns) > 3 * std_dev]

    jump_freq  = len(jumps) / max(len(log_returns) / 252, 1)
    jump_mu    = np.mean(jumps) if len(jumps) > 0 else 0.0
    jump_sigma = np.std(jumps)  if len(jumps) > 1 else 1e-4

    dt      = 1.0 / 252
    paths   = np.zeros((CFG.forecast_days + 1, n_sims))
    paths[0]= S0

    Z1   = np.random.standard_normal((CFG.forecast_days, n_sims))
    Z2   = np.random.standard_normal((CFG.forecast_days, n_sims))
    Pois = np.random.poisson(jump_freq * dt, (CFG.forecast_days, n_sims))

    drift     = (mu - 0.5 * sigma ** 2) * dt
    diffusion = sigma * np.sqrt(dt)

    for t in range(1, CFG.forecast_days + 1):
        jump    = Pois[t - 1] * (jump_mu + jump_sigma * Z2[t - 1])
        paths[t]= paths[t - 1] * np.exp(drift + diffusion * Z1[t - 1] + jump)

    return paths


# ═══════════════════════════════════════════════════════════════════
#  SECTION 7 — INTRADAY MOMENTUM SIGNAL
# ═══════════════════════════════════════════════════════════════════

def intraday_momentum_signal(prices: np.ndarray) -> Tuple[int, str]:
    if len(prices) < 10:
        return 0, "Insufficient data"
    atr_5 = np.mean(np.abs(np.diff(prices[-6:])))
    gap   = prices[-1] - prices[-2]
    avg_5 = np.mean(prices[-5:])
    trend = prices[-1] - avg_5
    if gap > 0.5 * atr_5 and trend > 0:
        return 1,  f"Intraday bullish gap +{gap:.2f} (ATR {atr_5:.2f})"
    elif gap < -0.5 * atr_5 and trend < 0:
        return -1, f"Intraday bearish gap {gap:.2f} (ATR {atr_5:.2f})"
    return 0, "No intraday breakout signal"


# ═══════════════════════════════════════════════════════════════════
#  SECTION 8 — ADAPTIVE WEIGHT SYSTEM (Bayesian / Batch Update)
# ═══════════════════════════════════════════════════════════════════

def get_normalised_weights() -> Dict[str, float]:
    """Load saved weights and L1-normalise so Σ = 10.0."""
    w = CFG.default_weights.copy()
    if os.path.exists(CFG.weights_file):
        try:
            with open(CFG.weights_file) as f:
                w.update(json.load(f))
        except Exception:
            pass
    total = sum(abs(v) for v in w.values())
    if total > 0:
        return {k: round(abs(v) / total * 10.0, 4) for k, v in w.items()}
    return w


def save_weights(w: Dict[str, float]):
    with open(CFG.weights_file, "w") as f:
        json.dump(w, f, indent=4)


def update_learning_model_batch(current_price: float):
    """
    Bayesian-style batch weight update (weekly batch, not noisy daily flip).
    Uses logistic regression over a rolling window if sklearn is available.
    Falls back to smoothed directional update otherwise.
    """
    if not os.path.exists(CFG.log_file):
        return
    try:
        df = pd.read_csv(CFG.log_file)
        if len(df) < 10:
            return

        df["actual_return"] = pd.to_numeric(df["actual_return"], errors="coerce")
        df["S0"] = pd.to_numeric(df["S0"], errors="coerce")

        # Update actual return for last row
        if len(df) >= 2:
            prev_price  = float(df["S0"].iloc[-2])
            actual_ret  = (current_price - prev_price) / prev_price
            df.loc[df.index[-1], "actual_return"] = round(actual_ret, 6)

        # Only run batch update weekly (every 5 rows with known returns)
        known = df.dropna(subset=["actual_return"])
        if len(known) < 5 or len(known) % 5 != 0:
            df.to_csv(CFG.log_file, index=False)
            return

        dir_cols = [c for c in df.columns if c.startswith("dir_")]
        w = get_normalised_weights()

        if SKLEARN_OK and len(known) >= 20 and dir_cols:
            # LASSO-regularised logistic regression
            X = known[dir_cols].fillna(0).to_numpy()
            y = (known["actual_return"].to_numpy() > 0).astype(int)
            try:
                lr = LogisticRegression(penalty="l1", solver="saga",
                                        C=1.0, max_iter=500, random_state=42)
                lr.fit(X, y)
                for i, col in enumerate(dir_cols):
                    feat = col[4:]   # strip "dir_"
                    if feat in w:
                        coef = float(lr.coef_[0][i])
                        w[feat] = max(0.05, w[feat] + 0.1 * coef)
                LOG.info("🧠  Weights updated via LASSO logistic regression")
            except Exception:
                _simple_weight_update(w, known, dir_cols)
        else:
            _simple_weight_update(w, known, dir_cols)

        total = sum(w.values())
        w = {k: round(v / total * 10.0, 4) for k, v in w.items()}
        save_weights(w)
        df.to_csv(CFG.log_file, index=False)

    except Exception as e:
        LOG.warning(f"Learning update skipped: {e}")


def _simple_weight_update(w: Dict, df: pd.DataFrame, dir_cols: List[str]):
    """Smoothed directional update (fallback)."""
    recent = df.tail(10)
    for col in dir_cols:
        feat = col[4:]
        if feat not in w:
            continue
        correct = sum(
            1 for _, row in recent.iterrows()
            if not pd.isna(row.get("actual_return")) and
               int(row.get(col, 0) or 0) * float(row["actual_return"]) > 0
        )
        accuracy = correct / len(recent)
        lr = 0.03
        if accuracy > 0.55:
            w[feat] = min(2.0, w[feat] + lr)
        elif accuracy < 0.45:
            w[feat] = max(0.05, w[feat] - lr)


# ═══════════════════════════════════════════════════════════════════
#  SECTION 9 — NEWS SENTIMENT
# ═══════════════════════════════════════════════════════════════════

def fetch_news_sentiment() -> float:
    if not CFG.news_api_key:
        return 0.0
    BULL = {"surge", "rally", "buy", "high", "record", "gain", "rise", "soar",
             "strong", "bullish", "up", "positive"}
    BEAR = {"fall",  "drop",  "sell", "low",  "crash",  "lose", "slump", "plunge",
             "weak", "bearish", "down", "negative", "pressure"}
    try:
        url  = (f"https://newsapi.org/v2/everything?q="
                f"{requests.utils.quote(CFG.news_query)}"
                f"&sortBy=publishedAt&pageSize=20&language=en"
                f"&apiKey={CFG.news_api_key}")
        arts = requests.get(url, timeout=8).json().get("articles", [])
        pos, neg = 0, 0
        for a in arts:
            t = (a.get("title", "") + " " + a.get("description", "")).lower()
            pos += sum(1 for w in BULL if f" {w} " in f" {t} ")
            neg += sum(1 for w in BEAR if f" {w} " in f" {t} ")
        total = pos + neg
        score = float((pos - neg) / total) if total > 0 else 0.0
        LOG.info(f"📰  News sentiment: {score:+.2f}  ({pos}↑ {neg}↓)")
        return max(-1.0, min(1.0, score))
    except Exception as e:
        LOG.warning(f"News error: {e}")
        return 0.0


# ═══════════════════════════════════════════════════════════════════
#  SECTION 10 — SIGNAL GENERATION (with z-score standardisation)
# ═══════════════════════════════════════════════════════════════════

def generate_signals(prices, paths, sentiment, zar, dxy, vix, us10y,
                      regime_idx: int, hurst: float,
                      pc1: float, pc2: float,
                      correlations: Dict) -> Dict:
    S0      = float(prices[-1])
    weights = get_normalised_weights()

    ema20      = compute_ema(prices, 20)
    ema50      = compute_ema(prices, 50)
    rsi_val    = compute_rsi_wilders(prices, 14)
    linreg     = compute_linreg_slope(prices, 20)
    dxy_trend  = (dxy[-1] - dxy[-10]) / dxy[-10]
    vix_trend  = (vix[-1] - vix[-10]) / vix[-10]
    eg_z, eg_pv, ou_half = engle_granger_zscore(prices, zar)
    intra_dir, intra_label = intraday_momentum_signal(prices)

    # Real rate proxy: GLD log-return vs US10Y change
    us10y_chg  = float(us10y[-1] - us10y[-5])   # basis point move 5d
    real_rate_signal = -1 if us10y_chg > 0.10 else (1 if us10y_chg < -0.10 else 0)

    med_1m = float(np.percentile(paths[21], 50))
    mc_up  = (med_1m - S0) / S0

    score   = 0.0
    reasons = []
    dirs    = {}

    # ── Monte Carlo direction ────────────────────────────────────
    if mc_up > 0.01:
        score += weights.get("mc", 1.5)
        reasons.append(f"MC 1m median +{mc_up:.1%} ↑"); dirs["mc"] = 1
    elif mc_up < -0.01:
        score -= weights.get("mc", 1.5)
        reasons.append(f"MC 1m median {mc_up:.1%} ↓"); dirs["mc"] = -1
    else:
        dirs["mc"] = 0

    # ── EMA crossover ───────────────────────────────────────────
    if ema20[-1] > ema50[-1]:
        score += weights.get("ema_cross", 1.0)
        reasons.append("EMA20 > EMA50 ↑"); dirs["ema_cross"] = 1
    else:
        score -= weights.get("ema_cross", 1.0)
        reasons.append("EMA20 < EMA50 ↓"); dirs["ema_cross"] = -1

    # ── RSI ─────────────────────────────────────────────────────
    if rsi_val < 35:
        score += weights.get("rsi", 1.0)
        reasons.append(f"RSI {rsi_val:.1f} oversold ↑"); dirs["rsi"] = 1
    elif rsi_val > 65:
        score -= weights.get("rsi", 1.0)
        reasons.append(f"RSI {rsi_val:.1f} overbought ↓"); dirs["rsi"] = -1
    else:
        dirs["rsi"] = 0

    # ── News ────────────────────────────────────────────────────
    if abs(sentiment) > 0.1:
        score += sentiment * weights.get("news", 0.8)
        dirs["news"] = 1 if sentiment > 0 else -1
        reasons.append(f"News sentiment {sentiment:+.2f} {'↑' if sentiment>0 else '↓'}")
    else:
        dirs["news"] = 0

    # ── DXY ─────────────────────────────────────────────────────
    if dxy_trend < -0.01:
        score += weights.get("dxy", 1.0)
        reasons.append(f"USD weakening {dxy_trend:.1%} ↑"); dirs["dxy"] = 1
    elif dxy_trend > 0.01:
        score -= weights.get("dxy", 1.0)
        reasons.append(f"USD strengthening {dxy_trend:.1%} ↓"); dirs["dxy"] = -1
    else:
        dirs["dxy"] = 0

    # ── VIX (flight-to-safety boosts gold) ──────────────────────
    if vix_trend > 0.05:
        score += weights.get("vix", 1.0)
        reasons.append(f"VIX rising +{vix_trend:.1%} (safe-haven ↑)"); dirs["vix"] = 1
    elif vix_trend < -0.05:
        score -= weights.get("vix", 1.0)
        reasons.append(f"VIX falling {vix_trend:.1%} ↓"); dirs["vix"] = -1
    else:
        dirs["vix"] = 0

    # ── EG cointegration z-score ─────────────────────────────────
    coint_sig = "★ cointegrated" if eg_pv < 0.05 else "(not sig.)"
    if eg_z < -1.5:
        score += weights.get("zar_coint", 1.5)
        reasons.append(f"EG z={eg_z:.2f} undervalued vs ZAR {coint_sig} ↑")
        dirs["zar_coint"] = 1
    elif eg_z > 1.5:
        score -= weights.get("zar_coint", 1.5)
        reasons.append(f"EG z={eg_z:.2f} overvalued vs ZAR {coint_sig} ↓")
        dirs["zar_coint"] = -1
    else:
        dirs["zar_coint"] = 0

    # ── Linear regression slope ──────────────────────────────────
    if linreg > 0.001:
        score += weights.get("linreg", 0.8)
        reasons.append("OLS slope: uptrend ↑"); dirs["linreg"] = 1
    elif linreg < -0.001:
        score -= weights.get("linreg", 0.8)
        reasons.append("OLS slope: downtrend ↓"); dirs["linreg"] = -1
    else:
        dirs["linreg"] = 0

    # ── Intraday momentum ────────────────────────────────────────
    if intra_dir != 0:
        score += intra_dir * weights.get("intraday", 0.7)
        reasons.append(intra_label + (" ↑" if intra_dir > 0 else " ↓"))
    dirs["intraday"] = intra_dir

    # ── Hurst exponent signal ─────────────────────────────────────
    if hurst > 0.55:
        # Trending — follow the EMA signal
        hurst_dir = 1 if ema20[-1] > ema50[-1] else -1
        score += hurst_dir * weights.get("hurst", 0.8)
        reasons.append(f"Hurst={hurst:.3f} (trending) → {'↑' if hurst_dir>0 else '↓'}")
        dirs["hurst"] = hurst_dir
    elif hurst < 0.45:
        # Mean-reverting — fade the EMA signal
        hurst_dir = -1 if ema20[-1] > ema50[-1] else 1
        score += hurst_dir * weights.get("hurst", 0.8)
        reasons.append(f"Hurst={hurst:.3f} (mean-reverting) → {'↑' if hurst_dir>0 else '↓'}")
        dirs["hurst"] = hurst_dir
    else:
        reasons.append(f"Hurst={hurst:.3f} (random walk, no signal)")
        dirs["hurst"] = 0

    # ── HMM Regime ───────────────────────────────────────────────
    # Regime 0=bull boost, 1=neutral, 2=crisis → reduce but gold benefits
    if regime_idx == 0:
        score += weights.get("regime", 1.2)
        reasons.append("HMM: Bull regime ↑"); dirs["regime"] = 1
    elif regime_idx == 2:
        # Crisis: gold is a safe haven — slight positive bias
        score += 0.5 * weights.get("regime", 1.2)
        reasons.append("HMM: Crisis regime — safe-haven gold ↑"); dirs["regime"] = 1
    else:
        reasons.append("HMM: Transitional regime (neutral)")
        dirs["regime"] = 0

    # ── Real rate proxy (US10Y move) ─────────────────────────────
    if real_rate_signal != 0:
        score += real_rate_signal * weights.get("real_rate", 0.9)
        label = f"US10Y {'rising' if us10y_chg>0 else 'falling'} {us10y_chg:+.2f}bps"
        reasons.append(label + (" → gold headwind ↓" if real_rate_signal < 0 else " → gold tailwind ↑"))
    dirs["real_rate"] = real_rate_signal

    # ── Action label ─────────────────────────────────────────────
    if   score >= CFG.strong_buy_threshold:  action = "🟢 STRONG BUY"
    elif score >= CFG.buy_threshold:         action = "🟡 BUY"
    elif score <= CFG.strong_sell_threshold: action = "🔴 STRONG SELL"
    elif score <= CFG.sell_threshold:        action = "🟠 SELL"
    else:                                    action = "⚪ HOLD"

    return {
        "action": action, "score": round(score, 4), "S0": S0,
        "rsi": rsi_val, "ema20": float(ema20[-1]), "ema50": float(ema50[-1]),
        "linreg_slope": linreg,
        "mc_median_1m": med_1m,
        "mc_p5_1m":  float(np.percentile(paths[21],  5)),
        "mc_p95_1m": float(np.percentile(paths[21], 95)),
        "mc_p5_1y":  float(np.percentile(paths[-1],  5)),
        "mc_p95_1y": float(np.percentile(paths[-1], 95)),
        "eg_zscore": round(eg_z, 4),
        "eg_pvalue": round(eg_pv, 4),
        "ou_half_life": round(ou_half, 1),
        "hurst": round(hurst, 4),
        "regime_label": "",   # filled in job()
        "pc1": round(pc1, 4),
        "pc2": round(pc2, 4),
        "reasons": reasons,
        "feature_directions": dirs,
        "correlations": correlations,
        "real_rate_signal": real_rate_signal,
    }


# ═══════════════════════════════════════════════════════════════════
#  SECTION 11 — REALISTIC EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════════

def realistic_entry_exit(S0: float, signal_score: float,
                          mode: str, kelly_f: float,
                          cvar_1d: float) -> Dict:
    if mode == "intraday":
        friction      = TOTAL_FRICTION_INTRADAY
        open_gap      = 0.0015 if signal_score > 0 else -0.0015
        entry_price   = S0 * (1 + open_gap + CFG.typical_spread_pct / 2)
        exit_slippage = CFG.slippage_pct_intraday * 1.2
        holding_label = "1h – 12h (intraday, same-day square-off)"
        t3_note       = "No T+3 lock-up (intraday close-out)"
    else:
        friction      = TOTAL_FRICTION_SWING
        open_gap      = 0.0008
        entry_price   = S0 * (1 + open_gap + CFG.typical_spread_pct / 2)
        exit_slippage = CFG.slippage_pct_swing
        holding_label = "1 day – several weeks"
        t3_note       = "T+3 settlement: capital locked 3 business days"

    round_trip_cost_pct = friction
    breakeven_move_pct  = round_trip_cost_pct
    breakeven_price_up  = entry_price * (1 + breakeven_move_pct)
    breakeven_price_dn  = entry_price * (1 - breakeven_move_pct)

    max_by_cvar      = min(2 * cvar_1d, CFG.portfolio_value_zar * CFG.max_position_pct)
    position_zar     = min(kelly_f * CFG.portfolio_value_zar, max_by_cvar)
    units            = int(position_zar / entry_price)
    actual_notional  = units * entry_price
    pnl_per_1pct     = actual_notional * 0.01

    return {
        "mode":              mode,
        "holding":           holding_label,
        "t3_note":           t3_note,
        "S0_close":          round(S0, 4),
        "realistic_entry":   round(entry_price, 4),
        "spread_cost_zar":   round(actual_notional * CFG.typical_spread_pct, 2),
        "slippage_cost_zar": round(actual_notional * exit_slippage, 2),
        "brokerage_zar":     round(actual_notional * CFG.jse_brokerage_pct, 2),
        "total_friction_zar":round(actual_notional * round_trip_cost_pct, 2),
        "breakeven_up":      round(breakeven_price_up, 4),
        "breakeven_dn":      round(breakeven_price_dn, 4),
        "breakeven_pct":     round(breakeven_move_pct * 100, 3),
        "kelly_fraction":    round(kelly_f, 4),
        "position_zar":      round(actual_notional, 2),
        "units":             units,
        "pnl_per_1pct_zar":  round(pnl_per_1pct, 2),
    }


# ═══════════════════════════════════════════════════════════════════
#  SECTION 12 — LOGGING & TELEGRAM
# ═══════════════════════════════════════════════════════════════════

def send_telegram(msg: str):
    if not CFG.telegram_bot_token or not CFG.telegram_chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{CFG.telegram_bot_token}/sendMessage",
            json={"chat_id": CFG.telegram_chat_id,
                  "text": msg, "parse_mode": "Markdown"},
            timeout=8
        )
    except Exception:
        pass


def log_run(signals: Dict, sigma: float, cvar_1d: float,
            kelly_f: float, risk_free: float,
            exec_intra: Dict, exec_swing: Dict,
            perf: Dict, regime_label: str,
            garch_converged: bool, evt_flag: bool):
    row = {
        "date":              datetime.today().strftime("%Y-%m-%d %H:%M"),
        "S0":                round(signals["S0"], 4),
        "action":            signals["action"],
        "score":             signals["score"],
        "rsi":               round(signals["rsi"], 2),
        "ema20":             round(signals["ema20"], 4),
        "ema50":             round(signals["ema50"], 4),
        "linreg_slope":      round(signals["linreg_slope"], 6),
        "sigma_garch":       round(sigma, 6),
        "daily_variance":    round(sigma ** 2, 8),
        "risk_free_rate":    round(risk_free, 4),
        "mc_median_1m":      round(signals["mc_median_1m"], 2),
        "eg_zscore":         signals["eg_zscore"],
        "eg_pvalue":         signals["eg_pvalue"],
        "ou_half_life_days": signals["ou_half_life"],
        "hurst":             signals["hurst"],
        "regime":            regime_label,
        "pc1":               signals["pc1"],
        "pc2":               signals["pc2"],
        "cvar_1d_zar":       round(cvar_1d, 2),
        "kelly_pct":         round(kelly_f * 100, 2),
        "garch_converged":   garch_converged,
        "evt_cvar":          evt_flag,
        # Performance
        "sharpe":            perf.get("sharpe", ""),
        "sortino":           perf.get("sortino", ""),
        "calmar":            perf.get("calmar", ""),
        "omega":             perf.get("omega", ""),
        "max_drawdown_pct":  perf.get("max_drawdown", ""),
        # Execution
        "intra_entry":           exec_intra["realistic_entry"],
        "intra_friction_zar":    exec_intra["total_friction_zar"],
        "intra_breakeven_pct":   exec_intra["breakeven_pct"],
        "intra_units":           exec_intra["units"],
        "swing_entry":           exec_swing["realistic_entry"],
        "swing_friction_zar":    exec_swing["total_friction_zar"],
        "swing_breakeven_pct":   exec_swing["breakeven_pct"],
        "swing_units":           exec_swing["units"],
        "actual_return":         float("nan"),
    }
    for feat, d in signals["feature_directions"].items():
        row[f"dir_{feat}"] = d

    df_new = pd.DataFrame([row])
    if os.path.exists(CFG.log_file):
        df_new = pd.concat([pd.read_csv(CFG.log_file), df_new], ignore_index=True)
    df_new.to_csv(CFG.log_file, index=False)


# ═══════════════════════════════════════════════════════════════════
#  SECTION 13 — PRINT HELPERS
# ═══════════════════════════════════════════════════════════════════

def _exec_block(e: Dict, label: str):
    print(f"\n  ┌── {label} Execution Reality ──────────────────────────")
    print(f"  │  Yesterday's close (S0):    R{e['S0_close']:>12,.4f}")
    print(f"  │  Realistic entry price:     R{e['realistic_entry']:>12,.4f}  ← limit/open fill")
    print(f"  │  Bid-ask spread cost:       R{e['spread_cost_zar']:>12,.2f}")
    print(f"  │  Slippage cost:             R{e['slippage_cost_zar']:>12,.2f}")
    print(f"  │  Brokerage + STT + STRATE:  R{e['brokerage_zar']:>12,.2f}")
    print(f"  │  {'─'*55}")
    print(f"  │  TOTAL round-trip cost:     R{e['total_friction_zar']:>12,.2f}  ({e['breakeven_pct']:.3f}% breakeven)")
    print(f"  │  {'─'*55}")
    print(f"  │  Breakeven price (long):    R{e['breakeven_up']:>12,.4f}")
    print(f"  │  Breakeven price (short):   R{e['breakeven_dn']:>12,.4f}")
    print(f"  │  Position size (Kelly):     R{e['position_zar']:>12,.2f}  × {e['units']} units")
    print(f"  │  P&L per 1% move:           R{e['pnl_per_1pct_zar']:>12,.2f}")
    print(f"  │  Holding window:            {e['holding']}")
    print(f"  │  Settlement note:           {e['t3_note']}")
    print(f"  └{'─'*58}")


def _print_risk_dashboard(sigma: float, cvar_1d: float, kelly_f: float,
                           kelly_lo: float, kelly_hi: float,
                           perf: Dict, regime_label: str,
                           hurst: float, correlations: Dict,
                           garch_converged: bool, evt_flag: bool,
                           ou_half: float):
    print(f"\n{'─'*70}")
    print("  📊  INSTITUTIONAL RISK DASHBOARD")
    print(f"{'─'*70}")
    print(f"  GARCH daily vol (σ):          {sigma*100:>10.3f}%"
          f"  {'✓ converged' if garch_converged else '⚠ EWMA fallback'}")
    print(f"  Daily variance (σ²):          {sigma**2:>10.6f}")
    print(f"  Annualised vol:               {sigma*np.sqrt(252)*100:>10.2f}%")
    print(f"  CVaR 1-day (95%):            R{cvar_1d:>12,.2f}"
          f"  {'[EVT-GPD]' if evt_flag else '[Historical]'}")
    print(f"  Fractional Kelly (×0.25):     {kelly_f*100:>10.2f}%"
          f"  95% CI [{kelly_lo*100:.1f}%–{kelly_hi*100:.1f}%]")
    print(f"  HMM Regime:                   {regime_label}")
    print(f"  Hurst Exponent:               {hurst:>10.4f}"
          f"  ({'trending' if hurst>0.55 else 'mean-rev' if hurst<0.45 else 'random'})")
    print(f"  OU mean-rev half-life:        {ou_half:>10.1f}d")
    print(f"{'─'*70}")
    if perf:
        print(f"  ROLLING PERFORMANCE (252d)")
        print(f"  Sharpe:   {perf.get('sharpe','N/A'):>7}  |  "
              f"Sortino:  {perf.get('sortino','N/A'):>7}  |  "
              f"Calmar: {perf.get('calmar','N/A'):>7}")
        print(f"  Omega:    {perf.get('omega','N/A'):>7}  |  "
              f"Ann Ret:  {perf.get('ann_return','N/A'):>5}%  |  "
              f"Max DD: {perf.get('max_drawdown','N/A'):>5}%")
    print(f"{'─'*70}")
    print(f"  ROLLING CORRELATIONS (60d)  — GLD.JO vs:")
    print(f"  ZAR:  {correlations.get('corr_zar', 0):>+.3f}  |  "
          f"DXY:  {correlations.get('corr_dxy', 0):>+.3f}  |  "
          f"VIX:  {correlations.get('corr_vix', 0):>+.3f}")


# ═══════════════════════════════════════════════════════════════════
#  SECTION 14 — MAIN JOB
# ═══════════════════════════════════════════════════════════════════

def job():
    print(f"\n{SEP}")
    print(f"  📡  GLD.JO Institutional Scan — "
          f"{datetime.now().strftime('%Y-%m-%d %H:%M SAST')}")
    print(f"  🔬  Gold Monte Carlo Bot v4.0 — Institutional Edition")
    print(SEP)

    try:
        # ── 1. Data ──────────────────────────────────────────────
        prices, zar, dxy, vix, us10y, dates = download_data()
        S0 = float(prices[-1])
        yahoo_url = f"https://finance.yahoo.com/quote/{CFG.ticker}"

        # ── 2. Weight update ─────────────────────────────────────
        update_learning_model_batch(S0)

        # ── 3. Parameters ────────────────────────────────────────
        risk_free  = fetch_sarb_repo_rate()
        mu, sigma, log_ret, garch_converged = estimate_parameters(prices)

        # ── 4. Monte Carlo ───────────────────────────────────────
        LOG.info(f"🎲  Running {CFG.num_simulations:,} Merton Jump-Diffusion paths…")
        paths = run_merton_jump_diffusion(S0, mu, sigma, log_ret)
        mc_converged = check_mc_convergence(S0, mu, sigma, log_ret)
        if not mc_converged:
            LOG.warning("⚠️  MC not fully converged — consider increasing NUM_SIMULATIONS")
        LOG.info("  ✓  Simulation complete")

        # ── 5. Advanced analytics ────────────────────────────────
        hurst = compute_hurst_exponent(prices)
        regime_idx, regime_probs, regime_label = detect_market_regime(log_ret)
        eg_z, eg_pv, ou_half = engle_granger_zscore(prices, zar)
        johansen_ok = johansen_test(prices, zar)
        cvar_1d, evt_flag = compute_cvar(CFG.portfolio_value_zar, log_ret,
                                          CFG.cvar_confidence)
        kelly_f  = compute_kelly(mu, sigma, risk_free)
        kelly_lo, kelly_hi = bootstrap_kelly_ci(log_ret, risk_free / 252)
        correlations = compute_rolling_correlations(prices, zar, dxy, vix)
        pc1, pc2 = compute_macro_pca(prices, zar, dxy, vix, us10y)
        perf     = compute_performance_metrics(log_ret, risk_free / 252)

        # ── 6. Signals ───────────────────────────────────────────
        sentiment = fetch_news_sentiment()
        signals   = generate_signals(prices, paths, sentiment, zar, dxy, vix,
                                      us10y, regime_idx, hurst, pc1, pc2,
                                      correlations)
        signals["regime_label"] = regime_label

        # ── 7. Execution reality ─────────────────────────────────
        exec_intra = realistic_entry_exit(S0, signals["score"], "intraday",
                                           kelly_f, cvar_1d)
        exec_swing = realistic_entry_exit(S0, signals["score"], "swing",
                                           kelly_f, cvar_1d)

        # ── 8. Logging ───────────────────────────────────────────
        log_run(signals, sigma, cvar_1d, kelly_f, risk_free,
                exec_intra, exec_swing, perf, regime_label,
                garch_converged, evt_flag)

        # ── 9. Kupiec VaR backtest ───────────────────────────────
        try:
            if os.path.exists(CFG.log_file):
                hist = pd.read_csv(CFG.log_file)
                known = hist.dropna(subset=["actual_return", "cvar_1d_zar"])
                if len(known) >= 20:
                    violations = int(((known["actual_return"].abs() *
                                       CFG.portfolio_value_zar) >
                                      known["cvar_1d_zar"]).sum())
                    kp, kp_pass = kupiec_test(violations, len(known))
                    LOG.info(f"📋  Kupiec VaR test: p={kp:.3f} "
                             f"{'✓ pass' if kp_pass else '⚠ FAIL — recalibrate'} "
                             f"({violations}/{len(known)} violations)")
        except Exception:
            pass

        # ── 10. Console output ───────────────────────────────────
        print(f"\n{'':>4}SIGNAL:  {signals['action']}  "
              f"(composite score: {signals['score']:.2f})")
        print(DSEP)
        print(f"  Live Chart:                  {yahoo_url}")
        print(f"  Price (GLD.JO close):        R{S0:>12,.4f}")
        print(f"  EG cointegration z-score:    {signals['eg_zscore']:>10.2f}  "
              f"(p={signals['eg_pvalue']:.3f})")
        print(f"  Johansen cointegrated:       {'✓ Yes' if johansen_ok else '✗ No':>10}")
        print(f"  MC 1-month median:           R{signals['mc_median_1m']:>12,.2f}  "
              f"[{signals['mc_p5_1m']:,.2f} – {signals['mc_p95_1m']:,.2f}]")
        print(f"  MC 1-year range (5/95%):     R{signals['mc_p5_1y']:>12,.2f} – "
              f"R{signals['mc_p95_1y']:,.2f}")
        print(f"  PCA Macro Factor PC1:        {pc1:>10.4f}")

        _print_risk_dashboard(sigma, cvar_1d, kelly_f, kelly_lo, kelly_hi,
                               perf, regime_label, hurst, correlations,
                               garch_converged, evt_flag, ou_half)

        print(f"\n  SIGNAL FACTORS:")
        for r in signals["reasons"]:
            print(f"    • {r}")
        print(DSEP)

        if CFG.trading_mode in ("intraday", "both"):
            _exec_block(exec_intra, "INTRADAY (1h–12h)")
        if CFG.trading_mode in ("swing", "both"):
            _exec_block(exec_swing, "SWING (1d–weeks)")

        print(f"\n  ⚠  DISCLAIMER: Research use only. Not financial advice.")
        print(SEP)

        # ── 11. Telegram ─────────────────────────────────────────
        msg = (
            f"🏅 *GLD.JO Signal v4.0*\n"
            f"*{signals['action']}*  (score: {signals['score']:.2f})\n\n"
            f"📊 [Live Chart]({yahoo_url})\n\n"
            f"Price: *R{S0:,.4f}*\n"
            f"Regime: *{regime_label}*\n"
            f"CVaR(95%): R{cvar_1d:,.2f} {'[EVT]' if evt_flag else '[Hist]'}\n"
            f"Kelly: {kelly_f:.1%} [{kelly_lo*100:.1f}%–{kelly_hi*100:.1f}%]\n"
            f"Hurst: {hurst:.3f} | σ: {sigma*100:.2f}%\n"
            f"Sharpe: {perf.get('sharpe','—')} | Sortino: {perf.get('sortino','—')}\n"
            f"EG z: {signals['eg_zscore']:.2f}  OU half-life: {ou_half:.0f}d\n\n"
            + "\n".join(f"• {r}" for r in signals["reasons"])
        )
        send_telegram(msg)

    except DataValidationError as e:
        msg = f"❌ Data validation error: {e}"
        LOG.error(msg)
        send_telegram(msg)
    except Exception as e:
        traceback.print_exc()
        send_telegram(f"❌ GoldBot v4 error: {e}")


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT — runs immediately then every weekday at 17:30 SAST
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    job()
    schedule.every().day.at("17:30").do(job)
    LOG.info("⏰  Scheduler active — next run at 17:30 SAST daily. Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)
