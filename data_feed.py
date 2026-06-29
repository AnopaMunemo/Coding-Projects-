"""
data_feed.py — Institutional-grade backend data pipeline.

Provides three data domains in a single orchestrated bundle:

  1. EQUITY   — OHLCV history, full fundamental metrics, Graham/Buffett
                value screening, Magic Formula composite ranking
  2. FIXED INCOME — US Treasury yield curve (3M/5Y/10Y/30Y) +
                    bond-ETF YTM proxies (TLT, AGG, LQD, HYG, …)
  3. FOREX    — Daily, hourly, and 5-min tick data for majors/crosses/
                commodities; per-pair session analysis; optimal entry
                windows ranked by volatility × liquidity composite score

Language note
─────────────
Python handles analytics, screening, and backtesting well.
For *live* Forex order execution use MQL5 (MetaTrader 5) — it offers
native broker connectivity, sub-millisecond routing, and built-in
position management that Python cannot replicate. C++ with a FIX adapter
is the right choice for co-located HFT (< 1 ms round-trips).
"""

from __future__ import annotations

import logging
import os
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


# ══════════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-30s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("data_feed")


# ══════════════════════════════════════════════════════════════════════════════
# Configuration dataclasses
# ══════════════════════════════════════════════════════════════════════════════

def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.environ.get(name, default)))
    except Exception:
        return default


@dataclass
class RetryConfig:
    """Exponential-backoff parameters for all network calls.

    Overridable via env so offline / sandboxed runs don't stall on dead network:
      ATLAS_MAX_RETRIES   (default 3)  — attempts per call
      ATLAS_RETRY_BACKOFF (default 1.5s) — base backoff, doubles each retry
    e.g. `ATLAS_MAX_RETRIES=1 ATLAS_RETRY_BACKOFF=0` for an instant synthetic fallback.
    """
    max_attempts: int   = field(default_factory=lambda: _env_int("ATLAS_MAX_RETRIES", 3))
    base_backoff: float = field(default_factory=lambda: _env_float("ATLAS_RETRY_BACKOFF", 1.5))


@dataclass
class EquityConfig:
    """Universe definition and value-screen thresholds."""
    tickers: List[str] = field(default_factory=lambda: [
        # US Large-Cap — diversified sector basket
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META",
        "BRK-B", "JPM", "BAC", "GS",
        "JNJ", "UNH", "PFE", "ABBV", "MRK",
        "XOM", "CVX", "COP",
        "PG", "KO", "PEP", "WMT", "COST",
        "V", "MA", "PYPL",
        "HD", "LOW", "NKE",
        "LLY", "TMO", "DHR",
    ])
    historical_period:   str   = "3y"
    historical_interval: str   = "1d"

    # ── Graham/Buffett value-screen thresholds ──────────────────────────────
    max_pe_ratio:       float = 20.0   # trailing P/E
    max_pb_ratio:       float = 3.0    # price-to-book
    min_earnings_yield: float = 0.05   # E/P > 5 %
    min_roe:            float = 0.12   # return on equity > 12 %
    max_debt_to_equity: float = 2.0    # D/E < 2×


@dataclass
class FixedIncomeConfig:
    """Treasury tickers and bond-ETF universe."""
    treasury_tickers: Dict[str, str] = field(default_factory=lambda: {
        "3M":  "^IRX",   # 13-week T-bill yield (quoted in %)
        "5Y":  "^FVX",   # 5-year Treasury yield
        "10Y": "^TNX",   # 10-year Treasury yield
        "30Y": "^TYX",   # 30-year Treasury yield
    })
    bond_etfs: Dict[str, str] = field(default_factory=lambda: {
        "SHY": "iShares 1-3Y Treasury",
        "IEF": "iShares 7-10Y Treasury",
        "TLT": "iShares 20+Y Treasury",
        "AGG": "iShares Core US Aggregate",
        "BND": "Vanguard Total Bond Market",
        "LQD": "iBoxx Investment Grade Corp",
        "HYG": "iBoxx High Yield Corp",
        "EMB": "JP Morgan EM Bond",
    })
    historical_period:   str = "2y"
    historical_interval: str = "1d"


@dataclass
class ForexConfig:
    """Pair lists and resolution config for FX data."""
    majors: List[str] = field(default_factory=lambda: [
        "EURUSD=X", "GBPUSD=X", "USDJPY=X", "USDCHF=X",
        "AUDUSD=X", "USDCAD=X", "NZDUSD=X",
    ])
    crosses: List[str] = field(default_factory=lambda: [
        "EURGBP=X", "EURJPY=X", "GBPJPY=X",
        "AUDJPY=X", "CADJPY=X",
    ])
    commodities: List[str] = field(default_factory=lambda: [
        "XAUUSD=X",   # Gold — safe-haven / inflation hedge
        "XAGUSD=X",   # Silver
    ])
    # yfinance limits: 1h → 730 d max; 5m → 60 d max; 1m → 7 d max
    daily_period:    str = "3y"
    daily_interval:  str = "1d"
    hourly_period:   str = "60d"
    hourly_interval: str = "1h"
    tick_period:     str = "5d"
    tick_interval:   str = "5m"


