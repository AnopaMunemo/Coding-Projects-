"""
═══════════════════════════════════════════════════════════════════
  GOLD MONTE CARLO & ML TRADING BOT  ─  South Africa (GLD.JO / JSE)
  v2.0 ─ Improved Mathematics, GARCH Volatility, Kelly Criterion,
          Feature-Level Attribution, VaR, EMA, ZAR Macro Factor,
          Telegram Alerts, Daily Scheduling
═══════════════════════════════════════════════════════════════════

  MATHEMATICAL MODELS USED:
  ─────────────────────────
  • GBM (Monte Carlo):   S_{t+dt} = S_t · exp[(μ - σ²/2)dt + σ√dt · Z]
  • GARCH(1,1):          σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}
  • EMA:                 EMA_t = P_t · K + EMA_{t-1} · (1 - K),  K = 2/(n+1)
  • RSI (Wilder's):      RSI = 100 - 100/(1 + EMA(gains)/EMA(losses))
  • Linear Regression:   Y = α + β·X  (trend slope, normalised)
  • Parametric VaR:      VaR = V_p · z · σ · √t
  • Kelly Criterion:     f* = (p·b - q) / b

  INSTALL REQUIREMENTS:
  ─────────────────────
  pip install yfinance pandas numpy scipy arch schedule requests
═══════════════════════════════════════════════════════════════════
"""

import numpy as np
import pandas as pd
import requests
import json
import os
import time
import schedule
import warnings
from datetime import datetime
from scipy import stats

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("Missing: pip install yfinance")

try:
    from arch import arch_model
    GARCH_AVAILABLE = True
except ImportError:
    GARCH_AVAILABLE = False
    print("⚠️  'arch' not found — pip install arch — falling back to EWMA volatility.")

warnings.filterwarnings("ignore")


# ═══════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

TICKER          = "GLD.JO"          # NewGold ETF on the JSE (ZAR-priced)
                                     # ← was "GLD" (USD SPDR). GLD.JO is the
                                     #   correct JSE-listed instrument.
START_DATE      = "2020-01-01"
FORECAST_DAYS   = 252                # ~1 trading year
NUM_SIMULATIONS = 10_000

LOG_FILE        = "gold_simulation_log.csv"
WEIGHTS_FILE    = "model_weights.json"

# Telegram (get free token from @BotFather; chat_id via @userinfobot)
TELEGRAM_BOT_TOKEN = ""             # ← paste your Telegram Bot Token
TELEGRAM_CHAT_ID   = ""             # ← paste your Chat ID

# NewsAPI
NEWS_API_KEY  = ""                  # ← free key from newsapi.org
NEWS_QUERY    = "gold price South Africa JSE rand mining"

# Risk Parameters (adjust to your account)
PORTFOLIO_VALUE_ZAR = 100_000       # Your total ZAR portfolio value
VAR_CONFIDENCE      = 0.95          # 95% VaR confidence level
RISK_FREE_RATE      = 0.0825        # ~SARB repo rate (annualised)

# Signal thresholds
STRONG_BUY_THRESHOLD  =  2.5
BUY_THRESHOLD         =  1.0
STRONG_SELL_THRESHOLD = -2.5
SELL_THRESHOLD        = -1.0

# Default feature weights (overwritten by WEIGHTS_FILE after first run)
DEFAULT_WEIGHTS = {
    "mc":       1.5,    # Monte Carlo GBM forecast
    "ema_cross":1.0,    # EMA(20) vs EMA(50) crossover
    "rsi":      1.0,    # RSI momentum
    "news":     1.0,    # News sentiment
    "dxy":      1.0,    # US Dollar Index
    "vix":      1.0,    # CBOE Volatility Index
    "zar":      1.2,    # USD/ZAR exchange rate (key for GLD.JO)
    "linreg":   0.8,    # Linear regression price slope
}


# ═══════════════════════════════════════════════════════════════════
#  1. DATA DOWNLOAD  — GLD.JO + ZAR/USD + DXY + VIX
# ═══════════════════════════════════════════════════════════════════

