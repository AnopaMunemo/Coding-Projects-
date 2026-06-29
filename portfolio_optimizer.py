"""
portfolio_optimizer.py — Quantitative portfolio engine.

Modules
───────
  RegimeDetector     — 3-state Hidden Markov Model (Bull / Bear / Sideways)
                       with volatility-quantile fallback if hmmlearn absent
  PortfolioOptimizer — Sharpe-maximising mean-variance optimizer with
                       risk-appetite / time-horizon constraints, pre-made
                       theme portfolios, walk-forward validation, and
                       Monte Carlo probability forecasting
  build_portfolio()  — Top-level entry point; accepts a PortfolioRequest
                       and a data bundle from DataFeedOrchestrator.run()
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize

warnings.filterwarnings("ignore", category=FutureWarning)

# ── 10-year US Treasury Bond rate — strict filter baseline ───────────────────
RISK_FREE_RATE_10Y = 0.0445   # ~4.45% — assets below this are excluded

try:
    from hmmlearn.hmm import GaussianHMM
    _HMM_AVAILABLE = True
except ImportError:
    _HMM_AVAILABLE = False

logger = logging.getLogger("portfolio_optimizer")


# ══════════════════════════════════════════════════════════════════════════════
# Configuration & request dataclasses
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PortfolioRequest:
    budget_usd:           float   # total investable capital in USD
    risk_appetite:        str     # 'Conservative' | 'Moderate' | 'Aggressive'
    time_horizon_months:  int     # investment horizon in months
    preferred_stock_type: str     # 'Tech' | 'Value' | 'Dividend' | 'Emerging' | 'Balanced'
    target_return:        float   = 0.15   # used by Monte Carlo (e.g. 0.15 = 15%)
    risk_free_rate:       float   = 0.045  # US 3-month T-bill proxy
    monte_carlo_sims:     int     = 10_000
    max_single_position:  float   = 0.20   # hard cap per ticker

    def __post_init__(self) -> None:
        valid_ra = {"Conservative", "Moderate", "Aggressive"}
        valid_st = {
            # US theme universes
            "Tech", "Value", "Dividend", "Emerging", "Balanced",
            # SA/JSE universes
            "JSE Large Cap", "JSE Banks", "JSE Mining",
            "JSE ETFs", "EasyEquities", "SA Balanced",
        }
        if self.risk_appetite not in valid_ra:
            raise ValueError(f"risk_appetite must be one of {valid_ra}")
        if self.preferred_stock_type not in valid_st:
            raise ValueError(f"preferred_stock_type must be one of {valid_st}")


@dataclass
class _Constraints:
    """Internal allocation bands derived from risk appetite + horizon."""
    min_equity:       float
    max_equity:       float
    min_fi:           float
    max_fi:           float
    max_single_pos:   float


@dataclass
class PortfolioAllocation:
    ticker:        str
    asset_class:   str     # 'equity' | 'fixed_income'
    weight:        float
    dollar_amount: float
    shares:        float
    price:         float
    rationale:     str
    beta:          float = 1.0   # systematic risk vs market proxy
    treynor_ratio: float = 0.0   # (E[R] - Rf) / Beta
    expected_return: float = 0.0 # annualised expected return


# ── Beta & Treynor utilities ──────────────────────────────────────────────────

def calculate_beta(asset_returns: pd.Series, market_returns: pd.Series) -> float:
    """Beta = Cov(asset, market) / Var(market). Returns 1.0 on insufficient data."""
    aligned = pd.concat([asset_returns, market_returns], axis=1).dropna()
    if len(aligned) < 10:
        return 1.0
    cov_mat = aligned.cov().values
    market_var = cov_mat[1, 1]
    return float(cov_mat[0, 1] / market_var) if market_var > 1e-12 else 1.0


def calculate_treynor(ann_return: float, beta: float, rf: float = RISK_FREE_RATE_10Y) -> float:
    """Treynor Ratio = (E[R] - Rf) / Beta."""
    return (ann_return - rf) / beta if abs(beta) > 1e-9 else 0.0


@dataclass
class WalkForwardResult:
    windows:           int
    mean_oos_return:   float
    mean_oos_sharpe:   float
    max_drawdown:      float
    win_rate:          float
    monthly_returns:   List[float]


@dataclass
class PortfolioResult:
    request:             PortfolioRequest
    allocations:         List[PortfolioAllocation]
    total_invested:      float
    expected_return:     float   # annualised
    expected_volatility: float   # annualised
    sharpe_ratio:        float
    equity_weight:       float
    fi_weight:           float
    regime:              str
    regime_confidence:   float
    monte_carlo_prob:    float   # P(return ≥ target over horizon)
    mc_median_return:    float
    mc_p10:              float   # 10th-percentile outcome
    mc_p90:              float   # 90th-percentile outcome
    walk_forward:        Optional[WalkForwardResult]
    summary:             str


# ══════════════════════════════════════════════════════════════════════════════
# Pre-made theme universes
# ══════════════════════════════════════════════════════════════════════════════

THEME_UNIVERSES: Dict[str, List[str]] = {
    # ── US theme universes ────────────────────────────────────────────────────
    "Tech": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "META",
        "AMD",  "AVGO", "QCOM", "ORCL",  "CRM",
        "ADBE", "SNOW", "NOW",  "PLTR",  "TSM",
    ],
    "Value": [
        "BRK-B", "JPM", "BAC",  "WFC",  "GS",
        "XOM",   "CVX", "COP",  "JNJ",  "PFE",
        "KO",    "PG",  "MO",   "T",    "VZ",
    ],
    "Dividend": [
        "KO",   "PEP", "JNJ",  "PG",   "MCD",
        "MMM",  "T",   "VZ",   "MO",   "PM",
        "O",    "WPC", "D",    "SO",   "ABBV",
    ],
    "Emerging": [
        "EEM",  "VWO",  "INDA", "EWT",  "EWZ",
        "MCHI", "KWEB", "EWY",  "EWJ",  "GXC",
    ],
    "Balanced": [
        "AAPL", "MSFT", "JPM",  "JNJ",  "KO",
        "XOM",  "V",    "PG",   "BRK-B","GOOGL",
        "HD",   "UNH",  "CVX",  "ABBV", "TMO",
    ],
    # ── JSE / South African universes ─────────────────────────────────────────
    "JSE Large Cap": [
        "NPN.JO", "SOL.JO", "SHP.JO", "FSR.JO", "CPI.JO",
        "DSY.JO", "MTN.JO", "VOD.JO", "SLM.JO", "ABG.JO",
    ],
    "JSE Banks": [
        "FSR.JO", "SBK.JO", "ABG.JO", "NED.JO", "INL.JO",
        "CPI.JO", "DSY.JO", "SLM.JO",
    ],
    "JSE Mining": [
        "AGL.JO", "BHP.JO", "GFI.JO", "ANG.JO", "IMP.JO",
        "SOL.JO", "SSW.JO", "AMS.JO",
    ],
    "JSE ETFs": [
        "STX40.JO", "STXSWIX.JO", "STXWDM.JO", "STXNDX.JO", "PTXSPY.JO",
    ],
    "EasyEquities": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
        "V",    "META", "TSLA", "JPM",   "JNJ",
    ],
    "SA Balanced": [
        "NPN.JO", "FSR.JO", "SHP.JO", "STX40.JO", "STXWDM.JO", "SOL.JO",
    ],
}

# Fixed-income instruments per risk appetite.
# SA bonds are listed first so the JSE synthetic bundle resolves them immediately;
# US ETFs remain as fallback for live US-data bundles.
FI_UNIVERSE: Dict[str, List[str]] = {
    "Conservative": ["R186.JO", "R2030.JO", "R213.JO", "R214.JO", "STXGOV.JO",
                     "SHY", "IEF", "TLT", "AGG", "BND"],
    "Moderate":     ["R2030.JO", "R213.JO", "STXGOV.JO",
                     "IEF", "AGG", "LQD"],
    "Aggressive":   ["STXGOV.JO", "NGOVSUS",
                     "HYG", "LQD"],
}


# ══════════════════════════════════════════════════════════════════════════════
# Regime detector (HMM + fallback)
# ══════════════════════════════════════════════════════════════════════════════

class RegimeDetector:
    """
    3-state market regime classifier.

    Primary:  GaussianHMM on [daily_return, vol_5d, vol_20d]
    Fallback: volatility-quantile bucketing when hmmlearn is absent or
              the model fails to converge.

    Regimes
    ───────
    0 → Bull    (low vol, positive drift)
    1 → Bear    (high vol, negative drift)
    2 → Sideways (moderate vol, near-zero drift)
    """

    LABELS = {0: "Bull", 1: "Bear", 2: "Sideways"}

    def __init__(self, n_regimes: int = 3, random_state: int = 42) -> None:
        self.n_regimes    = n_regimes
        self.random_state = random_state
        self._model: Optional[Any] = None
        self._regime_map: Dict[int, int] = {}   # raw HMM state → canonical label
        self._log = logging.getLogger("portfolio_optimizer.regime")

    # ── Feature preparation ────────────────────────────────────────────────

    @staticmethod
    def _build_features(returns: pd.Series) -> np.ndarray:
        r     = returns.dropna()
        vol5  = r.rolling(5).std().fillna(r.std())
        vol20 = r.rolling(20).std().fillna(r.std())
        feats = np.column_stack([r.values, vol5.values, vol20.values])
        return feats[~np.isnan(feats).any(axis=1)]

    # ── Fit ────────────────────────────────────────────────────────────────

    def fit(self, returns: pd.Series) -> "RegimeDetector":
        feats = self._build_features(returns)
        if len(feats) < 60:
            self._log.warning("Insufficient data for HMM; using fallback")
            self._model = None
            return self

        if _HMM_AVAILABLE:
            try:
                model = GaussianHMM(
                    n_components=self.n_regimes,
                    covariance_type="full",
                    n_iter=200,
                    random_state=self.random_state,
                )
                model.fit(feats)
                self._model = model
                self._map_states(returns)
                self._log.info("HMM fitted: %d states, %d obs", self.n_regimes, len(feats))
            except Exception as exc:
                self._log.warning("HMM fit failed (%s); using fallback", exc)
                self._model = None
        else:
            self._log.info("hmmlearn not installed — using volatility-quantile regime")
            self._model = None
        return self

    def _map_states(self, returns: pd.Series) -> None:
        """Map raw HMM states to canonical Bull/Bear/Sideways labels by
        sorting states on mean return (highest → Bull, lowest → Bear)."""
        feats  = self._build_features(returns)
        states = self._model.predict(feats)
        state_means = {s: feats[states == s, 0].mean() for s in range(self.n_regimes)}
        sorted_states = sorted(state_means, key=state_means.get, reverse=True)
        # highest mean return = Bull(0), lowest = Bear(1), middle = Sideways(2)
        canonical = [0, 1, 2] if self.n_regimes == 3 else list(range(self.n_regimes))
        self._regime_map = {raw: can for raw, can in zip(sorted_states, canonical)}

    # ── Predict ───────────────────────────────────────────────────────────

    def predict(self, returns: pd.Series) -> Tuple[str, float]:
        """
        Return (regime_label, confidence).
        confidence = posterior probability of the predicted state.
        """
        feats = self._build_features(returns)
        if len(feats) == 0:
            return "Sideways", 0.5

        if self._model is not None:
            try:
                log_probs    = self._model.predict_proba(feats)
                raw_state    = int(np.argmax(log_probs[-1]))
                canonical    = self._regime_map.get(raw_state, raw_state)
                confidence   = float(log_probs[-1, raw_state])
                return self.LABELS.get(canonical, "Sideways"), confidence
            except Exception:
                pass

        # Volatility-quantile fallback
        vol = feats[-20:, 1].mean()           # recent 20-bar vol
        all_vol = feats[:, 1]
        q33, q67 = np.quantile(all_vol, [0.33, 0.67])
        recent_ret = feats[-20:, 0].mean()

        if vol <= q33 and recent_ret > 0:
            return "Bull", 0.65
        elif vol >= q67 or recent_ret < -0.001:
            return "Bear", 0.65
        else:
            return "Sideways", 0.55


# ══════════════════════════════════════════════════════════════════════════════
# Portfolio optimizer
# ══════════════════════════════════════════════════════════════════════════════

class PortfolioOptimizer:
    """
    Mean-variance optimizer with institutional overlays:

    • Regime-adjusted expected returns (HMM state weights)
    • Risk-appetite + time-horizon allocation bands
    • Walk-forward out-of-sample validation
    • Bootstrap Monte Carlo probability forecasting
    """

    def __init__(
        self,
        request: PortfolioRequest,
        data_bundle: Dict[str, Any],
    ) -> None:
        self.req     = request
        self.bundle  = data_bundle
        self._log    = logging.getLogger("portfolio_optimizer.engine")
        self._constraints = self._build_constraints()
        self._port_ret: pd.Series = pd.Series(dtype=float)   # set during build()

    # ── Constraints ────────────────────────────────────────────────────────

    def _build_constraints(self) -> _Constraints:
        base = {
            "Conservative": _Constraints(0.20, 0.40, 0.50, 0.75, 0.10),
            "Moderate":     _Constraints(0.40, 0.65, 0.25, 0.50, 0.15),
            "Aggressive":   _Constraints(0.70, 0.95, 0.05, 0.25, 0.25),
        }[self.req.risk_appetite]

        h = self.req.time_horizon_months
        if h < 6:
            # Short horizon → more defensive
            base = _Constraints(
                min_equity=max(0.10, base.min_equity - 0.15),
                max_equity=max(0.20, base.max_equity - 0.20),
                min_fi=min(0.80, base.min_fi + 0.20),
                max_fi=min(0.85, base.max_fi + 0.15),
                max_single_pos=min(0.10, base.max_single_pos),
            )
        elif h > 36:
            # Long horizon → allow more equity
            base = _Constraints(
                min_equity=base.min_equity,
                max_equity=min(0.95, base.max_equity + 0.10),
                min_fi=max(0.05, base.min_fi - 0.10),
                max_fi=max(0.10, base.max_fi - 0.10),
                max_single_pos=base.max_single_pos,
            )
        return base

    # ── Universe selection ─────────────────────────────────────────────────

    def _select_equity_universe(self) -> List[str]:
        """
        Merge theme tickers with value-screened tickers from data_feed.
        Value-screened stocks (from Graham/Magic Formula) always get included.
        """
        screened = self.bundle.get("equity", {}).get("screened_universe", pd.DataFrame())
        screened_tickers: List[str] = []
        if not screened.empty and "ticker" in screened.columns:
            # Top 8 from value screen
            screened_tickers = screened["ticker"].head(8).tolist()

        theme_tickers = THEME_UNIVERSES.get(self.req.preferred_stock_type, [])

        # Merge, preserve order, keep value screen up front
        combined = screened_tickers + [t for t in theme_tickers if t not in screened_tickers]

        # Filter to tickers we actually have history for
        available = set(self.bundle.get("equity", {}).get("histories", {}).keys())
        filtered  = [t for t in combined if t in available]

        # Fall back to theme if history is sparse
        if len(filtered) < 5:
            filtered = [t for t in theme_tickers if t in available] or list(available)[:15]

        return filtered[:15]   # cap at 15 equity positions

    def _select_fi_universe(self) -> List[str]:
        fi_tickers = FI_UNIVERSE.get(self.req.risk_appetite, ["AGG"])
        available  = set(
            self.bundle.get("fixed_income", {}).get("etf_histories", {}).keys()
        )
        return [t for t in fi_tickers if t in available] or fi_tickers[:2]

    # ── Returns matrix ────────────────────────────────────────────────────

    def _returns_matrix(self, tickers: List[str], source: str = "equity") -> pd.DataFrame:
        """Align daily returns for a list of tickers on a common date index."""
        frames: Dict[str, pd.Series] = {}

        if source == "equity":
            hist_map = self.bundle.get("equity", {}).get("histories", {})
        else:
            hist_map = self.bundle.get("fixed_income", {}).get("etf_histories", {})

        for t in tickers:
            df = hist_map.get(t)
            if df is None or df.empty:
                continue
            close = df["Close"] if "Close" in df.columns else df.iloc[:, 3]
            frames[t] = close.pct_change().rename(t)

        if not frames:
            return pd.DataFrame()

        out = pd.concat(frames.values(), axis=1).dropna(how="all")
        return out.fillna(0)

    # ── Regime detection ──────────────────────────────────────────────────

    def _detect_regime(self, returns: pd.DataFrame) -> Tuple[str, float]:
        if returns.empty:
            return "Sideways", 0.5
        # Use equal-weighted portfolio return as the regime signal
        port_ret = returns.mean(axis=1)
        detector = RegimeDetector()
        detector.fit(port_ret)
        return detector.predict(port_ret)

    # ── Optimisation ──────────────────────────────────────────────────────

    def _optimise_equity_weights(
        self,
        returns: pd.DataFrame,
        regime: str,
    ) -> np.ndarray:
        """
        Maximise Sharpe ratio subject to:
          • weights ≥ 0  (long-only)
          • sum(weights) = 1
          • each weight ≤ max_single_pos
        Returns weight vector aligned with returns.columns.
        """
        n = len(returns.columns)
        if n == 0:
            return np.array([])

        # Regime overlay: shrink expected returns toward zero in Bear regime
        mean_ret  = returns.mean().values * 252
        cov_mat   = returns.cov().values  * 252

        shrink = {"Bull": 1.0, "Sideways": 0.85, "Bear": 0.60}.get(regime, 0.85)
        adj_mean = mean_ret * shrink + (1 - shrink) * mean_ret.mean()

        rfr = self.req.risk_free_rate

        def neg_sharpe(w: np.ndarray) -> float:
            pr = float(np.dot(w, adj_mean))
            pv = float(np.sqrt(np.dot(w, cov_mat @ w) + 1e-12))
            return -(pr - rfr) / pv

        def neg_sharpe_grad(w: np.ndarray) -> np.ndarray:
            pr  = float(np.dot(w, adj_mean))
            pv  = float(np.sqrt(np.dot(w, cov_mat @ w) + 1e-12))
            sr  = (pr - rfr) / pv
            dpr = adj_mean            # d(portfolio return)/dw
            dpv = cov_mat @ w / pv    # d(portfolio vol)/dw
            # d(Sharpe)/dw = (dpr - sr·dpv) / pv  →  negate for minimisation
            return -(dpr - sr * dpv) / pv

        w0 = np.full(n, 1.0 / n)
        cap = min(self.req.max_single_position, self._constraints.max_single_pos)
        bounds      = [(0.01, cap)] * n
        constraints = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]

        result = minimize(
            neg_sharpe,
            w0,
            jac=neg_sharpe_grad,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"maxiter": 500, "ftol": 1e-10},
        )

        if result.success:
            weights = np.clip(result.x, 0, cap)
            return weights / weights.sum()

        # Fall back to inverse-volatility (risk-parity) weights
        self._log.warning("Sharpe optimisation did not converge; using risk-parity")
        vols = returns.std().values + 1e-9
        inv  = 1.0 / vols
        return inv / inv.sum()

    def _fi_weights(self, fi_returns: pd.DataFrame) -> np.ndarray:
        """Equal weight across selected fixed-income ETFs (simple, low-cost)."""
        n = len(fi_returns.columns)
        return np.full(n, 1.0 / n) if n > 0 else np.array([])

    # ── Walk-forward ──────────────────────────────────────────────────────

    def walk_forward_test(
        self,
        returns: pd.DataFrame,
        train_days: int = 252,
        test_days:  int = 21,
    ) -> WalkForwardResult:
        """
        Expanding-window walk-forward:
        - Fit optimiser on first `train_days`
        - Evaluate on next `test_days` (out-of-sample)
        - Slide forward by `test_days`, repeat
        """
        if len(returns) < train_days + test_days:
            return WalkForwardResult(0, 0.0, 0.0, 0.0, 0.0, [])

        monthly_rets: List[float] = []
        start = train_days

        while start + test_days <= len(returns):
            train = returns.iloc[:start]
            test  = returns.iloc[start: start + test_days]

            regime, _ = self._detect_regime(train)
            w = self._optimise_equity_weights(train, regime)

            if len(w) == len(test.columns):
                oos_daily = (test * w).sum(axis=1)
                oos_ret   = float((1 + oos_daily).prod() - 1)
                monthly_rets.append(oos_ret)

            start += test_days

        if not monthly_rets:
            return WalkForwardResult(0, 0.0, 0.0, 0.0, 0.0, [])

        arr = np.array(monthly_rets)

        # Max drawdown over OOS series
        cumulative = np.cumprod(1 + arr)
        peak       = np.maximum.accumulate(cumulative)
        drawdowns  = (cumulative - peak) / (peak + 1e-12)
        max_dd     = float(drawdowns.min())

        mean_ret  = float(arr.mean())
        mean_sharpe = float(
            (arr.mean() * 12 - self.req.risk_free_rate)
            / (arr.std() * np.sqrt(12) + 1e-9)
        )
        win_rate  = float((arr > 0).mean())

        self._log.info(
            "Walk-forward: %d windows | mean OOS %.2f%% | Sharpe %.2f | MaxDD %.2f%%",
            len(monthly_rets), mean_ret * 100, mean_sharpe, max_dd * 100,
        )

        return WalkForwardResult(
            windows=len(monthly_rets),
            mean_oos_return=round(mean_ret, 6),
            mean_oos_sharpe=round(mean_sharpe, 4),
            max_drawdown=round(max_dd, 6),
            win_rate=round(win_rate, 4),
            monthly_returns=monthly_rets,
        )

    # ── Monte Carlo ───────────────────────────────────────────────────────

    def monte_carlo(
        self,
        portfolio_returns: pd.Series,
        target: float,
        months: int,
    ) -> Tuple[float, float, float, float]:
        """
        Bootstrap-resample monthly returns N times over `months`.

        Returns
        ───────
        (probability, median_return, p10, p90)

        probability = fraction of paths where cumulative return ≥ target
        """
        if portfolio_returns.empty:
            return 0.5, 0.0, -0.5, 0.5

        # Resample to monthly
        monthly = (1 + portfolio_returns).resample("ME").prod() - 1
        monthly = monthly.dropna()

        if len(monthly) < 6:
            # Not enough monthly obs — use daily-bootstrapped monthly proxy
            daily   = portfolio_returns.dropna().values
            monthly = pd.Series(
                [(1 + np.random.choice(daily, 21, replace=True)).prod() - 1
                 for _ in range(max(24, len(daily) // 21))]
            )

        rng         = np.random.default_rng(seed=42)
        n           = self.req.monte_carlo_sims
        samples     = rng.choice(monthly.values, size=(n, months), replace=True)
        final_rets  = np.prod(1 + samples, axis=1) - 1

        prob  = float(np.mean(final_rets >= target))
        med   = float(np.median(final_rets))
        p10   = float(np.percentile(final_rets, 10))
        p90   = float(np.percentile(final_rets, 90))
        return prob, med, p10, p90

    def probability_matrix(
        self,
        months_list:  List[int],
        targets_list: List[float],
        port_ret:     Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Build a full probability matrix for the dashboard's Probability Module.

        Rows    = target returns (e.g. 0.05, 0.10, 0.20 …)
        Columns = hold horizons in months
        Cell    = P(cumulative return ≥ target over that horizon)

        Drives the "Hold for X months for a Y% likelihood of Z% profit" panel.
        Call after build() so the cached portfolio return series is available.
        """
        series = port_ret if port_ret is not None else self._port_ret
        if series is None or series.empty:
            return pd.DataFrame()

        matrix: Dict[int, List[float]] = {}
        for m in months_list:
            col: List[float] = []
            for tgt in targets_list:
                prob, *_ = self.monte_carlo(series, tgt, m)
                col.append(round(prob, 4))
            matrix[m] = col

        df = pd.DataFrame(
            matrix,
            index=[f"{t:.0%}" for t in targets_list],
        )
        df.index.name   = "target_return"
        df.columns.name = "hold_months"
        return df

    # ── Build result ──────────────────────────────────────────────────────

    def build(self) -> PortfolioResult:
        self._log.info(
            "Building portfolio: %s | %s | %d months | $%.0f",
            self.req.preferred_stock_type,
            self.req.risk_appetite,
            self.req.time_horizon_months,
            self.req.budget_usd,
        )

        eq_tickers = self._select_equity_universe()
        fi_tickers = self._select_fi_universe()

        self._log.info("Equity universe: %s", eq_tickers)
        self._log.info("FI universe: %s", fi_tickers)

        eq_ret = self._returns_matrix(eq_tickers, source="equity")
        fi_ret = self._returns_matrix(fi_tickers, source="fi")

        # ── Regime ───────────────────────────────────────────────────────
        regime, regime_conf = self._detect_regime(eq_ret)
        self._log.info("Detected regime: %s (confidence %.0f%%)", regime, regime_conf * 100)

        # ── Filter: exclude assets below 10-year TB rate ─────────────────
        # Compute equal-weighted market proxy for beta calculation
        market_proxy = eq_ret.mean(axis=1) if not eq_ret.empty else pd.Series(dtype=float)
        # Drop tickers whose annualised expected return < RISK_FREE_RATE_10Y
        if not eq_ret.empty:
            ann_means = eq_ret.mean() * 252
            passed = [t for t in eq_ret.columns if ann_means[t] >= RISK_FREE_RATE_10Y]
            if len(passed) >= 3:
                eq_ret = eq_ret[passed]
                eq_tickers = list(eq_ret.columns)
                self._log.info("TB-rate filter: %d/%d equity tickers passed (≥%.2f%% ann. return)",
                               len(passed), len(ann_means), RISK_FREE_RATE_10Y * 100)
            else:
                self._log.info("TB-rate filter: too few assets passed; keeping all (synthetic/bear market data)")

        # ── Equity weights ────────────────────────────────────────────────
        eq_weights = (
            self._optimise_equity_weights(eq_ret, regime)
            if not eq_ret.empty else np.array([])
        )
        fi_weights = (
            self._fi_weights(fi_ret)
            if not fi_ret.empty else np.array([])
        )

        # ── Top-level split (equity vs fixed income) ──────────────────────
        c = self._constraints
        # Blend: Bull → more equity, Bear → more FI
        regime_equity_bias = {"Bull": 0.05, "Sideways": 0.0, "Bear": -0.10}.get(regime, 0.0)
        target_eq_frac = float(np.clip(
            (c.min_equity + c.max_equity) / 2 + regime_equity_bias,
            c.min_equity, c.max_equity,
        ))
        target_fi_frac = 1.0 - target_eq_frac

        self._log.info(
            "Allocation split → equity %.0f%%  FI %.0f%%",
            target_eq_frac * 100, target_fi_frac * 100,
        )

        # ── Dollar allocations ─────────────────────────────────────────────
        eq_budget = self.req.budget_usd * target_eq_frac
        fi_budget = self.req.budget_usd * target_fi_frac

        allocations: List[PortfolioAllocation] = []
        screened_df = self.bundle.get("equity", {}).get("screened_universe", pd.DataFrame())

        for ticker, w in zip(eq_tickers, eq_weights):
            dollar = eq_budget * float(w)
            hist = self.bundle["equity"]["histories"].get(ticker)
            price = float(hist["Close"].iloc[-1]) if hist is not None and not hist.empty else 0.0
            shares = (dollar / price) if price > 0 else 0.0

            # Beta & Treynor
            ticker_ret = eq_ret[ticker] if ticker in eq_ret.columns else pd.Series(dtype=float)
            beta_val = calculate_beta(ticker_ret, market_proxy) if not ticker_ret.empty and not market_proxy.empty else 1.0
            ann_ret_ticker = float(ticker_ret.mean() * 252) if not ticker_ret.empty else 0.0
            treynor_val = calculate_treynor(ann_ret_ticker, beta_val)

            if not screened_df.empty and "ticker" in screened_df.columns:
                row = screened_df[screened_df["ticker"] == ticker]
                if not row.empty:
                    pe  = row["trailingPE"].values[0]
                    mos = row.get("grahamMoS", pd.Series([None])).values[0]
                    rationale = f"Value-screened: P/E={pe:.1f}" if pe else "Value screen"
                    if mos and not np.isnan(mos):
                        rationale += f", Graham MoS={mos:.0%}"
                else:
                    rationale = f"Theme: {self.req.preferred_stock_type}"
            else:
                rationale = f"Theme: {self.req.preferred_stock_type}"

            allocations.append(PortfolioAllocation(
                ticker=ticker, asset_class="equity",
                weight=round(float(w) * target_eq_frac, 6),
                dollar_amount=round(dollar, 2),
                shares=round(shares, 4),
                price=round(price, 4),
                rationale=rationale,
                beta=round(beta_val, 4),
                treynor_ratio=round(treynor_val, 4),
                expected_return=round(ann_ret_ticker, 6),
            ))

        for ticker, w in zip(fi_tickers, fi_weights):
            dollar = fi_budget * float(w)
            hist   = self.bundle["fixed_income"]["etf_histories"].get(ticker)
            price  = float(hist["Close"].iloc[-1]) if hist is not None and not hist.empty else 0.0
            shares = (dollar / price) if price > 0 else 0.0

            etf_df = self.bundle["fixed_income"].get("etf_yields", pd.DataFrame())
            ytm_str = ""
            if not etf_df.empty and "ticker" in etf_df.columns:
                row = etf_df[etf_df["ticker"] == ticker]
                if not row.empty:
                    ytm = row["ytm_proxy"].values[0]
                    ytm_str = f", YTM≈{ytm:.2%}" if ytm else ""

            fi_ticker_ret = fi_ret[ticker] if ticker in fi_ret.columns else pd.Series(dtype=float)
            fi_ann_ret = float(fi_ticker_ret.mean() * 252) if not fi_ticker_ret.empty else 0.0
            allocations.append(PortfolioAllocation(
                ticker=ticker, asset_class="fixed_income",
                weight=round(float(w) * target_fi_frac, 6),
                dollar_amount=round(dollar, 2),
                shares=round(shares, 4),
                price=round(price, 4),
                rationale=f"{self.req.risk_appetite} fixed-income{ytm_str}",
                beta=round(calculate_beta(fi_ticker_ret, market_proxy) if not fi_ticker_ret.empty and not market_proxy.empty else 0.3, 4),
                treynor_ratio=round(calculate_treynor(fi_ann_ret, 0.3), 4),
                expected_return=round(fi_ann_ret, 6),
            ))

        # ── Portfolio analytics ───────────────────────────────────────────
        port_ret = pd.Series(dtype=float)
        if not eq_ret.empty and len(eq_weights) == len(eq_ret.columns):
            port_ret = (eq_ret * eq_weights).sum(axis=1) * target_eq_frac
        if not fi_ret.empty and len(fi_weights) == len(fi_ret.columns):
            fi_series = (fi_ret * fi_weights).sum(axis=1) * target_fi_frac
            port_ret  = port_ret.add(fi_series, fill_value=0)

        self._port_ret = port_ret   # cache for probability_matrix()

        ann_ret = float(port_ret.mean() * 252) if not port_ret.empty else 0.0
        ann_vol = float(port_ret.std() * np.sqrt(252)) if not port_ret.empty else 0.01
        sharpe  = (ann_ret - self.req.risk_free_rate) / (ann_vol + 1e-9)

        # ── Walk-forward ──────────────────────────────────────────────────
        wf_result: Optional[WalkForwardResult] = None
        if not eq_ret.empty and len(eq_ret) >= 300:
            wf_result = self.walk_forward_test(eq_ret)
        else:
            self._log.info("Insufficient history for walk-forward (need 300+ days)")

        # ── Monte Carlo ───────────────────────────────────────────────────
        mc_prob, mc_med, mc_p10, mc_p90 = self.monte_carlo(
            port_ret, self.req.target_return, self.req.time_horizon_months
        )
        self._log.info(
            "Monte Carlo: P(ret≥%.0f%%) = %.1f%% | median=%.1f%% | [p10=%.1f%%, p90=%.1f%%]",
            self.req.target_return * 100,
            mc_prob * 100, mc_med * 100, mc_p10 * 100, mc_p90 * 100,
        )

        # ── Summary text ──────────────────────────────────────────────────
        summary = (
            f"{self.req.risk_appetite} {self.req.preferred_stock_type} portfolio | "
            f"Regime: {regime} ({regime_conf:.0%} confidence) | "
            f"Expected return: {ann_ret:.1%} p.a. | Sharpe: {sharpe:.2f} | "
            f"P(≥{self.req.target_return:.0%} in {self.req.time_horizon_months}m) = "
            f"{mc_prob:.1%} | Median path: {mc_med:.1%}"
        )

        total_invested = sum(a.dollar_amount for a in allocations)

        return PortfolioResult(
            request=self.req,
            allocations=allocations,
            total_invested=round(total_invested, 2),
            expected_return=round(ann_ret, 6),
            expected_volatility=round(ann_vol, 6),
            sharpe_ratio=round(sharpe, 4),
            equity_weight=round(target_eq_frac, 4),
            fi_weight=round(target_fi_frac, 4),
            regime=regime,
            regime_confidence=round(regime_conf, 4),
            monte_carlo_prob=round(mc_prob, 4),
            mc_median_return=round(mc_med, 4),
            mc_p10=round(mc_p10, 4),
            mc_p90=round(mc_p90, 4),
            walk_forward=wf_result,
            summary=summary,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def build_portfolio(
    budget_usd:           float,
    risk_appetite:        str,
    time_horizon_months:  int,
    preferred_stock_type: str,
    data_bundle:          Dict[str, Any],
    target_return:        float = 0.15,
    risk_free_rate:       float = 0.045,
) -> PortfolioResult:
    """
    Convenience wrapper.  All parameters are validated inside PortfolioRequest.

    Example
    ───────
    from data_feed import DataFeedOrchestrator
    bundle = DataFeedOrchestrator().run()
    result = build_portfolio(50_000, 'Moderate', 12, 'Value', bundle)
    """
    req = PortfolioRequest(
        budget_usd=budget_usd,
        risk_appetite=risk_appetite,
        time_horizon_months=time_horizon_months,
        preferred_stock_type=preferred_stock_type,
        target_return=target_return,
        risk_free_rate=risk_free_rate,
    )
    return PortfolioOptimizer(req, data_bundle).build()


# ══════════════════════════════════════════════════════════════════════════════
# CLI smoke test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import json

    logging.basicConfig(level=logging.INFO)

    # Minimal stub bundle so the engine can run without data_feed
    print("Running portfolio_optimizer in standalone stub mode …")
    print("For real output, integrate with DataFeedOrchestrator.run()\n")

    stub_dates = pd.date_range("2021-01-01", periods=756, freq="B")

    def _fake_history(seed: int) -> pd.DataFrame:
        rng   = np.random.default_rng(seed)
        price = 100 * np.cumprod(1 + rng.normal(0.0003, 0.012, 756))
        df    = pd.DataFrame({
            "Open": price, "High": price * 1.005,
            "Low":  price * 0.995, "Close": price, "Volume": 1e6,
        }, index=stub_dates)
        df["Return"] = df["Close"].pct_change()
        return df

    stub_bundle = {
        "equity": {
            "histories": {t: _fake_history(i) for i, t in enumerate(
                ["AAPL","MSFT","GOOGL","JPM","XOM","JNJ","KO","BRK-B",
                 "NVDA","V","PG","META","CVX","UNH","HD"]
            )},
            "screened_universe": pd.DataFrame({
                "ticker": ["KO", "JPM", "XOM", "BRK-B"],
                "trailingPE": [22.1, 11.3, 13.5, 8.9],
                "grahamMoS":  [0.15, 0.22, 0.18, 0.30],
                "compositeScore": [0.82, 0.78, 0.71, 0.91],
            }),
        },
        "fixed_income": {
            "etf_histories": {t: _fake_history(100 + i) for i, t in
                              enumerate(["AGG", "IEF", "TLT"])},
            "etf_yields": pd.DataFrame({
                "ticker":    ["AGG", "IEF", "TLT"],
                "ytm_proxy": [0.045, 0.043, 0.042],
            }),
        },
    }

    result = build_portfolio(
        budget_usd=50_000,
        risk_appetite="Moderate",
        time_horizon_months=12,
        preferred_stock_type="Value",
        data_bundle=stub_bundle,
        target_return=0.12,
    )

    print("\n" + "═" * 70)
    print("PORTFOLIO RESULT")
    print("═" * 70)
    print(result.summary)
    print(f"\nTotal invested : ${result.total_invested:,.2f}")
    print(f"Equity weight  : {result.equity_weight:.0%}")
    print(f"FI weight      : {result.fi_weight:.0%}")
    print(f"Regime         : {result.regime} ({result.regime_confidence:.0%})")
    print(f"\nMonte Carlo ({result.request.monte_carlo_sims:,} sims over "
          f"{result.request.time_horizon_months} months):")
    print(f"  P(≥{result.request.target_return:.0%})  = {result.monte_carlo_prob:.1%}")
    print(f"  Median return = {result.mc_median_return:.1%}")
    print(f"  10th pct      = {result.mc_p10:.1%}")
    print(f"  90th pct      = {result.mc_p90:.1%}")

    if result.walk_forward:
        wf = result.walk_forward
        print(f"\nWalk-forward ({wf.windows} OOS windows):")
        print(f"  Mean OOS return = {wf.mean_oos_return:.2%}")
        print(f"  Mean OOS Sharpe = {wf.mean_oos_sharpe:.2f}")
        print(f"  Max drawdown    = {wf.max_drawdown:.2%}")
        print(f"  Win rate        = {wf.win_rate:.0%}")

    print("\nAllocations:")
    for a in result.allocations:
        print(
            f"  {a.ticker:<8} {a.asset_class:<14} "
            f"wt={a.weight:.2%}  ${a.dollar_amount:>8,.0f}  "
            f"{a.shares:.2f} shares @ ${a.price:.2f}  [{a.rationale}]"
        )