@dataclass
class DataFeedConfig:
    """Top-level configuration object — pass a customised instance to override."""
    equity:       EquityConfig       = field(default_factory=EquityConfig)
    fixed_income: FixedIncomeConfig  = field(default_factory=FixedIncomeConfig)
    forex:        ForexConfig        = field(default_factory=ForexConfig)
    retry:        RetryConfig        = field(default_factory=RetryConfig)


# ══════════════════════════════════════════════════════════════════════════════
# Retry helper
# ══════════════════════════════════════════════════════════════════════════════

def _with_retry(
    fn: Any,
    *args: Any,
    cfg: RetryConfig,
    label: str = "",
    **kwargs: Any,
) -> Any:
    """
    Call fn(*args, **kwargs) with exponential-backoff retry.
    Returns None (never raises) so callers can do a simple null-check.
    """
    backoff = cfg.base_backoff
    for attempt in range(1, cfg.max_attempts + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if attempt == cfg.max_attempts:
                logger.warning(
                    "FAILED [%s] after %d attempt(s) — %s", label, attempt, exc
                )
                return None
            logger.debug(
                "Retry %d/%d [%s] — %s", attempt, cfg.max_attempts, label, exc
            )
            time.sleep(backoff)
            backoff *= 2
    return None


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse MultiIndex columns produced by yf.download (single ticker)."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


# ══════════════════════════════════════════════════════════════════════════════
# EquityDataProvider
# ══════════════════════════════════════════════════════════════════════════════

class EquityDataProvider:
    """
    Fetches OHLCV history with technical overlays, full fundamental
    metrics, and applies a multi-factor value screen to rank the universe.
    """

    # All info-dict fields we want to capture
    _FUNDAMENTAL_FIELDS: Tuple[str, ...] = (
        "trailingPE", "forwardPE", "priceToBook",
        "trailingEps", "forwardEps",
        "returnOnEquity", "returnOnAssets",
        "debtToEquity", "currentRatio", "quickRatio",
        "profitMargins", "grossMargins", "operatingMargins",
        "revenueGrowth", "earningsGrowth",
        "dividendYield", "trailingAnnualDividendYield",
        "marketCap", "enterpriseValue",
        "enterpriseToRevenue", "enterpriseToEbitda",
        "bookValue", "priceToSalesTrailing12Months",
        "freeCashflow", "operatingCashflow",
        "totalDebt", "totalCash",
        "beta", "52WeekChange",
        "shortRatio", "shortPercentOfFloat",
        "heldPercentInstitutions", "heldPercentInsiders",
        "sharesOutstanding",
    )

    def __init__(self, config: EquityConfig, retry: RetryConfig) -> None:
        self.cfg   = config
        self.retry = retry
        self._log  = logging.getLogger("data_feed.equity")

    # ── History ────────────────────────────────────────────────────────────

    def fetch_history(self, ticker: str) -> Optional[pd.DataFrame]:
        """Return OHLCV DataFrame with technical indicators appended."""
        self._log.debug("History: %s", ticker)
        raw = _with_retry(
            yf.download,
            ticker,
            period=self.cfg.historical_period,
            interval=self.cfg.historical_interval,
            auto_adjust=True,
            progress=False,
            cfg=self.retry,
            label=f"equity_hist:{ticker}",
        )
        if raw is None or raw.empty:
            self._log.warning("No history — %s", ticker)
            return None
        df = _flatten_columns(raw.copy())
        df = self._add_indicators(df)
        df.attrs["ticker"] = ticker
        return df

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]

        # Trend MAs
        df["SMA_20"]  = close.rolling(20).mean()
        df["SMA_50"]  = close.rolling(50).mean()
        df["SMA_200"] = close.rolling(200).mean()

        # RSI (14)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        df["RSI_14"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

        # ATR (14) — used by regime detection in downstream modules
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        df["ATR_14"] = tr.rolling(14).mean()

        # Bollinger Bands (20, 2σ)
        std20        = close.rolling(20).std()
        df["BB_MID"] = df["SMA_20"]
        df["BB_UP"]  = df["SMA_20"] + 2 * std20
        df["BB_LO"]  = df["SMA_20"] - 2 * std20
        df["BB_PCT"] = (close - df["BB_LO"]) / (df["BB_UP"] - df["BB_LO"])

        # MACD (12, 26, 9)
        ema12          = close.ewm(span=12, adjust=False).mean()
        ema26          = close.ewm(span=26, adjust=False).mean()
        df["MACD"]     = ema12 - ema26
        df["MACD_sig"] = df["MACD"].ewm(span=9, adjust=False).mean()

        # Daily return & annualised realised volatility
        df["Return"]  = close.pct_change()
        df["Vol_20d"] = df["Return"].rolling(20).std() * np.sqrt(252)

        return df

    # ── Fundamentals ───────────────────────────────────────────────────────

    def fetch_fundamentals(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Pull fundamental info dict and derive value-screen metrics.
        Returns None gracefully if the ticker is unavailable.
        """
        self._log.debug("Fundamentals: %s", ticker)
        try:
            t    = yf.Ticker(ticker)
            info = _with_retry(
                lambda: t.info, cfg=self.retry, label=f"fund:{ticker}"
            )
        except Exception as exc:
            self._log.warning("Fundamentals failed — %s: %s", ticker, exc)
            return None

        if not info:
            return None

        row: Dict[str, Any] = {"ticker": ticker}

        for field_name in self._FUNDAMENTAL_FIELDS:
            row[field_name] = info.get(field_name)

        price = info.get("currentPrice") or info.get("regularMarketPrice")
        row["currentPrice"] = price

        # ── Derived value metrics ──────────────────────────────────────────

        pe   = row.get("trailingPE")
        eps  = row.get("trailingEps")
        bvps = row.get("bookValue")
        fcf  = row.get("freeCashflow")
        mktcap = row.get("marketCap")
        shares = row.get("sharesOutstanding")
        cash   = info.get("totalCash")
        debt   = info.get("totalDebt")

        # Earnings yield = 1 / P/E (Greenblatt Magic Formula component)
        row["earningsYield"] = (1.0 / pe) if pe and pe > 0 else None

        # Graham Number: sqrt(22.5 × EPS × BVPS)
        # Price below Graham Number → potentially undervalued
        if eps and eps > 0 and bvps and bvps > 0:
            row["grahamNumber"] = float(np.sqrt(22.5 * eps * bvps))
        else:
            row["grahamNumber"] = None

        # Margin of safety vs Graham Number (positive = trading below intrinsic)
        if row["grahamNumber"] and price and price > 0:
            row["grahamMoS"] = (row["grahamNumber"] - price) / row["grahamNumber"]
        else:
            row["grahamMoS"] = None

        # FCF yield — quality check on earnings sustainability
        row["fcfYield"] = (fcf / mktcap) if fcf and mktcap and mktcap > 0 else None

        # Net cash per share (Buffett net-net proxy)
        if all(v is not None for v in [cash, debt, shares]) and shares > 0:
            row["netCashPerShare"] = (cash - debt) / shares
        else:
            row["netCashPerShare"] = None

        # EV / EBIT proxy (if EBITDA available)
        ev    = row.get("enterpriseValue")
        evebt = row.get("enterpriseToEbitda")
        row["evToEbitda"] = evebt  # direct from info

        return row

    # ── Value screen ───────────────────────────────────────────────────────

    def screen_undervalued(
        self, fundamentals: List[Dict[str, Any]]
    ) -> pd.DataFrame:
        """
        Apply institutional multi-factor value screen and return a ranked
        DataFrame. Passing stocks must clear ALL threshold gates; ranking
        uses a composite of earnings yield, ROE, Graham MoS, and FCF yield.
        """
        df = pd.DataFrame([f for f in fundamentals if f is not None])
        if df.empty:
            return df

        mask = pd.Series(True, index=df.index)

        def _apply(col: str, op, threshold: float, fill: bool = False) -> None:
            nonlocal mask
            if col in df.columns:
                mask &= op(df[col], threshold).fillna(fill)

        _apply("trailingPE",      lambda s, t: s.between(0.01, t), self.cfg.max_pe_ratio,       False)
        _apply("priceToBook",     lambda s, t: s <= t,              self.cfg.max_pb_ratio,        False)
        _apply("earningsYield",   lambda s, t: s >= t,              self.cfg.min_earnings_yield,  False)
        _apply("returnOnEquity",  lambda s, t: s >= t,              self.cfg.min_roe,             False)
        _apply("debtToEquity",    lambda s, t: s <= t,              self.cfg.max_debt_to_equity,  True)

        screened = df[mask].copy()
        if screened.empty:
            self._log.warning("Value screen: 0 stocks passed all gates")
            return screened

        # ── Composite score ────────────────────────────────────────────────
        # Higher score = more attractive on a combined earnings/quality/value basis
        score_cols = {
            "earningsYield":  0.30,   # Magic Formula component 1
            "returnOnEquity": 0.25,   # Magic Formula component 2 (ROIC proxy)
            "grahamMoS":      0.25,   # Graham intrinsic value discount
            "fcfYield":       0.20,   # Cash generation quality
        }
        composite = pd.Series(0.0, index=screened.index)
        for col, weight in score_cols.items():
            if col in screened.columns:
                s = screened[col].dropna()
                if len(s) > 1:
                    rng = s.max() - s.min()
                    if rng > 0:
                        composite.loc[s.index] += weight * (s - s.min()) / rng

        screened["compositeScore"] = composite
        screened = screened.sort_values("compositeScore", ascending=False)
        self._log.info(
            "Value screen: %d/%d stocks passed | top pick: %s",
            len(screened), len(df),
            screened["ticker"].iloc[0] if not screened.empty else "—",
        )
        return screened.reset_index(drop=True)

    # ── Batch fetch ────────────────────────────────────────────────────────

    def fetch_all(
        self,
    ) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame]:
        """
        Iterate the full ticker universe and return:
          - histories: dict[ticker → OHLCV DataFrame]
          - screened : ranked value-screen DataFrame
        """
        histories: Dict[str, pd.DataFrame] = {}
        fundamentals: List[Dict[str, Any]] = []

        for ticker in self.cfg.tickers:
            hist = self.fetch_history(ticker)
            if hist is not None:
                histories[ticker] = hist

            fund = self.fetch_fundamentals(ticker)
            if fund is not None:
                fundamentals.append(fund)

        screened = self.screen_undervalued(fundamentals)
        return histories, screened


# ══════════════════════════════════════════════════════════════════════════════
# FixedIncomeDataProvider
# ══════════════════════════════════════════════════════════════════════════════

class FixedIncomeDataProvider:
    """
    Fetches US Treasury yield-curve data and bond-ETF YTM proxies.

    Treasury tickers (^IRX, ^FVX, ^TNX, ^TYX) are quoted in percent by
    yfinance — we normalise them to decimal on ingestion.
    Bond ETF YTM proxies use the 30-day SEC yield when available, falling
    back through trailing dividend yield and distribution yield fields.
    """

    def __init__(self, config: FixedIncomeConfig, retry: RetryConfig) -> None:
        self.cfg   = config
        self.retry = retry
        self._log  = logging.getLogger("data_feed.fixed_income")

    # ── Treasury yields ────────────────────────────────────────────────────

    def fetch_treasury_yields(self) -> Dict[str, pd.DataFrame]:
        """Historical yield series for each tenor, normalised to decimal."""
        yields: Dict[str, pd.DataFrame] = {}
        for tenor, ticker in self.cfg.treasury_tickers.items():
            self._log.info("Treasury %s (%s)", tenor, ticker)
            raw = _with_retry(
                yf.download,
                ticker,
                period=self.cfg.historical_period,
                interval=self.cfg.historical_interval,
                auto_adjust=True,
                progress=False,
                cfg=self.retry,
                label=f"treasury:{tenor}",
            )
            if raw is None or raw.empty:
                self._log.warning("No data — Treasury %s (%s)", tenor, ticker)
                continue
            df = _flatten_columns(raw.copy())
            for col in ("Open", "High", "Low", "Close"):
                if col in df.columns:
                    df[col] = df[col] / 100.0   # percent → decimal
            yields[tenor] = df
        return yields

    def current_yield_curve(
        self, treasury_yields: Dict[str, pd.DataFrame]
    ) -> pd.Series:
        """Most-recent closing yield for each available tenor."""
        curve: Dict[str, float] = {}
        for tenor, df in treasury_yields.items():
            if not df.empty and "Close" in df.columns:
                last = df["Close"].dropna()
                if not last.empty:
                    curve[tenor] = float(last.iloc[-1])
        # Preserve natural tenor ordering
        order = ["3M", "5Y", "10Y", "30Y"]
        return pd.Series({k: curve[k] for k in order if k in curve}, name="yield_decimal")

    def yield_curve_slope(self, curve: pd.Series) -> Optional[float]:
        """
        10Y − 3M spread in basis points.
        Negative value indicates curve inversion (recession signal).
        """
        try:
            return (curve["10Y"] - curve["3M"]) * 10_000
        except KeyError:
            return None

    def _build_yield_model(
        self,
        treasury_yields: Dict[str, pd.DataFrame],
    ) -> Optional[pd.DataFrame]:
        """
        Align all tenor series on a common date index and return a tidy
        DataFrame of daily yields.  Used by downstream regime-detection
        modules to detect inversion events.
        """
        frames: Dict[str, pd.Series] = {}
        for tenor, df in treasury_yields.items():
            if "Close" in df.columns:
                frames[tenor] = df["Close"].rename(tenor)
        if not frames:
            return None
        out = pd.concat(frames.values(), axis=1).sort_index()
        out.index.name = "date"
        return out

    # ── Bond ETF YTM proxy ─────────────────────────────────────────────────

    def fetch_etf_yield(self, ticker: str) -> Dict[str, Any]:
        """
        Retrieve YTM proxy for a bond ETF.

        Priority for YTM proxy field:
          1. info['yield']                         (30-day SEC yield)
          2. info['trailingAnnualDividendYield']   (trailing distribution)
          3. info['dividendYield']
          4. info['threeYearAverageReturn']
        """
        result: Dict[str, Any] = {
            "ticker":    ticker,
            "ytm_proxy": None,
            "ytm_source": None,
        }
        try:
            t    = yf.Ticker(ticker)
            info = _with_retry(
                lambda: t.info, cfg=self.retry, label=f"etf_info:{ticker}"
            )
        except Exception as exc:
            self._log.warning("ETF info failed — %s: %s", ticker, exc)
            return result

        if not info:
            return result

        result["name"]     = info.get("longName") or info.get("shortName")
        result["category"] = info.get("category")
        result["nav"]      = info.get("navPrice") or info.get("regularMarketPrice")
        result["aum"]      = info.get("totalAssets")
        result["expense_ratio"] = info.get("annualReportExpenseRatio")
        result["duration_years"] = info.get("duration")   # Macaulay duration

        ytm_candidates = [
            ("yield",                       "sec_30d_yield"),
            ("trailingAnnualDividendYield", "trailing_div_yield"),
            ("dividendYield",               "dividend_yield"),
            ("threeYearAverageReturn",      "3yr_avg_return"),
        ]
        for field_name, source_label in ytm_candidates:
            val = info.get(field_name)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        result["ytm_proxy"]  = fval
                        result["ytm_source"] = source_label
                        break
                except (TypeError, ValueError):
                    continue

        return result

    def fetch_etf_history(self, ticker: str) -> Optional[pd.DataFrame]:
        """Historical NAV/price series for a bond ETF."""
        raw = _with_retry(
            yf.download,
            ticker,
            period=self.cfg.historical_period,
            interval=self.cfg.historical_interval,
            auto_adjust=True,
            progress=False,
            cfg=self.retry,
            label=f"etf_hist:{ticker}",
        )
        if raw is None or raw.empty:
            self._log.warning("No history — ETF %s", ticker)
            return None
        return _flatten_columns(raw.copy())

    # ── Batch fetch ────────────────────────────────────────────────────────

    def fetch_all(self) -> Dict[str, Any]:
        self._log.info("Fetching Treasury yield curve …")
        treasury_yields = self.fetch_treasury_yields()
        curve  = self.current_yield_curve(treasury_yields)
        spread = self.yield_curve_slope(curve)
        yield_model = self._build_yield_model(treasury_yields)

        self._log.info(
            "Yield curve %s | 10Y-3M spread: %s bp",
            curve.to_dict(),
            f"{spread:+.1f}" if spread is not None else "N/A",
        )

        etf_rows:      List[Dict[str, Any]]       = []
        etf_histories: Dict[str, pd.DataFrame]    = {}

        for ticker, label in self.cfg.bond_etfs.items():
            self._log.info("Bond ETF: %s (%s)", ticker, label)
            etf_rows.append(self.fetch_etf_yield(ticker))
            hist = self.fetch_etf_history(ticker)
            if hist is not None:
                etf_histories[ticker] = hist

        return {
            "treasury_yields":   treasury_yields,
            "yield_curve":       curve,
            "yield_model":       yield_model,    # aligned multi-tenor DataFrame
            "curve_slope_bp":    spread,
            "etf_yields":        pd.DataFrame(etf_rows),
            "etf_histories":     etf_histories,
        }


# ══════════════════════════════════════════════════════════════════════════════
# ForexDataProvider
# ══════════════════════════════════════════════════════════════════════════════

class ForexDataProvider:
    """
    Multi-resolution FX data provider.

    Fetches daily (3y), hourly (60d), and 5-min tick (5d) series for
    major pairs, crosses, and commodity pairs.  Session analysis
    aggregates hourly bars by UTC hour to identify optimal entry windows
    via a volatility × liquidity composite score.
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

    # UTC hour ranges for major sessions (end is exclusive)
    SESSIONS: Dict[str, Tuple[int, int]] = {
        "Sydney":       (21, 6),    # wraps midnight
        "Tokyo":        (0,  9),
        "London":       (8,  16),
        "New York":     (13, 21),
        "LDN/NY Overlap": (13, 16),   # highest institutional liquidity
    }

    def __init__(self, config: ForexConfig, retry: RetryConfig) -> None:
        self.cfg   = config
        self.retry = retry
        self._log  = logging.getLogger("data_feed.forex")

    @property
    def all_pairs(self) -> List[str]:
        return self.cfg.majors + self.cfg.crosses + self.cfg.commodities

    def _label(self, ticker: str) -> str:
        return self.PAIR_LABELS.get(ticker, ticker)

    # ── Low-level download ─────────────────────────────────────────────────

    def _download(
        self, ticker: str, period: str, interval: str
    ) -> Optional[pd.DataFrame]:
        raw = _with_retry(
            yf.download,
            ticker,
            period=period,
            interval=interval,
            auto_adjust=True,
            progress=False,
            cfg=self.retry,
            label=f"fx:{ticker}@{interval}",
        )
        if raw is None or raw.empty:
            self._log.warning("No data — %s @ %s", self._label(ticker), interval)
            return None
        df = _flatten_columns(raw.copy())
        df.index = pd.to_datetime(df.index, utc=True)
        df.attrs.update({"ticker": ticker, "pair": self._label(ticker)})
        return df

    # ── Daily ──────────────────────────────────────────────────────────────

    def fetch_daily(self, ticker: str) -> Optional[pd.DataFrame]:
        df = self._download(ticker, self.cfg.daily_period, self.cfg.daily_interval)
        if df is None:
            return None
        return self._add_daily_indicators(df)

    def _add_daily_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["Close"]
        high  = df["High"]
        low   = df["Low"]

        # Trend
        df["SMA_20"]  = close.rolling(20).mean()
        df["SMA_50"]  = close.rolling(50).mean()
        df["EMA_9"]   = close.ewm(span=9,  adjust=False).mean()
        df["EMA_21"]  = close.ewm(span=21, adjust=False).mean()

        # RSI (14)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        df["RSI_14"] = 100 - (100 / (1 + gain / loss.replace(0, np.nan)))

        # ATR (14) — also used as a volatility filter for position sizing
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        df["ATR_14"]  = tr.rolling(14).mean()
        df["ATR_pct"] = df["ATR_14"] / close.replace(0, np.nan)

        # MACD (12, 26, 9)
        ema12          = close.ewm(span=12, adjust=False).mean()
        ema26          = close.ewm(span=26, adjust=False).mean()
        df["MACD"]     = ema12 - ema26
        df["MACD_sig"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_hist"]= df["MACD"] - df["MACD_sig"]

        # Bollinger Bands (20, 2σ)
        std20        = close.rolling(20).std()
        df["BB_UP"]  = df["SMA_20"] + 2 * std20
        df["BB_LO"]  = df["SMA_20"] - 2 * std20
        df["BB_PCT"] = (close - df["BB_LO"]) / (df["BB_UP"] - df["BB_LO"] + 1e-12)

        # Daily return & annualised vol
        df["Return"]  = close.pct_change()
        df["Vol_20d"] = df["Return"].rolling(20).std() * np.sqrt(252)

        return df

    # ── Hourly ─────────────────────────────────────────────────────────────

    def fetch_hourly(self, ticker: str) -> Optional[pd.DataFrame]:
        return self._download(ticker, self.cfg.hourly_period, self.cfg.hourly_interval)

    # ── 5-min tick ─────────────────────────────────────────────────────────

    def fetch_tick(self, ticker: str) -> Optional[pd.DataFrame]:
        return self._download(ticker, self.cfg.tick_period, self.cfg.tick_interval)

    # ── Session analysis ───────────────────────────────────────────────────

    def session_analysis(self, hourly_df: pd.DataFrame) -> pd.DataFrame:
        """
        Aggregate hourly bars by UTC hour and compute per-hour statistics:

          mean_abs_return  — directional opportunity proxy
          mean_range_pct   — intrabar range (pip movement potential)
          mean_volume      — liquidity (often 0 for FX; fallback to range)
          composite_score  — weighted rank: 45 % return + 35 % range + 20 % vol

        Returns a DataFrame indexed by UTC hour (0-23), sorted best → worst.
        """
        df = hourly_df.copy()
        df["utc_hour"]   = df.index.hour
        df["abs_return"] = df["Close"].pct_change().abs()
        df["range_pct"]  = (
            (df["High"] - df["Low"]) / df["Close"].shift(1).replace(0, np.nan)
        )

        agg = df.groupby("utc_hour").agg(
            mean_abs_return=("abs_return", "mean"),
            std_return      =("abs_return", "std"),
            mean_range_pct  =("range_pct",  "mean"),
            mean_volume     =("Volume",      "mean"),
            bar_count       =("Close",       "count"),
        )

        # FX volume from yfinance is unreliable — fall back to range
        agg["liquidity_proxy"] = agg["mean_volume"].where(
            agg["mean_volume"] > 0, agg["mean_range_pct"]
        )

        def _rank_norm(s: pd.Series) -> pd.Series:
            lo, hi = s.min(), s.max()
            return (s - lo) / (hi - lo) if (hi - lo) > 0 else pd.Series(0.5, index=s.index)

        agg["composite_score"] = (
            0.45 * _rank_norm(agg["mean_abs_return"])
          + 0.35 * _rank_norm(agg["mean_range_pct"])
          + 0.20 * _rank_norm(agg["liquidity_proxy"])
        )

        # Tag each hour with its trading session(s)
        def _sessions(hour: int) -> str:
            tags = []
            if 0  <= hour < 9:             tags.append("Tokyo")
            if 8  <= hour < 16:            tags.append("London")
            if 13 <= hour < 21:            tags.append("New York")
            if hour >= 21 or hour < 6:     tags.append("Sydney")
            if 13 <= hour < 16:            tags.append("⬡ LDN/NY Overlap")
            return " | ".join(tags) if tags else "Off-peak"

        agg["sessions"] = agg.index.map(_sessions)
        return agg.sort_values("composite_score", ascending=False)

    def optimal_entry_windows(
        self,
        session_map: Dict[str, pd.DataFrame],
        top_n: int = 6,
    ) -> pd.DataFrame:
        """
        Aggregate session scores across all pairs and return the top_n
        UTC hours by mean composite score.
        """
        rows: List[pd.Series] = []
        for pair_label, sa in session_map.items():
            s = sa["composite_score"].rename(pair_label)
            rows.append(s)

        if not rows:
            return pd.DataFrame()

        combined = pd.concat(rows, axis=1)
        out = pd.DataFrame({
            "mean_score": combined.mean(axis=1),
            "pairs_count": combined.notna().sum(axis=1),
            "sessions": combined.index.map(
                lambda h: session_map[next(iter(session_map))]["sessions"].get(h, "")
                if session_map else ""
            ),
        }).sort_values("mean_score", ascending=False).head(top_n)
        out.index.name = "utc_hour"
        return out

    # ── Correlation matrix ─────────────────────────────────────────────────

    def correlation_matrix(
        self,
        daily_data: Dict[str, pd.DataFrame],
        window: int = 60,
    ) -> pd.DataFrame:
        """Rolling 60-day return correlation across all FX pairs."""
        returns: Dict[str, pd.Series] = {}
        for ticker, df in daily_data.items():
            col = "Return" if "Return" in df.columns else None
            if col:
                returns[self._label(ticker)] = df[col]
            elif "Close" in df.columns:
                returns[self._label(ticker)] = df["Close"].pct_change()

        if not returns:
            return pd.DataFrame()

        ret_df = pd.DataFrame(returns).dropna(how="all")
        return ret_df.tail(window).corr()

    # ── Batch fetch ────────────────────────────────────────────────────────

    def fetch_all(self) -> Dict[str, Any]:
        daily:       Dict[str, pd.DataFrame] = {}
        hourly:      Dict[str, pd.DataFrame] = {}
        tick:        Dict[str, pd.DataFrame] = {}
        session_map: Dict[str, pd.DataFrame] = {}

        for ticker in self.all_pairs:
            label = self._label(ticker)

            self._log.info("FX daily:  %s", label)
            df_d = self.fetch_daily(ticker)
            if df_d is not None:
                daily[ticker] = df_d

            self._log.info("FX hourly: %s", label)
            df_h = self.fetch_hourly(ticker)
            if df_h is not None:
                hourly[ticker] = df_h
                session_map[label] = self.session_analysis(df_h)

            self._log.info("FX tick:   %s", label)
            df_t = self.fetch_tick(ticker)
            if df_t is not None:
                tick[ticker] = df_t

        corr    = self.correlation_matrix(daily)
        optimal = self.optimal_entry_windows(session_map)

        return {
            "daily":              daily,
            "hourly":             hourly,
            "tick":               tick,
            "session_analysis":   session_map,
            "optimal_windows":    optimal,
            "correlation_matrix": corr,
        }


# ══════════════════════════════════════════════════════════════════════════════
# DataFeedOrchestrator
# ══════════════════════════════════════════════════════════════════════════════

class DataFeedOrchestrator:
    """
    Top-level orchestrator.  Coordinates all three data providers and
    returns a single, self-contained bundle ready for downstream modules
    (portfolio construction, regime detection, backtesting, live signals).

    Usage:
        config = DataFeedConfig()          # customise as needed
        feed   = DataFeedOrchestrator(config)
        bundle = feed.run()
    """

    def __init__(self, config: Optional[DataFeedConfig] = None) -> None:
        self.cfg  = config or DataFeedConfig()
        self._log = logging.getLogger("data_feed.orchestrator")

        self.equity = EquityDataProvider(self.cfg.equity,       self.cfg.retry)
        self.fi     = FixedIncomeDataProvider(self.cfg.fixed_income, self.cfg.retry)
        self.forex  = ForexDataProvider(self.cfg.forex,          self.cfg.retry)

    def run(self) -> Dict[str, Any]:
        """Execute the full pipeline and return the data bundle."""
        self._log.info("═" * 65)
        self._log.info(
            "DataFeed pipeline starting  %s",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        self._log.info("═" * 65)

        t0 = time.perf_counter()

        self._log.info("── [1/3] Equity ─────────────────────────────────────────")
        eq_hist, eq_screened = self.equity.fetch_all()

        self._log.info("── [2/3] Fixed Income ───────────────────────────────────")
        fi_data = self.fi.fetch_all()

        self._log.info("── [3/3] Forex ──────────────────────────────────────────")
        fx_data = self.forex.fetch_all()

        elapsed = time.perf_counter() - t0
        self._log.info("Pipeline complete in %.1f s", elapsed)

        bundle: Dict[str, Any] = {
            "equity": {
                "histories":         eq_hist,
                "screened_universe": eq_screened,
            },
            "fixed_income": fi_data,
            "forex":        fx_data,
            "meta": {
                "run_at":    datetime.now(timezone.utc).isoformat(),
                "elapsed_s": round(elapsed, 2),
            },
        }

        self._log_summary(bundle)
        return bundle

    def _log_summary(self, bundle: Dict[str, Any]) -> None:
        eq = bundle["equity"]
        fi = bundle["fixed_income"]
        fx = bundle["forex"]

        screened    = eq["screened_universe"]
        top3_tickers = (
            screened["ticker"].head(3).tolist()
            if not screened.empty and "ticker" in screened.columns
            else []
        )

        curve       = fi.get("yield_curve", pd.Series(dtype=float))
        slope_bp    = fi.get("curve_slope_bp")
        etf_yields  = fi.get("etf_yields", pd.DataFrame())
        best_etf_ytm = None
        if not etf_yields.empty and "ytm_proxy" in etf_yields.columns:
            best_row    = etf_yields.dropna(subset=["ytm_proxy"])
            best_etf_ytm = best_row["ytm_proxy"].max() if not best_row.empty else None

        opt = fx.get("optimal_windows", pd.DataFrame())
        best_hour    = int(opt.index[0]) if not opt.empty else None
        best_session = opt["sessions"].iloc[0] if not opt.empty else "—"

        self._log.info("─" * 65)
        self._log.info("PIPELINE SUMMARY")
        self._log.info("  Equity histories  : %d tickers",  len(eq["histories"]))
        self._log.info("  Value screen pass : %d stocks",   len(screened))
        self._log.info("  Top-3 value picks : %s",         top3_tickers)
        self._log.info("  Yield curve       : %s",
                       {k: f"{v:.3%}" for k, v in curve.items()})
        self._log.info(
            "  10Y-3M slope      : %s bp",
            f"{slope_bp:+.1f}" if slope_bp is not None else "N/A",
        )
        self._log.info("  Best ETF YTM proxy: %s",
                       f"{best_etf_ytm:.2%}" if best_etf_ytm else "N/A")
        self._log.info("  FX daily pairs    : %d",          len(fx["daily"]))
        self._log.info("  FX hourly pairs   : %d",          len(fx["hourly"]))
        self._log.info("  FX tick pairs     : %d",          len(fx["tick"]))
        self._log.info(
            "  Best entry window : %s  (session: %s)",
            f"{best_hour:02d}:00 UTC" if best_hour is not None else "N/A",
            best_session,
        )
        self._log.info("─" * 65)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point — quick smoke test
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    # Narrow universe for a fast demo run
    demo_cfg = DataFeedConfig(
        equity=EquityConfig(
            tickers=["AAPL", "MSFT", "GOOGL", "JPM", "XOM", "JNJ", "KO", "BRK-B"],
            historical_period="1y",
        ),
        fixed_income=FixedIncomeConfig(
            bond_etfs={"TLT": "iShares 20+Y", "AGG": "US Aggregate", "HYG": "High Yield"},
            historical_period="6mo",
        ),
        forex=ForexConfig(
            majors=["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
            crosses=[],
            commodities=["XAUUSD=X"],
        ),
    )

    feed   = DataFeedOrchestrator(demo_cfg)
    bundle = feed.run()

    # Print structured meta output
    print("\n── Meta ─────────────────────────────────────────────────────────")
    print(json.dumps(bundle["meta"], indent=2))

    # Top value-screen stocks
    screened = bundle["equity"]["screened_universe"]
    if not screened.empty:
        display_cols = [c for c in [
            "ticker", "currentPrice", "trailingPE", "priceToBook",
            "earningsYield", "returnOnEquity", "grahamNumber",
            "grahamMoS", "fcfYield", "compositeScore",
        ] if c in screened.columns]
        print("\n── Value-Screened Stocks ────────────────────────────────────────")
        print(screened[display_cols].to_string(index=False))

    # Yield curve
    print("\n── Yield Curve ──────────────────────────────────────────────────")
    print(bundle["fixed_income"]["yield_curve"].apply(lambda v: f"{v:.3%}"))

    # ETF YTM proxies
    etf_df = bundle["fixed_income"]["etf_yields"]
    if not etf_df.empty:
        print("\n── Bond ETF YTM Proxies ─────────────────────────────────────────")
        cols = [c for c in ["ticker", "name", "ytm_proxy", "ytm_source",
                             "duration_years", "expense_ratio"] if c in etf_df.columns]
        print(etf_df[cols].to_string(index=False))

    # Optimal FX entry windows
    opt = bundle["forex"]["optimal_windows"]
    if not opt.empty:
        print("\n── Optimal FX Entry Windows (UTC) ───────────────────────────────")
        print(opt.to_string())