def download_data() -> tuple:
    """
    Fetch daily Close prices for:
      GLD.JO  — NewGold ETF in ZAR (the actual JSE instrument)
      ZAR=X   — USD/ZAR exchange rate  (higher = weaker Rand)
      DX-Y.NYB— US Dollar Index        (inverse correlation to gold)
      ^VIX    — CBOE Volatility Index  (positive correlation to gold)
    """
    print(f"\n📥 [{datetime.now().strftime('%H:%M:%S')}] Fetching market data...")

    tickers = f"{TICKER} ZAR=X DX-Y.NYB ^VIX"
    raw = yf.download(tickers, start=START_DATE, auto_adjust=True, progress=False)["Close"]
    raw = raw.ffill().dropna()

    prices = raw[TICKER].to_numpy(dtype=float)
    zar    = raw["ZAR=X"].to_numpy(dtype=float)       # USD → ZAR
    dxy    = raw["DX-Y.NYB"].to_numpy(dtype=float)
    vix    = raw["^VIX"].to_numpy(dtype=float)

    print(f"   ✓ {len(prices)} trading days loaded. Latest GLD.JO = R{prices[-1]:,.2f}")
    return prices, zar, dxy, vix, raw.index


# ═══════════════════════════════════════════════════════════════════
#  2. MATHEMATICAL INDICATORS
# ═══════════════════════════════════════════════════════════════════

def compute_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """
    Exponential Moving Average (weights recent prices more heavily):

        EMA_t = P_t · K + EMA_{t-1} · (1 - K)
        K = 2 / (n + 1)

    Superior to SMA for trend detection because it reacts faster
    to price changes without being as noisy as raw price data.
    """
    k = 2.0 / (period + 1)
    ema = np.empty(len(prices))
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = prices[i] * k + ema[i - 1] * (1.0 - k)
    return ema


def compute_rsi_wilders(prices: np.ndarray, period: int = 14) -> float:
    """
    RSI using Wilder's Smoothing Method (the correct/standard approach):

        RS  = EMA_wilder(gains, period) / EMA_wilder(losses, period)
        RSI = 100 - 100 / (1 + RS)

    Wilder's smoothing uses alpha = 1/period, which is slower and
    more stable than a simple average. This avoids premature signals.

    Thresholds used:
      < 35  → Oversold  (buy signal)
      > 65  → Overbought (sell signal)
    [Slightly wider than the classic 30/70 to reduce false signals
     on a commodity ETF like GLD.JO]
    """
    data = prices[-(period * 3):]   # Use 3x history for warm-up
    deltas = np.diff(data)

    gains  = np.where(deltas > 0,  deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Wilder's smoothing: alpha = 1/period (equivalent to EMA with n = 2·period-1)
    alpha    = 1.0 / period
    avg_gain = float(gains[:period].mean())
    avg_loss = float(losses[:period].mean())

    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = alpha * g + (1.0 - alpha) * avg_gain
        avg_loss = alpha * l + (1.0 - alpha) * avg_loss

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def compute_linreg_slope(prices: np.ndarray, period: int = 20) -> float:
    """
    Fits the OLS regression line:  Y = α + β·X

    Returns β normalised by the current price so it is dimensionless
    and comparable across different price levels.

    Positive slope → price in an uptrend over the last `period` days.
    """
    y = prices[-period:].astype(float)
    x = np.arange(period, dtype=float)
    slope, _, _, _, _ = stats.linregress(x, y)
    return float(slope) / float(prices[-1])   # Normalise to be scale-free


def compute_garch_volatility(log_returns: np.ndarray) -> float:
    """
    GARCH(1,1) — Generalised Autoregressive Conditional Heteroskedasticity:

        σ²_t = ω + α·ε²_{t-1} + β·σ²_{t-1}

    Where:
      ω              = baseline (long-run) variance
      α·ε²_{t-1}     = reaction to yesterday's shock (ARCH term)
      β·σ²_{t-1}     = persistence of yesterday's variance (GARCH term)

    Why GARCH instead of simple std()?
      Gold exhibits volatility clustering — calm periods are followed by
      turbulent periods. A static σ over the full history severely
      under/over-estimates current risk. GARCH captures this.

    Falls back to RiskMetrics EWMA (λ=0.94) if 'arch' is not installed.
    """
    if GARCH_AVAILABLE:
        try:
            # Scale by 100 for numerical stability (arch library convention)
            model  = arch_model(log_returns * 100, vol="Garch", p=1, q=1,
                                dist="normal", rescale=False)
            result = model.fit(disp="off", show_warning=False)
            # One-step-ahead variance forecast, convert back from % scale
            fcast  = result.forecast(horizon=1)
            sigma  = float(np.sqrt(fcast.variance.values[-1, 0])) / 100.0
            if np.isfinite(sigma) and 0 < sigma < 1:
                return sigma
        except Exception:
            pass   # Fall through to EWMA

    # ── EWMA Fallback (RiskMetrics, λ = 0.94) ──────────────────────
    # σ²_t = λ·σ²_{t-1} + (1-λ)·r²_{t-1}
    lam = 0.94
    var = float(np.var(log_returns[:20]))
    for r in log_returns:
        var = lam * var + (1.0 - lam) * r ** 2
    return float(np.sqrt(var))


def estimate_parameters(prices: np.ndarray) -> tuple:
    """
    Estimate drift (μ) and dynamic volatility (σ) from daily log-returns.

    GBM assumes log-returns are i.i.d. Normal(μ, σ²):
        r_t = log(S_t / S_{t-1}) ~ N(μ, σ²)
    """
    log_returns = np.log(prices[1:] / prices[:-1])
    mu    = float(log_returns.mean())
    sigma = compute_garch_volatility(log_returns)
    return mu, sigma, log_returns


# ═══════════════════════════════════════════════════════════════════
#  3. MONTE CARLO SIMULATION  (Geometric Brownian Motion)
# ═══════════════════════════════════════════════════════════════════

def run_monte_carlo(S0: float, mu: float, sigma: float) -> np.ndarray:
    """
    Simulates NUM_SIMULATIONS price paths via the discrete GBM equation:

        S_{t+dt} = S_t · exp[(μ - σ²/2)·dt + σ·√dt · Z]

    The (μ - σ²/2) term is the Itô correction — it accounts for the
    Jensen's Inequality gap between the arithmetic mean of the lognormal
    distribution and its median.  Without it, paths are upward-biased.

    dt = 1/252  (one trading day expressed as a fraction of a year)
    Z  ~ N(0, 1) i.i.d.
    """
    dt    = 1.0 / 252
    paths = np.zeros((FORECAST_DAYS + 1, NUM_SIMULATIONS))
    paths[0] = S0

    # Vectorised for speed: generate all random shocks at once
    Z = np.random.standard_normal((FORECAST_DAYS, NUM_SIMULATIONS))
    drift     = (mu - 0.5 * sigma ** 2) * dt
    diffusion = sigma * np.sqrt(dt)

    for t in range(1, FORECAST_DAYS + 1):
        paths[t] = paths[t - 1] * np.exp(drift + diffusion * Z[t - 1])

    return paths


# ═══════════════════════════════════════════════════════════════════
#  4. RISK METRICS
# ═══════════════════════════════════════════════════════════════════

def compute_var(portfolio_value: float, sigma: float,
                confidence: float = 0.95, horizon_days: int = 1) -> float:
    """
    Parametric (Gaussian) Value at Risk:

        VaR = V_p · z · σ · √t

    Where:
      V_p = portfolio value in ZAR
      z   = z-score for confidence level (1.645 for 95%, 2.326 for 99%)
      σ   = daily volatility (from GARCH)
      t   = horizon in trading days

    Interpretation: With `confidence` probability, you will NOT lose
    more than VaR over the next `horizon_days` trading days.
    """
    z   = float(stats.norm.ppf(confidence))
    var = portfolio_value * z * sigma * np.sqrt(horizon_days)
    return float(var)


def compute_kelly_fraction(log_file: str) -> float:
    """
    Kelly Criterion — Optimal Position Sizing:

        f* = (p·b - q) / b

    Where:
      p = historical win rate  (fraction of correct directional calls)
      q = 1 - p  (loss rate)
      b = avg_win / avg_loss  (payoff ratio)

    Kelly gives the fraction of capital to risk on each trade to
    maximise the long-run geometric growth rate of the portfolio.

    Capped at 25% (half-Kelly philosophy) to account for model error
    and the real-world fat tails Kelly assumes away.

    Returns 5% default until at least 10 completed trades are logged.
    """
    if not os.path.exists(log_file):
        return 0.05

    df = pd.read_csv(log_file).dropna(subset=["actual_return"])
    if len(df) < 10:
        return 0.05

    wins   = df[df["actual_return"] > 0]["actual_return"]
    losses = df[df["actual_return"] <= 0]["actual_return"].abs()

    if losses.empty or wins.empty:
        return 0.05

    p      = len(wins) / len(df)       # Historical win rate
    q      = 1.0 - p                   # Loss rate
    b      = wins.mean() / losses.mean()  # Payoff ratio
    f_star = (p * b - q) / b

    # Negative Kelly means the edge is negative — don't trade
    return float(np.clip(f_star, 0.0, 0.25))


# ═══════════════════════════════════════════════════════════════════
#  5. CONTINUOUS LEARNING  — Feature-Level Attribution
# ═══════════════════════════════════════════════════════════════════

def load_weights() -> dict:
    if os.path.exists(WEIGHTS_FILE):
        with open(WEIGHTS_FILE, "r") as f:
            loaded = json.load(f)
        # Merge with defaults so new features always have a starting weight
        return {**DEFAULT_WEIGHTS, **loaded}
    return DEFAULT_WEIGHTS.copy()


def save_weights(weights: dict):
    with open(WEIGHTS_FILE, "w") as f:
        json.dump(weights, f, indent=4)


def update_learning_model(current_price: float):
    """
    Feature-Level Reward Attribution  (fixes the original "all-or-nothing" flaw)

    Original problem:
      If the trade won, ALL weights were increased — even signals that
      were WRONG. This inflated weights and destroyed signal quality.

    Fix — Isolated Attribution:
      After each trade, we look at what EACH individual signal predicted.
      If market went UP:
        → Signals that said BUY  get reward  (+lr)
        → Signals that said SELL get penalty (-lr)
      If market went DOWN:
        → Signals that said SELL get reward  (+lr)
        → Signals that said BUY  get penalty (-lr)

    This ensures weights converge toward the features that are
    genuinely predictive, rather than getting a free ride.

    Learning rate lr = 0.05 (conservative; avoids overshooting).
    """
    if not os.path.exists(LOG_FILE):
        return

    df = pd.read_csv(LOG_FILE)
    if len(df) < 2:
        return

    last = df.iloc[-1]

    # We need the previous row's price, not the latest
    if len(df) < 2:
        return

    prev = df.iloc[-2]
    price_prev  = float(prev["S0"])
    actual_ret  = (current_price - price_prev) / price_prev
    went_up     = actual_ret > 0

    # Collect per-feature direction columns (saved by log_run)
    feature_dirs = {
        col[4:]: int(last[col])
        for col in df.columns
        if col.startswith("dir_") and not pd.isna(last[col])
    }

    if not feature_dirs:
        return   # Old log format — skip this cycle

    lr      = 0.05
    weights = load_weights()
    updates = {}

    for feature, direction in feature_dirs.items():
        if feature not in weights or direction == 0:
            continue
        correct = (direction > 0 and went_up) or (direction < 0 and not went_up)
        delta   = +lr if correct else -lr
        weights[feature] = round(max(0.10, weights[feature] + delta), 4)
        updates[feature] = delta

    # Write the realised return back into the log
    df.loc[df.index[-1], "actual_return"] = round(actual_ret, 6)
    df.to_csv(LOG_FILE, index=False)

    save_weights(weights)
    arrow = "📈 UP" if went_up else "📉 DOWN"
    print(f"🧠 Weights updated | Market moved {arrow} ({actual_ret:+.2%})")
    pos = {k: f"+{v}" for k, v in updates.items() if v > 0}
    neg = {k: str(v) for k, v in updates.items() if v < 0}
    if pos: print(f"   ✅ Rewarded:  {pos}")
    if neg: print(f"   ❌ Penalised: {neg}")


# ═══════════════════════════════════════════════════════════════════
#  6. SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════════

def generate_signals(prices: np.ndarray, paths: np.ndarray,
                     sentiment_score: float,
                     zar: np.ndarray, dxy: np.ndarray, vix: np.ndarray) -> dict:
    """
    Composite weighted scoring across 8 independent signals.
    Each signal records its direction (+1 / -1 / 0) for ML attribution.
    """
    S0      = float(prices[-1])
    weights = load_weights()

    # ── Compute all indicators ──────────────────────────────────────
    ema20        = compute_ema(prices, 20)
    ema50        = compute_ema(prices, 50)
    rsi_val      = compute_rsi_wilders(prices, 14)
    linreg_slope = compute_linreg_slope(prices, 20)

    # Normalised 10-day trends for macro factors
    dxy_trend = (dxy[-1] - dxy[-10]) / dxy[-10]
    vix_trend = (vix[-1] - vix[-10]) / vix[-10]
    zar_trend = (zar[-1] - zar[-10]) / zar[-10]   # +ve = Rand weakening

    # Monte Carlo percentiles at 1-month horizon (21 trading days)
    median_1m = float(np.percentile(paths[21], 50))
    p5_1m     = float(np.percentile(paths[21],  5))
    p95_1m    = float(np.percentile(paths[21], 95))
    mc_upside = (median_1m - S0) / S0

    score             = 0.0
    reasons           = []
    feature_directions = {}   # Track each signal for ML

    # ── Signal 1: Monte Carlo (GBM) Forecast ───────────────────────
    # Positive if median 1-month projection > current price by >1%
    if mc_upside > 0.01:
        score += weights["mc"]
        reasons.append(f"MC Upside {mc_upside:+.1%}  [Med: R{median_1m:,.2f}] ↑")
        feature_directions["mc"] = 1
    elif mc_upside < -0.01:
        score -= weights["mc"]
        reasons.append(f"MC Downside {mc_upside:+.1%}  [Med: R{median_1m:,.2f}] ↓")
        feature_directions["mc"] = -1
    else:
        feature_directions["mc"] = 0

    # ── Signal 2: EMA Crossover ─────────────────────────────────────
    # EMA(20) > EMA(50) → price accelerating upward (Golden Cross-like)
    ema_gap = (ema20[-1] - ema50[-1]) / ema50[-1]
    if ema20[-1] > ema50[-1]:
        score += weights["ema_cross"]
        reasons.append(f"EMA20 > EMA50  (gap {ema_gap:+.2%}) ↑")
        feature_directions["ema_cross"] = 1
    else:
        score -= weights["ema_cross"]
        reasons.append(f"EMA20 < EMA50  (gap {ema_gap:+.2%}) ↓")
        feature_directions["ema_cross"] = -1

    # ── Signal 3: RSI (Wilder's Smoothing, 14-period) ───────────────
    if rsi_val < 35:
        score += weights["rsi"]
        reasons.append(f"RSI {rsi_val:.1f} — Oversold ↑")
        feature_directions["rsi"] = 1
    elif rsi_val > 65:
        score -= weights["rsi"]
        reasons.append(f"RSI {rsi_val:.1f} — Overbought ↓")
        feature_directions["rsi"] = -1
    else:
        reasons.append(f"RSI {rsi_val:.1f} — Neutral")
        feature_directions["rsi"] = 0

    # ── Signal 4: News Sentiment ────────────────────────────────────
    if abs(sentiment_score) > 0.1:
        score += sentiment_score * weights["news"]
        label  = "Bullish ↑" if sentiment_score > 0 else "Bearish ↓"
        reasons.append(f"News Sentiment {sentiment_score:+.2f} — {label}")
        feature_directions["news"] = 1 if sentiment_score > 0 else -1
    else:
        feature_directions["news"] = 0

    # ── Signal 5: DXY — US Dollar Index ─────────────────────────────
    # Gold (globally) is priced in USD, so a weaker Dollar → higher gold
    if dxy_trend < -0.01:
        score += weights["dxy"]
        reasons.append(f"Weak Dollar ({dxy_trend:+.1%} 10d) ↑")
        feature_directions["dxy"] = 1
    elif dxy_trend > 0.01:
        score -= weights["dxy"]
        reasons.append(f"Strong Dollar ({dxy_trend:+.1%} 10d) ↓")
        feature_directions["dxy"] = -1
    else:
        feature_directions["dxy"] = 0

    # ── Signal 6: VIX — Fear Index ──────────────────────────────────
    # Gold is a safe haven: rising fear → gold demand → price up
    if vix_trend > 0.05:
        score += weights["vix"]
        reasons.append(f"VIX Spiking ({vix_trend:+.1%} 10d) — Safe Haven Bid ↑")
        feature_directions["vix"] = 1
    elif vix_trend < -0.05:
        score -= weights["vix"]
        reasons.append(f"VIX Falling ({vix_trend:+.1%} 10d) — Risk-On ↓")
        feature_directions["vix"] = -1
    else:
        feature_directions["vix"] = 0

    # ── Signal 7: ZAR/USD — Rand Strength ───────────────────────────
    # GLD.JO is priced in ZAR. Even if USD gold is flat, a weaker
    # Rand (ZAR/USD rises) makes GLD.JO more expensive in ZAR terms.
    # This is a GLD.JO-specific signal absent from the US GLD bot.
    if zar_trend > 0.01:
        score += weights["zar"]
        reasons.append(f"Rand Weakening ({zar_trend:+.1%} 10d) → ZAR gold higher ↑")
        feature_directions["zar"] = 1
    elif zar_trend < -0.01:
        score -= weights["zar"]
        reasons.append(f"Rand Strengthening ({zar_trend:+.1%} 10d) → ZAR gold lower ↓")
        feature_directions["zar"] = -1
    else:
        feature_directions["zar"] = 0

    # ── Signal 8: Linear Regression Slope ───────────────────────────
    # Normalised OLS slope over last 20 days: Y = α + β·X, return β/S0
    if linreg_slope > 0.001:
        score += weights["linreg"]
        reasons.append(f"OLS Trend Slope +{linreg_slope:.4f} (uptrend) ↑")
        feature_directions["linreg"] = 1
    elif linreg_slope < -0.001:
        score -= weights["linreg"]
        reasons.append(f"OLS Trend Slope {linreg_slope:.4f} (downtrend) ↓")
        feature_directions["linreg"] = -1
    else:
        feature_directions["linreg"] = 0

    # ── Map score to action ─────────────────────────────────────────
    if   score >= STRONG_BUY_THRESHOLD:  action = "🟢 STRONG BUY"
    elif score >= BUY_THRESHOLD:         action = "🟡 BUY"
    elif score <= STRONG_SELL_THRESHOLD: action = "🔴 STRONG SELL"
    elif score <= SELL_THRESHOLD:        action = "🟠 SELL"
    else:                                action = "⚪ HOLD"

    return {
        "action":            action,
        "score":             score,
        "S0":                S0,
        "rsi":               rsi_val,
        "ema20":             float(ema20[-1]),
        "ema50":             float(ema50[-1]),
        "linreg_slope":      linreg_slope,
        "mc_median_1m":      median_1m,
        "mc_p5_1m":          p5_1m,
        "mc_p95_1m":         p95_1m,
        "reasons":           reasons,
        "feature_directions":feature_directions,
    }


# ═══════════════════════════════════════════════════════════════════
#  7. NEWS SENTIMENT
# ═══════════════════════════════════════════════════════════════════

def fetch_news_sentiment() -> float:
    """
    Naïve keyword-count sentiment over the 20 most recent headlines.
    Returns a score in [-1, +1]:  positive = bullish, negative = bearish.

    For production, replace with a proper NLP model (FinBERT, etc.).
    """
    if not NEWS_API_KEY:
        return 0.0
    try:
        url = (
            f"https://newsapi.org/v2/everything"
            f"?q={requests.utils.quote(NEWS_QUERY)}"
            f"&sortBy=publishedAt&pageSize=20&language=en"
            f"&apiKey={NEWS_API_KEY}"
        )
        articles = requests.get(url, timeout=8).json().get("articles", [])

        bullish = ["surge", "rally", "gain", "rise", "buy", "high",
                   "positive", "record", "strong", "breakout", "demand"]
        bearish = ["fall", "drop", "decline", "lose", "sell", "low",
                   "negative", "weak", "crash", "dump", "oversold"]

        pos, neg = 0, 0
        for article in articles:
            title = (article.get("title") or "").lower()
            pos  += sum(1 for w in bullish if w in title)
            neg  += sum(1 for w in bearish if w in title)

        total = pos + neg
        return float((pos - neg) / total) if total > 0 else 0.0
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════
#  8. TELEGRAM ALERTS
# ═══════════════════════════════════════════════════════════════════

def send_telegram_alert(message: str):
    """
    Sends a Markdown-formatted signal alert to your Telegram chat.

    Setup (free, 5 min):
      1. Search @BotFather on Telegram → /newbot → copy API Token
      2. Search @userinfobot → start it → copy your Chat ID
      3. Paste both into TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID above
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = (
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
            f"/sendMessage"
        )
        payload = {
            "chat_id":    TELEGRAM_CHAT_ID,
            "text":       message,
            "parse_mode": "Markdown",
        }
        resp = requests.post(url, json=payload, timeout=8)
        if resp.status_code != 200:
            print(f"⚠️  Telegram HTTP {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        print(f"⚠️  Telegram alert failed: {e}")


# ═══════════════════════════════════════════════════════════════════
#  9. LOGGING
# ═══════════════════════════════════════════════════════════════════

def log_run(signals: dict, sigma: float, var_1d: float, kelly_f: float):
    """
    Appends one row per run. Includes per-feature signal directions
    (dir_<feature>) so the ML attribution system can use them next run.

    The `actual_return` column is intentionally left NaN and filled
    in retroactively by update_learning_model() on the following run.
    """
    row = {
        "date":            datetime.today().strftime("%Y-%m-%d %H:%M"),
        "S0":              round(signals["S0"], 4),
        "action":          signals["action"],
        "score":           round(signals["score"], 4),
        "rsi":             round(signals["rsi"], 2),
        "ema20":           round(signals["ema20"], 4),
        "ema50":           round(signals["ema50"], 4),
        "linreg_slope":    round(signals["linreg_slope"], 6),
        "sigma_garch":     round(sigma, 6),
        "sigma_annual":    round(sigma * np.sqrt(252), 4),
        "mc_median_1m":    round(signals["mc_median_1m"], 2),
        "mc_p5_1m":        round(signals["mc_p5_1m"], 2),
        "mc_p95_1m":       round(signals["mc_p95_1m"], 2),
        "var_1d_zar":      round(var_1d, 2),
        "kelly_pct":       round(kelly_f * 100, 2),
        "actual_return":   np.nan,   # ← filled next run by update_learning_model
    }
    for feat, direction in signals["feature_directions"].items():
        row[f"dir_{feat}"] = direction

    df_new = pd.DataFrame([row])
    if os.path.exists(LOG_FILE):
        df_old     = pd.read_csv(LOG_FILE)
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_combined = df_new
    df_combined.to_csv(LOG_FILE, index=False)


# ═══════════════════════════════════════════════════════════════════
#  10. MAIN JOB
# ═══════════════════════════════════════════════════════════════════

def job():
    separator = "═" * 62
    print(f"\n{separator}")
    print(f"  📡 GLD.JO Market Scan — {datetime.now().strftime('%Y-%m-%d %H:%M SAST')}")
    print(separator)

    try:
        # ── Step 1: Download data ──────────────────────────────────
        prices, zar, dxy, vix, dates = download_data()
        S0 = float(prices[-1])

        # ── Step 2: Feature-level ML weight update ─────────────────
        update_learning_model(S0)

        # ── Step 3: Estimate μ and GARCH σ ─────────────────────────
        mu, sigma, log_returns = estimate_parameters(prices)

        # ── Step 4: Monte Carlo (GBM paths) ───────────────────────
        paths = run_monte_carlo(S0, mu, sigma)

        # ── Step 5: News sentiment ─────────────────────────────────
        sentiment = fetch_news_sentiment()

        # ── Step 6: Generate composite signal ─────────────────────
        signals = generate_signals(prices, paths, sentiment, zar, dxy, vix)

        # ── Step 7: Risk metrics ───────────────────────────────────
        var_1d  = compute_var(PORTFOLIO_VALUE_ZAR, sigma, VAR_CONFIDENCE, 1)
        var_5d  = compute_var(PORTFOLIO_VALUE_ZAR, sigma, VAR_CONFIDENCE, 5)
        kelly_f = compute_kelly_fraction(LOG_FILE)

        # ── Step 8: Log this run ───────────────────────────────────
        log_run(signals, sigma, var_1d, kelly_f)

        # ── Step 9: Print full report ──────────────────────────────
        ann_vol = sigma * np.sqrt(252)
        print(f"""
  SIGNAL        {signals['action']}   (Score: {signals['score']:.2f})
  ─────────────────────────────────────────────────────────
  Price (GLD.JO):   R{S0:>10,.2f}
  EMA(20/50):       R{signals['ema20']:,.2f} / R{signals['ema50']:,.2f}
  RSI (14, Wilder): {signals['rsi']:.1f}
  OLS Slope (20d):  {signals['linreg_slope']:+.5f}
  ─────────────────────────────────────────────────────────
  σ  (GARCH, daily):{sigma:.5f}  ({ann_vol:.2%} annualised)
  MC Median (1m):   R{signals['mc_median_1m']:>10,.2f}
  MC P5  / P95:     R{signals['mc_p5_1m']:,.2f}  /  R{signals['mc_p95_1m']:,.2f}
  ─────────────────────────────────────────────────────────
  VaR 1-day  (95%): R{var_1d:>10,.2f}  on R{PORTFOLIO_VALUE_ZAR:,}
  VaR 5-day  (95%): R{var_5d:>10,.2f}
  Kelly f*:         {kelly_f:.1%} of portfolio
  ─────────────────────────────────────────────────────────
  SIGNAL BREAKDOWN:""")
        for r in signals["reasons"]:
            print(f"    • {r}")
        print(separator)

        # ── Step 10: Telegram alert ────────────────────────────────
        alert = (
            f"🏅 *GLD.JO Signal* — {datetime.now().strftime('%d %b %Y %H:%M')}\n"
            f"*{signals['action']}*  (Score: {signals['score']:.2f})\n\n"
            f"Price: *R{S0:,.2f}*\n"
            f"RSI(14): {signals['rsi']:.1f}   |   σ: {ann_vol:.2%} p.a.\n"
            f"MC Median (1m): R{signals['mc_median_1m']:,.2f}\n"
            f"VaR(1d, 95%): R{var_1d:,.2f}\n"
            f"Kelly: {kelly_f:.1%} of portfolio\n\n"
            + "\n".join(f"• {r}" for r in signals["reasons"])
        )
        send_telegram_alert(alert)

    except Exception as e:
        import traceback
        print(f"❌ Error: {e}")
        traceback.print_exc()
        send_telegram_alert(f"❌ GoldBot error: {e}")


# ═══════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🚀 Gold Bot v2.0 — JSE Monitor Starting")
    print(f"   Ticker:     {TICKER}  (NewGold ETF, ZAR-priced)")
    print(f"   Portfolio:  R{PORTFOLIO_VALUE_ZAR:,}")
    print(f"   Schedule:   Daily at 17:30 SAST (after JSE close)")
    print(f"   GARCH:      {'arch library ✓' if GARCH_AVAILABLE else 'EWMA fallback'}")

    # Run immediately on startup
    job()

    # Schedule once daily at 17:30 SAST (right after JSE closes at 17:00).
    # Why daily, not every 4 hours?
    #   yfinance daily data only refreshes once per day. Running every
    #   4 hours produces identical signals from stale data — wasted compute.
    schedule.every().day.at("17:30").do(job)

    while True:
        schedule.run_pending()
        time.sleep(30)
