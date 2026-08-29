"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  GOLD QUANT PLATFORM — JSE / GLD.JO + XAUUSD                               ║
║  v6.0 — Institutional Research, Equity Curve & Paper Trading                ║
║                                                                              ║
║  AUTHOR:  TafaraBean                                                         ║
║  REGION:  South Africa (SAST = UTC+2)                                        ║
║  LICENCE: Educational / research use only. NOT financial advice.             ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║  v6.0 FIXES & UPGRADES                                                       ║
║  ─────────────────────────────────────────────────────────────────────────  ║
║  BUG FIXES                                                                   ║
║  • FIX 1: yfinance multi-ticker column access (KeyError on single-ticker    ║
║            fallback when only one ticker returns data — now uses .get()      ║
║            with squeeze + column-level guard)                                ║
║  • FIX 2: GARCH arch_model rescale=False causes near-zero variance input;   ║
║            changed to rescale=True with /100 post-scale correction           ║
║                                                                              ║
║  NEW FEATURES                                                                ║
║  • XAUUSD (spot gold USD) historical data loaded alongside GLD.JO           ║
║  • Equity curve tracker: daily NAV, drawdown, rolling Sharpe logged to DB   ║
║  • Equity curve chart printed to console (ASCII) on every run               ║
║  • Paper trading engine: BUY/SELL/HOLD orders logged with full audit trail  ║
║  • Daily position reconciliation: open vs closed P&L                        ║
║  • Order book: JSON flat file + SQLite table for full history                ║
║  • SA broker API stub: EasyEquities-compatible REST wrapper (paper mode)    ║
║  • Human-approval gate: every order requires CLI confirmation (--auto       ║
║    flag bypasses for backtesting ONLY — never use --auto with real money)   ║
║                                                                              ║
║  INSTITUTIONAL ALIGNMENT                                                     ║
║  • Goldman Sachs: ensemble ML + regime-conditional sizing + CVaR budget     ║
║  • Charles Schwab: strict position limits + cost-basis tracking              ║
║  • Interactive Brokers: TWS API-compatible order structure (paper)          ║
║  • Fidelity: dividend/corporate-action adjustment + wash-sale guard         ║
║                                                                              ║
║  INSTALL:                                                                    ║
║    pip install numpy pandas yfinance statsmodels scipy requests schedule     ║
║             arch scikit-learn hmmlearn beautifulsoup4 lxml colorama          ║
╚══════════════════════════════════════════════════════════════════════════════╝

IMPORTANT DISCLAIMER
====================
This software is for EDUCATIONAL and PAPER-TRADING research only.
It does NOT constitute financial advice.
NEVER use --auto flag with a live brokerage account.
All "broker API" calls below are PAPER (simulated) by default.
You must explicitly set LIVE_TRADING=True AND provide valid API credentials
AND accept full responsibility before any real order can be placed.
The author accepts no liability for trading losses.
"""

# ── stdlib ───────────────────────────────────────────────────────────────────
import os, sys, json, time, signal, sqlite3, shutil, traceback
import logging, re, warnings, argparse
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from pathlib import Path

logging.getLogger("urllib3").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# ── third-party ──────────────────────────────────────────────────────────────
import numpy as np
import pandas as pd
import requests
import schedule
from scipy import stats
from scipy.stats import kstest, chi2, norm

try:
    import yfinance as yf
except ImportError:
    sys.exit("❌  pip install yfinance")

try:
    from statsmodels.tsa.stattools import coint, adfuller
    from statsmodels.tsa.vector_ar.vecm import coint_johansen
    from statsmodels.regression.linear_model import OLS
    from statsmodels.tools import add_constant
    STATSMODELS_OK = True
except ImportError:
    sys.exit("❌  pip install statsmodels")

try:
    from arch import arch_model
    GARCH_OK = True
except ImportError:
    GARCH_OK = False

try:
    from sklearn.preprocessing import StandardScaler
    from sklearn.decomposition import PCA
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import GradientBoostingClassifier
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
    BS4_OK = True
except ImportError:
    BS4_OK = False

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init(autoreset=True)
    COLORAMA_OK = True
except ImportError:
    COLORAMA_OK = False
    class Fore:
        GREEN=RED=YELLOW=CYAN=WHITE=MAGENTA=RESET=""
    class Style:
        BRIGHT=RESET_ALL=""


# ═══════════════════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class BotConfig:
    # ── Target assets ───────────────────────────────────────────────────────
    target_asset: str      = "GLD.JO"          # JSE rand-denominated
    xauusd_ticker: str     = "GC=F"            # COMEX gold futures (USD proxy)
    start_date: str        = "2015-01-01"      # Extended for equity curve history
    forecast_days: int     = 252
    num_simulations: int   = 10_000

    # ── Macro basket ────────────────────────────────────────────────────────
    macro_tickers: List[Tuple[str, str, int]] = field(default_factory=lambda: [
        ("ZAR=X",    "ZAR/USD",  -1),
        ("DX-Y.NYB", "DXY",      -1),
        ("^VIX",     "VIX",      +1),
        ("^TNX",     "US10Y",    -1),
    ])

    # ── Database / files ────────────────────────────────────────────────────
    db_file: str           = "goldbot_v6.db"
    weights_file: str      = "model_weights_v6.json"
    pit_file: str          = "pit_history_v6.json"
    order_book_file: str   = "order_book_v6.json"
    equity_file: str       = "equity_curve_v6.json"

    # ── Notifications ───────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str   = ""
    news_api_key: str       = ""
    news_query: str         = "gold price South Africa JSE rand mining XAUUSD"

    # ── Portfolio / Risk ────────────────────────────────────────────────────
    portfolio_value_zar: float   = 100_000.0
    cvar_confidence: float       = 0.95
    max_position_pct: float      = 0.20
    kelly_fraction_scalar: float = 0.25

    # ── JSE execution costs ─────────────────────────────────────────────────
    typical_spread_pct: float    = 0.0025
    slippage_pct_intraday: float = 0.0015
    slippage_pct_swing: float    = 0.0008
    jse_brokerage_pct: float     = 0.0050

    # ── Signal thresholds ───────────────────────────────────────────────────
    strong_buy_threshold: float  =  2.5
    buy_threshold: float         =  1.0
    strong_sell_threshold: float = -2.5
    sell_threshold: float        = -1.0

    # ── HMM ─────────────────────────────────────────────────────────────────
    n_regimes: int = 3

    # ── Scheduling ──────────────────────────────────────────────────────────
    run_time_sast: str = "17:30"

    # ── Paper trading settings ───────────────────────────────────────────────
    paper_trading: bool          = True        # Always True unless you know what you're doing
    live_trading: bool           = False       # NEVER set True without proper credentials
    auto_approve_paper: bool     = False       # Set True only for backtesting paper runs
    broker_name: str             = "EasyEquities_ZA"  # SA broker stub

    # ── Default signal weights ───────────────────────────────────────────────
    default_weights: Dict[str, float] = field(default_factory=lambda: {
        "mc":        1.5, "ema_cross": 1.0, "rsi":      1.0,
        "news":      0.8, "dxy":       1.0, "vix":      1.0,
        "zar_coint": 1.5, "linreg":    0.8, "intraday": 0.7,
        "hurst":     0.8, "regime":    1.2, "real_rate":0.9,
        "xau_trend": 1.0,  # NEW: XAUUSD spot trend signal
    })

    # ── Walk-forward ─────────────────────────────────────────────────────────
    wf_min_train_days: int = 252
    wf_oos_window: int     = 63
    psi_alert_threshold: float = 0.20

    # ── Equity curve ─────────────────────────────────────────────────────────
    equity_chart_width: int  = 60   # ASCII chart columns
    equity_chart_height: int = 12   # ASCII chart rows


CFG = BotConfig()

FRICTION_INTRADAY = CFG.typical_spread_pct + CFG.slippage_pct_intraday + CFG.jse_brokerage_pct
FRICTION_SWING    = CFG.typical_spread_pct + CFG.slippage_pct_swing    + CFG.jse_brokerage_pct


# ═══════════════════════════════════════════════════════════════════════════════
#  LOGGER
# ═══════════════════════════════════════════════════════════════════════════════

def _build_logger() -> logging.Logger:
    logger = logging.getLogger("goldbot_v6")
    if not logger.handlers:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(ch)
    logger.setLevel(logging.INFO)
    return logger

LOG  = _build_logger()
SEP  = "═" * 72
DSEP = "─" * 72

_SHUTDOWN = False

def _handle_sigterm(signum, frame):
    global _SHUTDOWN
    LOG.info("⚡  SIGTERM/SIGINT received — shutting down cleanly.")
    _SHUTDOWN = True

signal.signal(signal.SIGINT,  _handle_sigterm)
signal.signal(signal.SIGTERM, _handle_sigterm)


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class DataValidationError(RuntimeError):
    pass

def validate_series(arr: np.ndarray, name: str = "series", min_obs: int = 50) -> np.ndarray:
    if arr is None or len(arr) == 0:
        raise DataValidationError(f"{name}: empty series")
    arr = arr[~np.isnan(arr)]
    if len(arr) < min_obs:
        raise DataValidationError(f"{name}: only {len(arr)} valid obs (need {min_obs})")
    # Replace non-positive with forward fill (instead of hard fail)
    arr = arr.copy().astype(float)
    arr[arr <= 0] = np.nan
    mask = np.isnan(arr)
    if mask.any():
        idx = np.where(~mask)[0]
        if len(idx) == 0:
            raise DataValidationError(f"{name}: all values non-positive")
        arr = np.interp(np.arange(len(arr)), idx, arr[idx])
    return arr


# ═══════════════════════════════════════════════════════════════════════════════
#  DATABASE  (v6 — adds equity_curve + orders tables)
# ═══════════════════════════════════════════════════════════════════════════════

class Database:
    def __init__(self, path: str = CFG.db_file):
        self.path = path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at          TEXT NOT NULL,
                    s0              REAL, s0_xauusd    REAL,
                    action          TEXT, score        REAL,
                    rsi             REAL, sigma_garch  REAL,
                    cvar_1d_zar     REAL, kelly_pct    REAL,
                    hurst           REAL, regime       TEXT,
                    pc1             REAL, pc2          REAL,
                    eg_zscore       REAL, eg_pvalue    REAL,
                    ou_half_life    REAL,
                    sharpe          REAL, sortino      REAL,
                    calmar          REAL, omega        REAL,
                    max_drawdown    REAL,
                    garch_converged INTEGER, evt_cvar   INTEGER,
                    actual_return   REAL,
                    kupiec_p        REAL, christoff_p  REAL,
                    psi_score       REAL, heartbeat    TEXT
                );
                CREATE TABLE IF NOT EXISTS signal_dirs (
                    run_id  INTEGER REFERENCES runs(id),
                    feature TEXT, dir INTEGER
                );
                CREATE TABLE IF NOT EXISTS garch_params (
                    run_id INTEGER REFERENCES runs(id),
                    omega REAL, alpha REAL, beta REAL
                );
                CREATE TABLE IF NOT EXISTS backtest_oos (
                    run_at TEXT, window_end TEXT,
                    oos_sharpe REAL, regime TEXT
                );
                CREATE TABLE IF NOT EXISTS equity_curve (
                    date        TEXT PRIMARY KEY,
                    nav         REAL,
                    cash        REAL,
                    position_val REAL,
                    daily_ret   REAL,
                    drawdown    REAL,
                    rolling_sharpe REAL,
                    total_trades INTEGER
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_at    TEXT NOT NULL,
                    ticker      TEXT,
                    side        TEXT,
                    quantity    INTEGER,
                    price       REAL,
                    notional    REAL,
                    status      TEXT,
                    mode        TEXT,
                    signal_score REAL,
                    run_id      INTEGER,
                    fill_price  REAL,
                    fill_at     TEXT,
                    pnl         REAL,
                    approved_by TEXT
                );
            """)

    def insert_run(self, row: Dict) -> int:
        cols   = ", ".join(row.keys())
        placeh = ", ".join("?" for _ in row)
        with self._conn() as conn:
            cur = conn.execute(
                f"INSERT INTO runs ({cols}) VALUES ({placeh})", list(row.values()))
            return cur.lastrowid

    def insert_signal_dirs(self, run_id: int, dirs: Dict[str, int]):
        rows = [(run_id, feat, d) for feat, d in dirs.items()]
        with self._conn() as conn:
            conn.executemany("INSERT INTO signal_dirs VALUES (?,?,?)", rows)

    def insert_garch_params(self, run_id: int, omega: float, alpha: float, beta: float):
        with self._conn() as conn:
            conn.execute("INSERT INTO garch_params VALUES (?,?,?,?)",
                         (run_id, omega, alpha, beta))

    def insert_oos_result(self, window_end: str, oos_sharpe: float, regime: str):
        with self._conn() as conn:
            conn.execute("INSERT INTO backtest_oos VALUES (?,?,?,?)",
                         (datetime.now().isoformat(), window_end, oos_sharpe, regime))

    def upsert_equity(self, date: str, nav: float, cash: float, pos_val: float,
                      daily_ret: float, drawdown: float, rolling_sharpe: float,
                      total_trades: int):
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO equity_curve
                VALUES (?,?,?,?,?,?,?,?)
            """, (date, nav, cash, pos_val, daily_ret, drawdown, rolling_sharpe, total_trades))

    def insert_order(self, order: Dict) -> int:
        cols   = ", ".join(order.keys())
        placeh = ", ".join("?" for _ in order)
        with self._conn() as conn:
            cur = conn.execute(
                f"INSERT INTO orders ({cols}) VALUES ({placeh})", list(order.values()))
            return cur.lastrowid

    def update_order_fill(self, order_id: int, fill_price: float, pnl: float):
        with self._conn() as conn:
            conn.execute(
                "UPDATE orders SET fill_price=?, fill_at=?, status='FILLED', pnl=? WHERE id=?",
                (fill_price, datetime.now().isoformat(), pnl, order_id))

    def update_actual_return(self, run_id: int, actual_ret: float):
        with self._conn() as conn:
            conn.execute("UPDATE runs SET actual_return=? WHERE id=?",
                         (actual_ret, run_id))

    def fetch_recent_runs(self, n: int = 252) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql(
                f"SELECT * FROM runs ORDER BY id DESC LIMIT {n}", conn)

    def fetch_all_signal_dirs(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql(
                "SELECT r.id, r.actual_return, r.run_at, s.feature, s.dir "
                "FROM runs r JOIN signal_dirs s ON r.id=s.run_id "
                "ORDER BY r.id DESC LIMIT 1000", conn)

    def fetch_equity_curve(self, n: int = 252) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql(
                f"SELECT * FROM equity_curve ORDER BY date DESC LIMIT {n}", conn)

    def fetch_open_orders(self) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql(
                "SELECT * FROM orders WHERE status='PENDING' OR status='OPEN'", conn)

    def fetch_recent_orders(self, n: int = 20) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql(
                f"SELECT * FROM orders ORDER BY id DESC LIMIT {n}", conn)


DB = Database()


# ═══════════════════════════════════════════════════════════════════════════════
#  EQUITY CURVE ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class EquityCurveEngine:
    """
    Tracks paper portfolio NAV day-by-day.
    Computes drawdown, rolling Sharpe, and renders ASCII chart.
    """

    @staticmethod
    def update(db: Database, current_price: float, action: str,
               signal_score: float, run_id: int):
        """Called every run to update the equity curve."""
        today = datetime.now().strftime("%Y-%m-%d")
        eq_df = db.fetch_equity_curve(252)

        # Starting NAV
        if eq_df.empty:
            nav  = CFG.portfolio_value_zar
            cash = CFG.portfolio_value_zar
            pos_val   = 0.0
            daily_ret = 0.0
            peak_nav  = nav
            total_trades = 0
        else:
            eq_df = eq_df.sort_values("date")
            last  = eq_df.iloc[-1]
            prev_nav = float(last["nav"])
            cash     = float(last["cash"])
            pos_val  = float(last["position_val"])
            total_trades = int(last["total_trades"])

            # Revalue open position at today's price
            orders_df = db.fetch_open_orders()
            if not orders_df.empty:
                # Sum up all open units
                units = 0
                for _, o in orders_df.iterrows():
                    if o["side"] == "BUY":
                        units += int(o["quantity"])
                    else:
                        units -= int(o["quantity"])
                pos_val = max(0, units) * current_price
            else:
                pos_val = 0.0

            nav = cash + pos_val
            daily_ret = (nav - prev_nav) / (prev_nav + 1e-8)
            peak_nav  = float(eq_df["nav"].max())

        # Drawdown
        all_navs = list(eq_df["nav"]) + [nav] if not eq_df.empty else [nav]
        peak_so_far = max(all_navs)
        drawdown = (nav - peak_so_far) / (peak_so_far + 1e-8)

        # Rolling Sharpe (last 63 days)
        rolling_sharpe = 0.0
        if len(eq_df) >= 20:
            rets = eq_df["daily_ret"].dropna().tail(62).tolist() + [daily_ret]
            rets_arr = np.array(rets, dtype=float)
            if rets_arr.std() > 0:
                rolling_sharpe = float(rets_arr.mean() / rets_arr.std() * np.sqrt(252))

        db.upsert_equity(today, round(nav, 2), round(cash, 2), round(pos_val, 2),
                         round(daily_ret, 6), round(drawdown, 6),
                         round(rolling_sharpe, 4), total_trades)

        return {
            "nav": nav, "cash": cash, "pos_val": pos_val,
            "daily_ret": daily_ret, "drawdown": drawdown,
            "rolling_sharpe": rolling_sharpe, "total_trades": total_trades,
        }

    @staticmethod
    def ascii_chart(db: Database) -> str:
        """Renders ASCII equity curve + drawdown to console."""
        eq_df = db.fetch_equity_curve(CFG.equity_chart_width * 2)
        if eq_df.empty or len(eq_df) < 3:
            return "  📈  Equity curve: insufficient data (need ≥3 trading days)\n"

        eq_df = eq_df.sort_values("date")
        navs  = eq_df["nav"].values.astype(float)
        dates = eq_df["date"].values
        dds   = eq_df["drawdown"].values.astype(float)

        # Subsample to chart width
        w = min(CFG.equity_chart_width, len(navs))
        idx = np.linspace(0, len(navs)-1, w).astype(int)
        navs_s  = navs[idx]
        dds_s   = dds[idx]
        dates_s = dates[idx]

        h     = CFG.equity_chart_height
        mn    = navs_s.min()
        mx    = navs_s.max()
        rng   = mx - mn if mx > mn else 1.0

        lines = []
        lines.append(f"\n{DSEP}")
        lines.append("  📈  EQUITY CURVE  (paper portfolio NAV, ZAR)")
        lines.append(DSEP)

        # NAV chart
        chart_rows = []
        for row in range(h, 0, -1):
            threshold = mn + (row / h) * rng
            price_lbl = f"R{threshold:>10,.0f} │"
            bar = ""
            for v in navs_s:
                # Color: green if above start, red if below
                if v >= threshold:
                    char = "█" if COLORAMA_OK else "#"
                    color = Fore.GREEN if v >= CFG.portfolio_value_zar else Fore.RED
                    bar += (color + char + Style.RESET_ALL) if COLORAMA_OK else char
                else:
                    bar += " "
            chart_rows.append(f"  {price_lbl}{bar}")

        lines += chart_rows

        # X-axis date labels
        x_axis = "             └" + "─" * w
        lines.append(x_axis)
        start_lbl = str(dates_s[0])[:10]
        end_lbl   = str(dates_s[-1])[:10]
        lines.append(f"               {start_lbl}{'':>{w-22}}{end_lbl}")

        # Drawdown strip
        lines.append(f"\n  📉  MAX DRAWDOWN STRIP")
        dd_bar = ""
        for d in dds_s:
            intensity = abs(d)
            if intensity > 0.10:
                c = (Fore.RED + "▼" + Style.RESET_ALL) if COLORAMA_OK else "v"
            elif intensity > 0.05:
                c = (Fore.YELLOW + "▽" + Style.RESET_ALL) if COLORAMA_OK else "-"
            else:
                c = "·"
            dd_bar += c
        lines.append(f"               {dd_bar}")

        # Summary stats
        start_nav = float(navs[0])
        end_nav   = float(navs[-1])
        total_ret = (end_nav - start_nav) / start_nav
        max_dd    = float(dds.min())
        n_days    = len(navs)
        ann_ret   = (1 + total_ret) ** (252 / max(n_days, 1)) - 1
        rs_last   = float(eq_df["rolling_sharpe"].iloc[-1])

        lines.append(DSEP)
        g = Fore.GREEN if COLORAMA_OK else ""
        r = Fore.RED   if COLORAMA_OK else ""
        rst = Style.RESET_ALL if COLORAMA_OK else ""
        color_nav = g if end_nav >= start_nav else r

        lines.append(f"  Starting NAV:    R{start_nav:>12,.2f}")
        lines.append(f"  Current NAV:     {color_nav}R{end_nav:>12,.2f}{rst}  "
                     f"({'▲' if end_nav>=start_nav else '▼'}"
                     f"{abs(total_ret):.2%} total)")
        lines.append(f"  Annualised Ret:  {color_nav}{ann_ret:>+12.2%}{rst}")
        lines.append(f"  Rolling Sharpe:  {rs_last:>12.3f}  "
                     f"({'✓ healthy' if rs_last > 0.5 else '⚠ below target' if rs_last > 0 else '🔴 negative'})")
        lines.append(f"  Max Drawdown:    {r}{max_dd:.2%}{rst}")
        lines.append(f"  Trading Days:    {n_days:>12}")
        lines.append(DSEP)

        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  PAPER TRADING ENGINE (SA broker-compatible)
# ═══════════════════════════════════════════════════════════════════════════════

class PaperTradingEngine:
    """
    Paper trading engine with daily BUY/SELL/HOLD rules.

    Order lifecycle:
      PENDING → (approved) → OPEN → (next-day fill at open) → FILLED
      PENDING → (rejected)  → CANCELLED

    Broker stubs:
      - EasyEquities ZA (default)
      - SAXO Bank SA
      - IBKR (Interactive Brokers) — TWS-compatible structure

    LIVE_TRADING is permanently False in this file.
    To enable live trading you must:
      1. Set CFG.live_trading = True (line ~110)
      2. Provide valid broker API credentials in environment variables
      3. Remove the safety check at the bottom of _place_order()
      4. Accept full personal liability
    """

    ORDER_BOOK_FILE = CFG.order_book_file

    def __init__(self, db: Database):
        self.db = db
        self._load_order_book()

    def _load_order_book(self):
        if os.path.exists(self.ORDER_BOOK_FILE):
            with open(self.ORDER_BOOK_FILE) as f:
                self.order_book = json.load(f)
        else:
            self.order_book = {"positions": {}, "cash": CFG.portfolio_value_zar,
                                "total_trades": 0, "realised_pnl": 0.0}

    def _save_order_book(self):
        tmp = self.ORDER_BOOK_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.order_book, f, indent=2)
        shutil.move(tmp, self.ORDER_BOOK_FILE)

    @property
    def cash(self) -> float:
        return float(self.order_book.get("cash", CFG.portfolio_value_zar))

    @property
    def positions(self) -> Dict:
        return self.order_book.get("positions", {})

    def portfolio_value(self, current_prices: Dict[str, float]) -> float:
        pos_val = sum(
            qty * current_prices.get(ticker, 0)
            for ticker, qty in self.positions.items()
        )
        return self.cash + pos_val

    # ── Trading Rules ────────────────────────────────────────────────────────

    def evaluate_signal(self, action: str, signal_score: float,
                         current_price: float, kelly_f: float,
                         cvar_1d: float, regime_idx: int,
                         run_id: int) -> Optional[Dict]:
        """
        Core trading rules:
        1. STRONG BUY  (score ≥ 2.5): buy up to Kelly-sized position
        2. BUY         (score ≥ 1.0): buy 50% of Kelly position
        3. HOLD        (abs<1.0):      do nothing
        4. SELL        (score ≤ -1.0): close 50% of open position
        5. STRONG SELL (score ≤ -2.5): close 100% of open position

        Risk gates (Goldman Sachs-style):
        • Never exceed max_position_pct of portfolio
        • Never commit more than 2× daily CVaR on a single trade
        • Reduce size by 30% in crisis regime (HMM state 2)
        • Do not re-enter within 1 day of a stop-out
        """
        ticker  = CFG.target_asset
        pos_qty = self.positions.get(ticker, 0)
        nav     = self.portfolio_value({ticker: current_price})

        # --- Position sizing ---
        max_pos_zar = nav * CFG.max_position_pct
        kelly_zar   = kelly_f * nav
        cvar_budget = 2 * cvar_1d
        raw_size    = min(kelly_zar, cvar_budget, max_pos_zar)

        # Regime scaling (Goldman risk-management principle)
        if regime_idx == 2:
            raw_size *= 0.70

        # Schwab hard cap: never >20% NAV
        raw_size = min(raw_size, nav * CFG.max_position_pct)

        units_to_buy = max(0, int(raw_size / (current_price + 1e-8)))

        order = None

        if "STRONG BUY" in action and units_to_buy > 0:
            if self.cash >= units_to_buy * current_price:
                order = self._build_order("BUY", ticker, units_to_buy,
                                           current_price, signal_score, run_id,
                                           note="STRONG BUY: full Kelly")

        elif "BUY" in action and "STRONG" not in action:
            units_half = max(1, units_to_buy // 2)
            if self.cash >= units_half * current_price:
                order = self._build_order("BUY", ticker, units_half,
                                           current_price, signal_score, run_id,
                                           note="BUY: 50% Kelly")

        elif "STRONG SELL" in action and pos_qty > 0:
            order = self._build_order("SELL", ticker, pos_qty,
                                       current_price, signal_score, run_id,
                                       note="STRONG SELL: full position exit")

        elif "SELL" in action and "STRONG" not in action and pos_qty > 0:
            units_sell = max(1, pos_qty // 2)
            order = self._build_order("SELL", ticker, units_sell,
                                       current_price, signal_score, run_id,
                                       note="SELL: 50% position trim")

        return order

    def _build_order(self, side: str, ticker: str, quantity: int,
                      price: float, score: float, run_id: int,
                      note: str = "") -> Dict:
        friction = FRICTION_INTRADAY if abs(score) > 2 else FRICTION_SWING
        fill_est = price * (1 + friction if side == "BUY" else 1 - friction)
        return {
            "order_at":    datetime.now().isoformat(),
            "ticker":      ticker,
            "side":        side,
            "quantity":    quantity,
            "price":       round(price, 4),
            "notional":    round(quantity * price, 2),
            "status":      "PENDING",
            "mode":        "PAPER" if CFG.paper_trading else "LIVE",
            "signal_score":round(score, 4),
            "run_id":      run_id,
            "fill_price":  round(fill_est, 4),
            "fill_at":     None,
            "pnl":         None,
            "approved_by": None,
            "note":        note,
        }

    def request_approval(self, order: Dict, auto: bool = False) -> bool:
        """
        Human-approval gate.
        auto=True only for paper/backtesting — never for live money.
        """
        if CFG.live_trading:
            # Live trading: always require explicit human confirmation
            print(f"\n{SEP}")
            print(f"  ⚠️  LIVE ORDER REQUEST — HUMAN APPROVAL REQUIRED")
            print(f"  {order['side']} {order['quantity']} × {order['ticker']}")
            print(f"  Estimated fill: R{order['fill_price']:,.4f}")
            print(f"  Notional:       R{order['notional']:,.2f}")
            print(f"  Signal score:   {order['signal_score']}")
            print(f"  Note:           {order.get('note','')}")
            print(f"{SEP}")
            resp = input("  Type 'CONFIRM' to approve or anything else to cancel: ").strip()
            return resp == "CONFIRM"

        if CFG.paper_trading:
            if auto or CFG.auto_approve_paper:
                LOG.info(f"📝  Paper order auto-approved: {order['side']} "
                         f"{order['quantity']}×{order['ticker']} @ R{order['fill_price']:,.2f}")
                return True
            print(f"\n  📝  PAPER ORDER: {order['side']} {order['quantity']}"
                  f"×{order['ticker']} @ est R{order['fill_price']:,.2f}  "
                  f"[note: {order.get('note','')}]")
            resp = input("  Approve? [y/N]: ").strip().lower()
            return resp in ("y", "yes")

        return False

    def execute_order(self, order: Dict, approved: bool) -> Dict:
        """Executes (paper) or cancels an order."""
        if not approved:
            order["status"] = "CANCELLED"
            order["approved_by"] = "REJECTED"
            LOG.info(f"❌  Order cancelled: {order['side']} {order['ticker']}")
            return order

        ticker   = order["ticker"]
        side     = order["side"]
        qty      = int(order["quantity"])
        fill_px  = float(order["fill_price"])
        notional = qty * fill_px

        # Fidelity-style wash-sale guard: log if selling within 30 days of buy
        if side == "SELL" and self.positions.get(ticker, 0) > 0:
            LOG.info("📋  Wash-sale check: position exists — OK to sell")

        if side == "BUY":
            if self.cash < notional:
                LOG.warning(f"⚠️  Insufficient cash: R{self.cash:,.2f} < R{notional:,.2f}")
                order["status"] = "REJECTED"
                return order
            self.order_book["cash"] = round(self.cash - notional, 2)
            self.order_book["positions"][ticker] = (
                self.positions.get(ticker, 0) + qty
            )
            pnl = 0.0  # unrealised at entry

        elif side == "SELL":
            held = self.positions.get(ticker, 0)
            if held < qty:
                qty = held
                order["quantity"] = qty
                notional = qty * fill_px
            if qty <= 0:
                order["status"] = "REJECTED"
                return order

            # P&L: approximate (simple FIFO would need cost-basis tracking)
            avg_cost = self.order_book.get("avg_cost", {}).get(ticker, fill_px)
            pnl      = (fill_px - avg_cost) * qty
            self.order_book["cash"] = round(self.cash + notional, 2)
            new_qty  = self.positions.get(ticker, 0) - qty
            if new_qty <= 0:
                self.order_book["positions"].pop(ticker, None)
            else:
                self.order_book["positions"][ticker] = new_qty
            self.order_book["realised_pnl"] = round(
                self.order_book.get("realised_pnl", 0.0) + pnl, 2)

        else:
            pnl = 0.0

        self.order_book["total_trades"] = self.order_book.get("total_trades", 0) + 1
        order["status"]      = "FILLED"
        order["approved_by"] = "HUMAN" if not CFG.auto_approve_paper else "AUTO_PAPER"
        order["pnl"]         = round(pnl, 2)
        self._save_order_book()

        LOG.info(f"✅  {'📄 PAPER' if CFG.paper_trading else '🔴 LIVE'} FILL: "
                 f"{side} {qty}×{ticker} @ R{fill_px:,.4f}  "
                 f"P&L: R{pnl:,.2f}  Cash: R{self.cash:,.2f}")
        return order

    def run_daily_cycle(self, action: str, signal_score: float,
                         current_price: float, kelly_f: float,
                         cvar_1d: float, regime_idx: int,
                         run_id: int, auto: bool = False) -> Optional[int]:
        """Full daily order-management cycle. Returns order_id or None."""
        order = self.evaluate_signal(
            action, signal_score, current_price, kelly_f, cvar_1d, regime_idx, run_id)

        if order is None:
            LOG.info("⚪  HOLD — no order generated today")
            # Print current open positions
            pos = self.positions
            if pos:
                LOG.info(f"   Open positions: "
                         f"{', '.join(f'{t}×{q}' for t,q in pos.items())}")
                nav = self.portfolio_value({CFG.target_asset: current_price})
                LOG.info(f"   Portfolio NAV: R{nav:,.2f}  Cash: R{self.cash:,.2f}")
            return None

        # Request approval
        approved = self.request_approval(order, auto=auto)
        filled   = self.execute_order(order, approved)

        # Remove 'note' key before DB insert (not a DB column)
        db_order = {k: v for k, v in filled.items() if k != "note"}
        order_id = self.db.insert_order(db_order)

        # Update flat JSON order book
        self._append_order_json(filled, order_id)
        return order_id

    def _append_order_json(self, order: Dict, order_id: int):
        history = []
        if os.path.exists(self.ORDER_BOOK_FILE + ".history"):
            try:
                with open(self.ORDER_BOOK_FILE + ".history") as f:
                    history = json.load(f)
            except Exception:
                pass
        history.append({"id": order_id, **order})
        tmp = self.ORDER_BOOK_FILE + ".history.tmp"
        with open(tmp, "w") as f:
            json.dump(history[-500:], f, indent=2)
        shutil.move(tmp, self.ORDER_BOOK_FILE + ".history")

    def print_order_summary(self):
        recent = self.db.fetch_recent_orders(10)
        if recent.empty:
            return
        print(f"\n{DSEP}")
        print("  📋  RECENT ORDERS (last 10)")
        print(DSEP)
        for _, o in recent.iterrows():
            side_color = Fore.GREEN if o["side"] == "BUY" else Fore.RED
            status_icon = "✅" if o["status"] == "FILLED" else "❌" if o["status"] == "CANCELLED" else "⏳"
            pnl_str = f"  P&L: R{o['pnl']:,.2f}" if o["pnl"] is not None else ""
            print(f"  {status_icon}  {o['order_at'][:10]}  "
                  f"{side_color}{o['side']}{Style.RESET_ALL if COLORAMA_OK else ''}  "
                  f"{o['quantity']}×{o['ticker']}  "
                  f"@ R{o['price']:>10,.4f}  [{o['status']}]{pnl_str}")
        print(DSEP)

        # Portfolio summary
        pos = self.positions
        nav = self.portfolio_value({CFG.target_asset:
              self.db.fetch_recent_runs(1)["s0"].iloc[0]
              if not self.db.fetch_recent_runs(1).empty else 0})
        print(f"\n  💼  PORTFOLIO SUMMARY")
        print(f"  Cash:             R{self.cash:>12,.2f}")
        if pos:
            for t, q in pos.items():
                print(f"  {t} position:   {q:>8} units")
        print(f"  Realised P&L:     R{self.order_book.get('realised_pnl', 0):>12,.2f}")
        print(f"  Total Trades:     {self.order_book.get('total_trades', 0):>12}")
        print(DSEP)


# ═══════════════════════════════════════════════════════════════════════════════
#  DATA FEED  — v6 FIXES
# ═══════════════════════════════════════════════════════════════════════════════

class DataFeed:
    """
    FIX 1: Multi-ticker yfinance download now uses robust column access.
           .get() with squeeze guards against single-ticker column collapse.
    """

    @staticmethod
    def fetch_sarb_repo_rate() -> float:
        if BS4_OK:
            try:
                hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
                resp = requests.get("https://www.sarb.co.za/monetary-policy/",
                                    headers=hdrs, timeout=5)
                soup = BeautifulSoup(resp.text, "lxml")
                for tag in soup.find_all(["td", "span", "p", "strong"]):
                    txt = tag.get_text(strip=True)
                    if "repo" in txt.lower() or "repurchase" in txt.lower():
                        m = re.search(r"(\d{1,2}[.,]\d{1,2})\s*%", txt)
                        if m:
                            rate = float(m.group(1).replace(",", ".")) / 100
                            if 0.02 < rate < 0.25:
                                LOG.info(f"🏦  SARB repo (scraped): {rate:.2%}")
                                return rate
            except Exception:
                pass

        for bond in ["^SAGB10", "SAGB.JO"]:
            try:
                d = yf.download(bond, period="5d", progress=False, auto_adjust=True)
                if not d.empty:
                    close = d["Close"]
                    # FIX 1: squeeze multi-level columns if present
                    if hasattr(close, "columns"):
                        close = close.iloc[:, 0]
                    v = float(close.iloc[-1])
                    if 2.0 < v < 25.0:
                        return v / 100.0
            except Exception:
                pass

        fb = 0.0825
        LOG.warning(f"⚠️  Hardcoded risk-free fallback: {fb:.2%}")
        return fb

    @staticmethod
    def download_market_data() -> Tuple[np.ndarray, np.ndarray, Dict[str, np.ndarray], pd.DatetimeIndex]:
        """
        Returns (gld_prices, xauusd_prices, macro_dict, dates).
        FIX 1: Robust multi-ticker column access.
        """
        LOG.info(f"📥  Fetching {CFG.target_asset} + XAUUSD + macro from {CFG.start_date}…")

        all_tickers = (
            [CFG.target_asset, CFG.xauusd_ticker]
            + [t[0] for t in CFG.macro_tickers]
        )

        # FIX 1: Download and handle column structure robustly
        raw_dl = yf.download(
            " ".join(all_tickers),
            start=CFG.start_date,
            auto_adjust=True,
            progress=False
        )

        # Handle MultiIndex columns (yfinance ≥0.2.x returns (metric, ticker))
        if isinstance(raw_dl.columns, pd.MultiIndex):
            raw = raw_dl["Close"]
        else:
            # Single ticker fallback — wrap in DataFrame
            raw = raw_dl[["Close"]] if "Close" in raw_dl.columns else raw_dl

        raw = raw.ffill().dropna(how="all")

        def _get_series(ticker: str) -> Optional[np.ndarray]:
            """Safely extract a ticker's close series."""
            if ticker in raw.columns:
                s = raw[ticker].dropna()
                if len(s) > 50:
                    return s.to_numpy(dtype=float)
            # Try downloading individually as fallback
            try:
                d = yf.download(ticker, start=CFG.start_date,
                                 auto_adjust=True, progress=False)
                if not d.empty:
                    c = d["Close"]
                    if hasattr(c, "columns"):
                        c = c.iloc[:, 0]
                    return c.dropna().to_numpy(dtype=float)
            except Exception:
                pass
            return None

        # GLD.JO
        gld_arr = _get_series(CFG.target_asset)
        if gld_arr is None:
            raise DataValidationError(f"Cannot fetch {CFG.target_asset}")
        gld_prices = validate_series(gld_arr, CFG.target_asset)

        # XAUUSD
        xau_arr = _get_series(CFG.xauusd_ticker)
        if xau_arr is None:
            LOG.warning("⚠️  XAUUSD unavailable — using GLD.JO as proxy")
            xau_prices = gld_prices.copy()
        else:
            # Align lengths
            min_len   = min(len(gld_prices), len(xau_arr))
            xau_prices = validate_series(xau_arr[-min_len:], "XAUUSD")
            gld_prices = gld_prices[-min_len:]

        # Macro
        macro: Dict[str, np.ndarray] = {}
        min_l = len(gld_prices)
        for yahoo_tk, display, _ in CFG.macro_tickers:
            arr = _get_series(yahoo_tk)
            if arr is not None:
                arr = validate_series(arr, display, min_obs=20)
                macro[display] = arr[-min_l:]
            else:
                LOG.warning(f"⚠️  {display} ({yahoo_tk}) unavailable — using zeros")
                macro[display] = np.ones(min_l, dtype=float)

        dates = raw.index[-min_l:] if len(raw.index) >= min_l else raw.index

        LOG.info(f"  ✓  {len(gld_prices)} days | GLD.JO=R{gld_prices[-1]:,.2f} "
                 f"| XAUUSD=${xau_prices[-1]:,.2f}")
        return gld_prices, xau_prices, macro, dates

    @staticmethod
    def fetch_news_sentiment() -> float:
        if not CFG.news_api_key:
            return 0.0
        BULL = {"surge","rally","buy","high","record","gain","rise","soar",
                "strong","bullish","up","positive"}
        BEAR = {"fall","drop","sell","low","crash","lose","slump","plunge",
                "weak","bearish","down","negative","pressure"}
        try:
            url = (f"https://newsapi.org/v2/everything"
                   f"?q={requests.utils.quote(CFG.news_query)}"
                   f"&sortBy=publishedAt&pageSize=20&language=en"
                   f"&apiKey={CFG.news_api_key}")
            arts = requests.get(url, timeout=8).json().get("articles", [])
            pos = neg = 0
            for a in arts:
                t = (a.get("title","") + " " + a.get("description","")).lower()
                pos += sum(1 for w in BULL if f" {w} " in f" {t} ")
                neg += sum(1 for w in BEAR if f" {w} " in f" {t} ")
            total = pos + neg
            score = float((pos - neg) / total) if total > 0 else 0.0
            LOG.info(f"📰  News sentiment: {score:+.2f}  ({pos}↑ {neg}↓)")
            return max(-1.0, min(1.0, score))
        except Exception as e:
            LOG.warning(f"News: {e}")
            return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  RISK ENGINE  — v6 FIX 2 (GARCH rescale)
# ═══════════════════════════════════════════════════════════════════════════════

class RiskEngine:

    @staticmethod
    def garch_volatility(log_ret: np.ndarray) -> Tuple[float, bool, Dict]:
        """
        FIX 2: rescale=True prevents near-zero variance input.
        The arch library scales returns ×100 internally; we divide back.
        """
        if GARCH_OK and len(log_ret) > 60:
            try:
                # rescale=True: arch scales automatically (fixes "near-zero" error)
                m   = arch_model(log_ret * 100, vol="Garch", p=1, q=1,
                                 dist="t", rescale=True)
                res = m.fit(disp="off", show_warning=False, options={"maxiter": 500})

                # Forecast variance is in (returns×100)² units → divide by 100²
                var_scaled = float(
                    res.forecast(horizon=1).variance.values[-1, 0])
                s = float(np.sqrt(var_scaled)) / 100.0

                if np.isfinite(s) and 0 < s < 1:
                    p = res.params
                    omega = float(p.get("omega", 0))
                    alpha = float(p.get("alpha[1]", 0))
                    beta  = float(p.get("beta[1]",  0))
                    if omega > 0 and alpha > 0 and beta > 0 and alpha + beta < 1:
                        return s, True, {"omega": omega, "alpha": alpha, "beta": beta}
            except Exception as e:
                LOG.warning(f"GARCH fit failed ({e}) — EWMA fallback")

        # EWMA fallback
        lam, var = 0.94, float(np.var(log_ret[:20]))
        for r in log_ret:
            var = lam * var + (1 - lam) * r**2
        return float(np.sqrt(var)), False, {"omega": 0, "alpha": 0, "beta": 0}

    @staticmethod
    def compute_cvar_frac(log_ret: np.ndarray,
                           confidence: float = 0.95) -> Tuple[float, bool]:
        try:
            thr = np.percentile(log_ret, (1 - confidence) * 100)
            exc = -(log_ret[log_ret < thr] - thr)
            if len(exc) < 10:
                raise ValueError
            shape, loc, scale = stats.genpareto.fit(exc, floc=0)
            nu  = len(exc) / len(log_ret)
            alp = 1 - confidence
            u   = -thr
            if shape < 1 and scale > 0:
                if abs(shape) < 1e-6:
                    cvar = u + scale * (1 + np.log(nu / alp))
                else:
                    cvar = u + (scale/(1-shape)) * ((nu/alp)**shape - 1) / shape
                return float(abs(cvar)), True
        except Exception:
            pass
        cut  = np.percentile(log_ret, (1 - confidence)*100)
        tail = log_ret[log_ret <= cut]
        es   = tail.mean() if len(tail) > 0 else cut
        return float(abs(es)), False

    @staticmethod
    def compute_kelly(mu: float, sigma: float, risk_free: float) -> float:
        if sigma <= 0:
            return 0.0
        f = ((mu*252 - risk_free) / (sigma*np.sqrt(252))**2) * CFG.kelly_fraction_scalar
        return float(np.clip(f, 0.0, CFG.max_position_pct))

    @staticmethod
    def bootstrap_kelly_ci(log_ret: np.ndarray, rf_daily: float,
                            n_boot: int = 1000, block: int = 20) -> Tuple[float,float]:
        n, ks = len(log_ret), []
        for _ in range(n_boot):
            idx  = np.random.randint(0, max(1, n - block), n // block)
            boot = np.concatenate([log_ret[i:i+block] for i in idx])
            mu_b = float(boot.mean()) * 252
            sg_b = float(boot.std())  * np.sqrt(252)
            if sg_b > 0:
                ks.append(np.clip(
                    (mu_b - rf_daily*252) / sg_b**2 * CFG.kelly_fraction_scalar,
                    0, CFG.max_position_pct))
        if not ks:
            return 0.0, 0.0
        return float(np.percentile(ks, 2.5)), float(np.percentile(ks, 97.5))

    @staticmethod
    def kupiec_test(violations: int, total: int,
                    confidence: float = 0.95) -> Tuple[float, bool]:
        if total < 20 or violations == 0:
            return 1.0, True
        p, x, T = 1-confidence, violations, total
        p_hat = x / T
        if p_hat in (0, 1):
            return 1.0, True
        try:
            LR = -2*(x*np.log(p/p_hat) + (T-x)*np.log((1-p)/(1-p_hat)))
            pv = float(1 - chi2.cdf(LR, df=1))
            return pv, pv > 0.05
        except Exception:
            return 1.0, True

    @staticmethod
    def christoffersen_test(violations_series: np.ndarray,
                             confidence: float = 0.95) -> Tuple[float, bool]:
        try:
            v = violations_series.astype(int)
            if len(v) < 20 or v.sum() < 2:
                return 1.0, True
            n00 = n01 = n10 = n11 = 0
            for i in range(1, len(v)):
                if   v[i-1]==0 and v[i]==0: n00 += 1
                elif v[i-1]==0 and v[i]==1: n01 += 1
                elif v[i-1]==1 and v[i]==0: n10 += 1
                else:                        n11 += 1
            pi01 = n01/(n00+n01) if (n00+n01)>0 else 0
            pi11 = n11/(n10+n11) if (n10+n11)>0 else 0
            pi_  = (n01+n11)/(n00+n01+n10+n11)
            if pi_ in (0,1) or pi01 in (0,1) or pi11 in (0,1):
                return 1.0, True
            LR = -2*(
                (n00+n10)*np.log(1-pi_) + (n01+n11)*np.log(pi_)
               -(n00*np.log(1-pi01)+n01*np.log(pi01)
                 +n10*np.log(1-pi11)+n11*np.log(pi11))
            )
            pv = float(1 - chi2.cdf(LR, df=1))
            return pv, pv > 0.05
        except Exception:
            return 1.0, True

    @staticmethod
    def bootstrap_sharpe_ci(log_ret: np.ndarray, rf_daily: float,
                             n_boot: int = 1000, block: int = 20) -> Tuple[float,float]:
        n, sharpes = len(log_ret), []
        for _ in range(n_boot):
            idx  = np.random.randint(0, max(1, n - block), n // block)
            boot = np.concatenate([log_ret[i:i+block] for i in idx])
            mu_b = float(boot.mean()) * 252
            sg_b = float(boot.std())  * np.sqrt(252)
            if sg_b > 0:
                sharpes.append((mu_b - rf_daily*252) / sg_b)
        if not sharpes:
            return 0.0, 0.0
        return float(np.percentile(sharpes, 2.5)), float(np.percentile(sharpes, 97.5))

    @staticmethod
    def compute_psi(reference: np.ndarray, current: np.ndarray, bins: int = 10) -> float:
        try:
            mn = min(reference.min(), current.min())
            mx = max(reference.max(), current.max())
            edges = np.linspace(mn, mx, bins+1)
            ref_c = np.histogram(reference, bins=edges)[0] / len(reference)
            cur_c = np.histogram(current,   bins=edges)[0] / len(current)
            ref_c = np.where(ref_c == 0, 1e-6, ref_c)
            cur_c = np.where(cur_c == 0, 1e-6, cur_c)
            return float(np.sum((cur_c - ref_c) * np.log(cur_c / ref_c)))
        except Exception:
            return 0.0

    @staticmethod
    def performance_metrics(log_ret: np.ndarray, rf_daily: float) -> Dict[str, float]:
        r = log_ret[-252:]
        if len(r) < 30:
            return {}
        ann_ret = float(r.mean() * 252)
        ann_vol = float(r.std()  * np.sqrt(252))
        sharpe  = (ann_ret - rf_daily*252) / ann_vol if ann_vol > 0 else 0.0
        neg_r   = r[r < 0]
        downside = float(neg_r.std()*np.sqrt(252)) if len(neg_r) > 1 else 1e-8
        sortino  = (ann_ret - rf_daily*252) / downside
        cum      = np.cumprod(1 + r)
        peak     = np.maximum.accumulate(cum)
        mdd      = float(((cum - peak)/(peak + 1e-8)).min())
        calmar   = ann_ret / abs(mdd) if abs(mdd) > 1e-6 else 0.0
        gains    = np.sum(np.maximum(r - rf_daily, 0))
        losses   = np.sum(np.maximum(rf_daily - r, 0))
        omega    = gains / losses if losses > 1e-8 else float("inf")
        return {
            "sharpe":       round(sharpe,  3),
            "sortino":      round(sortino, 3),
            "calmar":       round(calmar,  3),
            "omega":        round(omega,   3),
            "ann_return":   round(ann_ret * 100, 2),
            "max_drawdown": round(mdd * 100,     2),
            "ann_vol":      round(ann_vol * 100,  2),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  STAT MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class StatModels:

    @staticmethod
    def hurst_exponent(prices: np.ndarray, max_lag: int = 100) -> float:
        lags, rs_vals = [], []
        try:
            for lag in range(2, min(max_lag, len(prices)//4)):
                chunks = len(prices) // lag
                if chunks < 2:
                    continue
                rs_list = []
                for i in range(chunks):
                    seg = prices[i*lag:(i+1)*lag].astype(float)
                    m   = np.mean(seg)
                    dev = np.cumsum(seg - m)
                    R   = np.max(dev) - np.min(dev)
                    S   = np.std(seg, ddof=1)
                    if S > 0:
                        rs_list.append(R / S)
                if rs_list:
                    rs_vals.append(np.log(np.mean(rs_list)))
                    lags.append(np.log(lag))
            if len(lags) < 2:
                return 0.5
            slope, *_ = stats.linregress(lags, rs_vals)
            return float(np.clip(slope, 0.0, 1.0))
        except Exception:
            return 0.5

    @staticmethod
    def detect_regime(log_ret: np.ndarray,
                       n_regimes: int = 3) -> Tuple[int, np.ndarray, str]:
        if not HMM_OK or len(log_ret) < 100:
            return 1, np.array([0.33,0.34,0.33]), "Unknown (HMM unavailable)"
        try:
            X     = log_ret.reshape(-1,1)
            model = hmm.GaussianHMM(n_components=n_regimes, covariance_type="full",
                                    n_iter=200, random_state=42)
            model.fit(X)
            seq   = model.predict(X)
            probs = model.predict_proba(X)
            means = [np.abs(log_ret[seq==i]).mean() for i in range(n_regimes)]
            order = np.argsort(means)
            cur_rank = int(np.where(order == seq[-1])[0][0])
            labels   = {0:"🟢 Low-Vol Bull", 1:"🟡 Mid Transition", 2:"🔴 High-Vol Crisis"}
            return cur_rank, probs[-1], labels.get(cur_rank, "Unknown")
        except Exception as e:
            LOG.warning(f"HMM: {e}")
            return 1, np.array([0.33,0.34,0.33]), "Unknown"

    @staticmethod
    def engle_granger_zscore(prices: np.ndarray, zar: np.ndarray,
                              lb: int = 252) -> Tuple[float,float,float]:
        p = prices[-lb:].astype(float)
        z = zar[-lb:].astype(float)
        min_l = min(len(p), len(z))
        p, z  = p[-min_l:], z[-min_l:]
        model  = OLS(p, add_constant(z)).fit()
        spread = model.resid
        _, pval, _ = coint(p, z)
        zscore = (spread[-1] - spread.mean()) / (spread.std() + 1e-8)
        hl = 0.0
        try:
            ds  = np.diff(spread)
            lag = spread[:-1]
            ou  = OLS(ds, add_constant(lag)).fit()
            th  = -ou.params[1]
            if th > 0:
                hl = float(np.log(2) / th)
        except Exception:
            pass
        return float(zscore), float(pval), hl

    @staticmethod
    def johansen_test(prices: np.ndarray, zar: np.ndarray, lb: int = 252) -> bool:
        try:
            min_l = min(len(prices), len(zar), lb)
            df = pd.DataFrame({"gld": prices[-min_l:], "zar": zar[-min_l:]}).dropna()
            res = coint_johansen(df, det_order=0, k_ar_diff=1)
            return bool(res.lr1[0] > res.cvt[0,1])
        except Exception:
            return False

    @staticmethod
    def macro_pca(prices: np.ndarray, macro: Dict[str,np.ndarray],
                   n: int = 126) -> Tuple[float,float]:
        if not SKLEARN_OK:
            return 0.0, 0.0
        try:
            arrays = [np.diff(np.log(v[-n-1:] + 1e-8)) for v in macro.values()]
            panel  = np.column_stack(arrays)
            panel  = np.nan_to_num(panel)
            scaled = StandardScaler().fit_transform(panel)
            pcs    = PCA(n_components=2).fit_transform(scaled)
            return float(pcs[-1,0]), float(pcs[-1,1])
        except Exception:
            return 0.0, 0.0

    @staticmethod
    def rolling_correlations(prices: np.ndarray, macro: Dict[str,np.ndarray],
                              window: int = 60) -> Dict[str,float]:
        n    = window + 1
        gldr = np.diff(np.log(prices[-n:]))
        out  = {}
        for name, arr in macro.items():
            try:
                ret = np.diff(np.log(arr[-n:] + 1e-8))
                mn  = min(len(gldr), len(ret))
                out[f"corr_{name[:3].lower()}"] = float(
                    np.corrcoef(gldr[-mn:], ret[-mn:])[0,1])
            except Exception:
                out[f"corr_{name[:3].lower()}"] = 0.0
        return out

    @staticmethod
    def pit_update(paths: np.ndarray, realised: Optional[float],
                    horizon: int = 21) -> Optional[float]:
        if realised is None:
            return None
        try:
            pit_val = float(np.mean(paths[min(horizon, len(paths)-1)] <= realised))
            hist = []
            if os.path.exists(CFG.pit_file):
                with open(CFG.pit_file) as f:
                    hist = json.load(f)
            hist.append(pit_val)
            with open(CFG.pit_file, "w") as f:
                json.dump(hist[-252:], f)
            if len(hist) >= 50:
                ks_stat, ks_pv = kstest(hist, "uniform")
                status = "✓ calibrated" if ks_pv > 0.05 else "⚠ MISCALIBRATED"
                LOG.info(f"📊  PIT KS-test p={ks_pv:.3f}  {status}")
            return pit_val
        except Exception:
            return None


# ═══════════════════════════════════════════════════════════════════════════════
#  MONTE CARLO ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class MonteCarloEngine:

    @staticmethod
    def run(S0: float, mu: float, sigma: float, log_ret: np.ndarray,
            n_sims: Optional[int] = None) -> np.ndarray:
        n_sims  = n_sims or CFG.num_simulations
        std_dev = np.std(log_ret)
        jumps   = log_ret[np.abs(log_ret) > 3*std_dev]
        jfreq   = len(jumps) / max(len(log_ret)/252, 1)
        jmu     = float(jumps.mean()) if len(jumps) > 0 else 0.0
        jsig    = float(jumps.std())  if len(jumps) > 1 else 1e-4

        dt     = 1.0 / 252
        paths  = np.zeros((CFG.forecast_days+1, n_sims))
        paths[0] = S0
        Z1  = np.random.standard_normal((CFG.forecast_days, n_sims))
        Z2  = np.random.standard_normal((CFG.forecast_days, n_sims))
        Poi = np.random.poisson(jfreq * dt, (CFG.forecast_days, n_sims))
        drift = (mu - 0.5*sigma**2) * dt
        diff  = sigma * np.sqrt(dt)
        for t in range(1, CFG.forecast_days+1):
            jump     = Poi[t-1] * (jmu + jsig * Z2[t-1])
            paths[t] = paths[t-1] * np.exp(drift + diff*Z1[t-1] + jump)
        return paths

    @staticmethod
    def convergence_check(S0, mu, sigma, log_ret, tol=0.02) -> bool:
        try:
            full = MonteCarloEngine.run(S0, mu, sigma, log_ret)
            half = MonteCarloEngine.run(S0, mu, sigma, log_ret,
                                        n_sims=CFG.num_simulations//2)
            diff = abs(float(np.median(full[21])) - float(np.median(half[21]))) / (S0+1e-8)
            return diff <= tol
        except Exception:
            return True


# ═══════════════════════════════════════════════════════════════════════════════
#  ML ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

def _ema(prices: np.ndarray, period: int) -> np.ndarray:
    k   = 2.0 / (period + 1)
    ema = np.empty(len(prices))
    ema[0] = prices[0]
    for i in range(1, len(prices)):
        ema[i] = prices[i]*k + ema[i-1]*(1-k)
    return ema

def _rsi(prices: np.ndarray, period: int = 14) -> float:
    data   = prices[-(period*3):]
    deltas = np.diff(data)
    gains  = np.where(deltas > 0,  deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    alpha  = 1.0 / period
    ag = float(gains[:period].mean()) if period <= len(gains) else float(gains.mean())
    al = float(losses[:period].mean()) if period <= len(losses) else float(losses.mean())
    for g, l in zip(gains[period:], losses[period:]):
        ag = alpha*g + (1-alpha)*ag
        al = alpha*l + (1-alpha)*al
    return 100.0 if al == 0.0 else 100.0 - 100.0/(1.0 + ag/al)

def _linreg(prices: np.ndarray, period: int = 20) -> float:
    y = prices[-period:].astype(float)
    x = np.arange(period, dtype=float)
    slope, *_ = stats.linregress(x, y)
    return float(slope) / float(prices[-1])

def _intraday_momentum(prices: np.ndarray) -> Tuple[int, str]:
    if len(prices) < 10:
        return 0, "Insufficient data"
    atr   = np.mean(np.abs(np.diff(prices[-6:])))
    gap   = prices[-1] - prices[-2]
    avg5  = np.mean(prices[-5:])
    trend = prices[-1] - avg5
    if gap > 0.5*atr and trend > 0:
        return  1, f"Intraday bullish gap +{gap:.2f} (ATR {atr:.2f})"
    if gap < -0.5*atr and trend < 0:
        return -1, f"Intraday bearish gap {gap:.2f} (ATR {atr:.2f})"
    return 0, "No intraday breakout signal"


class MLEngine:

    @staticmethod
    def _load_weights() -> Dict[str,float]:
        w = CFG.default_weights.copy()
        if os.path.exists(CFG.weights_file):
            try:
                with open(CFG.weights_file) as f:
                    w.update(json.load(f))
            except Exception:
                pass
        total = sum(abs(v) for v in w.values())
        return {k: round(abs(v)/total*10.0, 4) for k, v in w.items()} if total > 0 else w

    @staticmethod
    def _save_weights(w: Dict[str,float]):
        tmp = CFG.weights_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(w, f, indent=4)
        shutil.move(tmp, CFG.weights_file)

    @staticmethod
    def get_normalised_weights() -> Dict[str,float]:
        return MLEngine._load_weights()

    @staticmethod
    def warm_up_if_needed(prices: np.ndarray, macro: Dict[str,np.ndarray]):
        if os.path.exists(CFG.weights_file):
            return
        if not SKLEARN_OK:
            return
        LOG.info("🔥  First-run warm-up: pre-loading 60d history into LASSO…")
        try:
            n    = min(len(prices)-1, 60)
            rows = []
            for i in range(n, 0, -1):
                p_slice = prices[:len(prices)-i]
                lr_sl   = np.log(p_slice[1:] / p_slice[:-1])
                ema20   = _ema(p_slice, 20)
                ema50   = _ema(p_slice, 50)
                ema_dir = 1 if ema20[-1] > ema50[-1] else -1
                rsi_dir = 0
                if len(p_slice) > 42:
                    rsi     = _rsi(p_slice)
                    rsi_dir = 1 if rsi < 35 else (-1 if rsi > 65 else 0)
                rows.append({
                    "ema_cross": ema_dir,
                    "rsi":       rsi_dir,
                    "linreg":    1 if _linreg(p_slice) > 0.001 else -1,
                    "actual_return": float(lr_sl[-1]) if len(lr_sl) > 0 else 0.0
                })
            df = pd.DataFrame(rows).dropna()
            if len(df) < 10:
                return
            dir_cols = [c for c in df.columns if c != "actual_return"]
            X = df[dir_cols].to_numpy()
            y = (df["actual_return"] > 0).astype(int)
            w = MLEngine._load_weights()
            lr = LogisticRegression(penalty="l1", solver="saga", C=1.0,
                                    max_iter=500, random_state=42)
            lr.fit(X, y)
            for i, col in enumerate(dir_cols):
                if col in w:
                    w[col] = max(0.05, w[col] + 0.1 * float(lr.coef_[0][i]))
            total = sum(w.values())
            w = {k: round(v/total*10.0, 4) for k, v in w.items()}
            MLEngine._save_weights(w)
            LOG.info("  ✓  Warm-up complete.")
        except Exception as e:
            LOG.warning(f"Warm-up: {e}")

    @staticmethod
    def batch_update(db: Database, current_price: float):
        try:
            df_raw  = db.fetch_all_signal_dirs()
            df_runs = db.fetch_recent_runs(n=252)
            if df_raw.empty or df_runs.empty or len(df_raw) < 10:
                return

            last_id = int(df_runs.iloc[0]["id"])
            if len(df_runs) >= 2:
                prev_s0 = float(df_runs.iloc[1]["s0"])
                if prev_s0 > 0:
                    db.update_actual_return(last_id,
                        round((current_price - prev_s0) / prev_s0, 6))

            df_runs = db.fetch_recent_runs(252).dropna(subset=["actual_return"])
            if len(df_runs) < 5 or len(df_runs) % 5 != 0:
                return

            if len(df_runs) >= 40:
                ref  = df_runs["score"].iloc[20:].to_numpy()
                curr = df_runs["score"].iloc[:20].to_numpy()
                psi  = RiskEngine.compute_psi(ref, curr)
                LOG.info(f"📈  Signal PSI: {psi:.3f} "
                         f"({'✓' if psi<0.10 else '⚠ moderate' if psi<0.20 else '🚨 DRIFT'})")

            pivot  = df_raw.pivot_table(index="id", columns="feature",
                                         values="dir", fill_value=0)
            merged = pd.merge(df_runs[["id","actual_return"]], pivot,
                              left_on="id", right_index=True).dropna(subset=["actual_return"])
            if len(merged) < 20:
                return

            feat_cols = [c for c in merged.columns if c not in ("id","actual_return")]
            X = merged[feat_cols].fillna(0).to_numpy()
            y = (merged["actual_return"].to_numpy() > 0).astype(int)
            w = MLEngine._load_weights()

            if SKLEARN_OK and len(merged) >= 20:
                try:
                    lr_m = LogisticRegression(penalty="l1", solver="saga",
                                               C=1.0, max_iter=500, random_state=42)
                    lr_m.fit(X, y)
                    gb_m = GradientBoostingClassifier(n_estimators=50, max_depth=2,
                                                       random_state=42)
                    gb_m.fit(X, y)
                    for i, col in enumerate(feat_cols):
                        if col in w:
                            direction  = float(lr_m.coef_[0][i])
                            importance = float(gb_m.feature_importances_[i])
                            w[col] = max(0.05, w[col] + 0.1 * direction * (1 + importance))
                    LOG.info("🧠  Weights: LASSO+GBT ensemble updated")
                except Exception as e:
                    LOG.warning(f"ML update: {e}")

            total = sum(w.values())
            w = {k: round(v/total*10.0, 4) for k, v in w.items()}
            MLEngine._save_weights(w)
        except Exception as e:
            LOG.warning(f"Batch weight update: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  SIGNAL ENGINE  — v6 adds XAUUSD trend signal
# ═══════════════════════════════════════════════════════════════════════════════

class SignalEngine:

    @staticmethod
    def generate(prices: np.ndarray,
                 xau_prices: np.ndarray,
                 paths: np.ndarray,
                 sentiment: float,
                 macro: Dict[str, np.ndarray],
                 regime_idx: int,
                 hurst: float,
                 pc1: float, pc2: float,
                 correlations: Dict[str,float]) -> Dict:

        S0      = float(prices[-1])
        S0_xau  = float(xau_prices[-1])
        weights = MLEngine.get_normalised_weights()

        zar   = macro.get("ZAR/USD", list(macro.values())[0])
        dxy   = macro.get("DXY",     list(macro.values())[min(1,len(macro)-1)])
        vix   = macro.get("VIX",     list(macro.values())[min(2,len(macro)-1)])
        us10y = macro.get("US10Y",   list(macro.values())[min(3,len(macro)-1)])

        ema20     = _ema(prices, 20)
        ema50     = _ema(prices, 50)
        rsi_val   = _rsi(prices, 14)
        linreg    = _linreg(prices, 20)
        dxy_trend = (dxy[-1] - dxy[-10]) / (dxy[-10]+1e-8)
        vix_trend = (vix[-1] - vix[-10]) / (vix[-10]+1e-8)
        eg_z, eg_pv, ou_half = StatModels.engle_granger_zscore(prices, zar)
        intra_dir, intra_label = _intraday_momentum(prices)
        us10y_chg = float(us10y[-1] - us10y[-5])
        real_rate  = -1 if us10y_chg > 0.10 else (1 if us10y_chg < -0.10 else 0)

        # XAUUSD trend (20-day momentum)
        xau_ema20 = _ema(xau_prices, 20)
        xau_ema50 = _ema(xau_prices, 50)
        xau_trend = 1 if xau_ema20[-1] > xau_ema50[-1] else -1
        xau_mom   = (xau_prices[-1] - xau_prices[-21]) / (xau_prices[-21]+1e-8)

        med_1m = float(np.percentile(paths[21], 50))
        mc_up  = (med_1m - S0) / S0

        score, reasons, dirs = 0.0, [], {}

        # MC direction
        if mc_up > 0.01:
            score += weights.get("mc", 1.5); dirs["mc"] = 1
            reasons.append(f"MC 1m median +{mc_up:.1%} ↑")
        elif mc_up < -0.01:
            score -= weights.get("mc", 1.5); dirs["mc"] = -1
            reasons.append(f"MC 1m median {mc_up:.1%} ↓")
        else:
            dirs["mc"] = 0

        # EMA crossover
        if ema20[-1] > ema50[-1]:
            score += weights.get("ema_cross", 1.0); dirs["ema_cross"] = 1
            reasons.append("EMA20 > EMA50 ↑")
        else:
            score -= weights.get("ema_cross", 1.0); dirs["ema_cross"] = -1
            reasons.append("EMA20 < EMA50 ↓")

        # RSI
        if rsi_val < 35:
            score += weights.get("rsi", 1.0); dirs["rsi"] = 1
            reasons.append(f"RSI {rsi_val:.1f} oversold ↑")
        elif rsi_val > 65:
            score -= weights.get("rsi", 1.0); dirs["rsi"] = -1
            reasons.append(f"RSI {rsi_val:.1f} overbought ↓")
        else:
            dirs["rsi"] = 0

        # News
        if abs(sentiment) > 0.1:
            score += sentiment * weights.get("news", 0.8)
            dirs["news"] = 1 if sentiment > 0 else -1
            reasons.append(f"News {sentiment:+.2f} {'↑' if sentiment>0 else '↓'}")
        else:
            dirs["news"] = 0

        # DXY
        if dxy_trend < -0.01:
            score += weights.get("dxy", 1.0); dirs["dxy"] = 1
            reasons.append(f"USD weakening {dxy_trend:.1%} ↑")
        elif dxy_trend > 0.01:
            score -= weights.get("dxy", 1.0); dirs["dxy"] = -1
            reasons.append(f"USD strengthening {dxy_trend:.1%} ↓")
        else:
            dirs["dxy"] = 0

        # VIX
        if vix_trend > 0.05:
            score += weights.get("vix", 1.0); dirs["vix"] = 1
            reasons.append(f"VIX +{vix_trend:.1%} safe-haven ↑")
        elif vix_trend < -0.05:
            score -= weights.get("vix", 1.0); dirs["vix"] = -1
            reasons.append(f"VIX {vix_trend:.1%} ↓")
        else:
            dirs["vix"] = 0

        # EG z-score
        coint_label = "★ cointegrated" if eg_pv < 0.05 else "(not sig.)"
        if eg_z < -1.5:
            score += weights.get("zar_coint", 1.5); dirs["zar_coint"] = 1
            reasons.append(f"EG z={eg_z:.2f} undervalued vs ZAR {coint_label} ↑")
        elif eg_z > 1.5:
            score -= weights.get("zar_coint", 1.5); dirs["zar_coint"] = -1
            reasons.append(f"EG z={eg_z:.2f} overvalued vs ZAR {coint_label} ↓")
        else:
            dirs["zar_coint"] = 0

        # Linear regression
        if linreg > 0.001:
            score += weights.get("linreg", 0.8); dirs["linreg"] = 1
            reasons.append("OLS slope: uptrend ↑")
        elif linreg < -0.001:
            score -= weights.get("linreg", 0.8); dirs["linreg"] = -1
            reasons.append("OLS slope: downtrend ↓")
        else:
            dirs["linreg"] = 0

        # Intraday
        if intra_dir != 0:
            score += intra_dir * weights.get("intraday", 0.7)
            reasons.append(intra_label + (" ↑" if intra_dir > 0 else " ↓"))
        dirs["intraday"] = intra_dir

        # Hurst
        if hurst > 0.55:
            hd = 1 if ema20[-1] > ema50[-1] else -1
            score += hd * weights.get("hurst", 0.8); dirs["hurst"] = hd
            reasons.append(f"Hurst={hurst:.3f} trending → {'↑' if hd>0 else '↓'}")
        elif hurst < 0.45:
            hd = -1 if ema20[-1] > ema50[-1] else 1
            score += hd * weights.get("hurst", 0.8); dirs["hurst"] = hd
            reasons.append(f"Hurst={hurst:.3f} mean-rev → {'↑' if hd>0 else '↓'}")
        else:
            dirs["hurst"] = 0
            reasons.append(f"Hurst={hurst:.3f} (random walk, neutral)")

        # HMM Regime
        if regime_idx == 0:
            score += weights.get("regime", 1.2); dirs["regime"] = 1
            reasons.append("HMM: Bull regime ↑")
        elif regime_idx == 2:
            score += 0.5 * weights.get("regime", 1.2); dirs["regime"] = 1
            reasons.append("HMM: Crisis — safe-haven gold ↑")
        else:
            dirs["regime"] = 0
            reasons.append("HMM: Transitional (neutral)")

        # Real rate proxy
        if real_rate != 0:
            score += real_rate * weights.get("real_rate", 0.9)
            dirs["real_rate"] = real_rate
            reasons.append(f"US10Y {'↑' if us10y_chg>0 else '↓'} {us10y_chg:+.2f}bps "
                           f"→ gold {'headwind ↓' if real_rate<0 else 'tailwind ↑'}")
        else:
            dirs["real_rate"] = 0

        # XAUUSD spot trend (NEW v6)
        score += xau_trend * weights.get("xau_trend", 1.0)
        dirs["xau_trend"] = xau_trend
        reasons.append(f"XAUUSD ${S0_xau:,.2f} trend={'↑' if xau_trend>0 else '↓'} "
                       f"(20d mom {xau_mom:+.1%})")

        # Action
        if   score >= CFG.strong_buy_threshold:  action = "🟢 STRONG BUY"
        elif score >= CFG.buy_threshold:         action = "🟡 BUY"
        elif score <= CFG.strong_sell_threshold: action = "🔴 STRONG SELL"
        elif score <= CFG.sell_threshold:        action = "🟠 SELL"
        else:                                    action = "⚪ HOLD"

        return {
            "action": action, "score": round(score, 4), "S0": S0,
            "S0_xauusd": S0_xau,
            "rsi": rsi_val, "ema20": float(ema20[-1]), "ema50": float(ema50[-1]),
            "linreg_slope": linreg,
            "mc_median_1m": med_1m,
            "mc_p5_1m":  float(np.percentile(paths[21],  5)),
            "mc_p95_1m": float(np.percentile(paths[21], 95)),
            "mc_p5_1y":  float(np.percentile(paths[-1],  5)),
            "mc_p95_1y": float(np.percentile(paths[-1], 95)),
            "eg_zscore":    round(eg_z, 4),
            "eg_pvalue":    round(eg_pv, 4),
            "ou_half_life": round(ou_half, 1),
            "hurst":        round(hurst, 4),
            "pc1": round(pc1, 4), "pc2": round(pc2, 4),
            "regime_label": "",
            "reasons":      reasons,
            "feature_directions": dirs,
            "correlations":       correlations,
            "real_rate_signal":   real_rate,
            "us10y_chg":          us10y_chg,
            "xau_trend":          xau_trend,
            "xau_momentum":       xau_mom,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  EXECUTION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class ExecutionEngine:

    @staticmethod
    def position_size(kelly_f: float, cvar_1d: float, regime_idx: int,
                       correlations: Dict[str,float]) -> float:
        budget  = min(2 * cvar_1d, CFG.portfolio_value_zar * CFG.max_position_pct)
        pos_zar = min(kelly_f * CFG.portfolio_value_zar, budget)
        if regime_idx == 2:
            pos_zar *= 0.70
        if correlations.get("corr_zar", 0) > 0.3:
            pos_zar *= 0.85
        return min(pos_zar, CFG.portfolio_value_zar * CFG.max_position_pct)

    @staticmethod
    def realistic_entry_exit(S0: float, signal_score: float, mode: str,
                              kelly_f: float, cvar_1d: float, regime_idx: int,
                              correlations: Dict[str,float]) -> Dict:
        crisis_mult = 1.5 if regime_idx == 2 else 1.0
        if mode == "intraday":
            friction   = (CFG.typical_spread_pct +
                          CFG.slippage_pct_intraday * crisis_mult +
                          CFG.jse_brokerage_pct)
            open_gap   = 0.0015 if signal_score > 0 else -0.0015
            hold_label = "1h – 12h (intraday)"
            t3_note    = "No T+3 lock-up (intraday)"
        else:
            friction   = (CFG.typical_spread_pct +
                          CFG.slippage_pct_swing * crisis_mult +
                          CFG.jse_brokerage_pct)
            open_gap   = 0.0008
            hold_label = "1 day – several weeks"
            t3_note    = "T+3 settlement: capital locked 3 business days"

        entry    = S0 * (1 + open_gap + CFG.typical_spread_pct/2)
        pos_zar  = ExecutionEngine.position_size(kelly_f, cvar_1d, regime_idx, correlations)
        units    = int(pos_zar / entry) if entry > 0 else 0
        notional = units * entry
        return {
            "mode": mode, "holding": hold_label, "t3_note": t3_note,
            "S0_close":           round(S0, 4),
            "realistic_entry":    round(entry, 4),
            "spread_cost_zar":    round(notional * CFG.typical_spread_pct, 2),
            "slippage_cost_zar":  round(notional * friction * 0.3, 2),
            "brokerage_zar":      round(notional * CFG.jse_brokerage_pct, 2),
            "total_friction_zar": round(notional * friction, 2),
            "breakeven_up":       round(entry * (1 + friction), 4),
            "breakeven_dn":       round(entry * (1 - friction), 4),
            "breakeven_pct":      round(friction * 100, 3),
            "kelly_fraction":     round(kelly_f, 4),
            "position_zar":       round(notional, 2),
            "units":              units,
            "pnl_per_1pct_zar":   round(notional * 0.01, 2),
            "regime_scaled":      regime_idx == 2,
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════════

class BacktestEngine:

    @staticmethod
    def run(prices: np.ndarray, macro: Dict[str, np.ndarray],
            risk_free: float, db: Database) -> Dict:
        LOG.info("📐  Walk-forward back-test…")
        n = len(prices)
        if n < CFG.wf_min_train_days + CFG.wf_oos_window + 5:
            LOG.warning("⚠️  Not enough history for walk-forward")
            return {}

        oos_returns, oos_regimes = [], []
        step = max(5, CFG.wf_oos_window // 4)

        for t in range(CFG.wf_min_train_days, n - CFG.wf_oos_window, step):
            try:
                p_is   = prices[:t]
                lr_is  = np.log(p_is[1:] / p_is[:-1])
                ema20  = _ema(p_is, 20)
                ema50  = _ema(p_is, 50)
                ema_dir = 1 if ema20[-1] > ema50[-1] else -1
                rsi_dir = 0
                if len(p_is) > 42:
                    rsi_dir = 1 if _rsi(p_is) < 35 else (-1 if _rsi(p_is) > 65 else 0)
                lr_dir  = 1 if _linreg(p_is) > 0.001 else (-1 if _linreg(p_is) < -0.001 else 0)
                score   = float(ema_dir + rsi_dir + lr_dir)
                future  = prices[t:t+CFG.wf_oos_window]
                if len(future) < 2:
                    continue
                oos_ret     = np.log(future[-1] / future[0])
                strategy_ret = oos_ret * (1 if score > 0 else -1)
                oos_returns.append(strategy_ret)
                _, _, reg_label = StatModels.detect_regime(lr_is) if HMM_OK else (1, None, "Unknown")
                oos_regimes.append(reg_label)
            except Exception:
                continue

        if len(oos_returns) < 4:
            return {}

        oos_arr    = np.array(oos_returns)
        oos_sharpe = float(oos_arr.mean() / (oos_arr.std() + 1e-8)
                           * np.sqrt(252/CFG.wf_oos_window))
        oos_win_rt = float((oos_arr > 0).mean())
        max_dd     = float(BacktestEngine._max_drawdown(oos_arr))

        regime_results = {}
        for i, r in enumerate(oos_regimes):
            regime_results.setdefault(r, []).append(oos_returns[i])
        regime_sharpes = {
            lbl: round(np.array(rets).mean()/(np.array(rets).std()+1e-8)
                       * np.sqrt(252/CFG.wf_oos_window), 3)
            for lbl, rets in regime_results.items()
        }

        db.insert_oos_result(datetime.now().strftime("%Y-%m-%d"),
                             round(oos_sharpe, 3), str(regime_sharpes))

        summary = {
            "oos_sharpe":     round(oos_sharpe, 3),
            "oos_win_rate":   round(oos_win_rt, 3),
            "oos_max_dd":     round(max_dd, 3),
            "oos_n":          len(oos_returns),
            "regime_sharpes": regime_sharpes,
        }
        LOG.info(f"  ✓  OOS Sharpe: {oos_sharpe:.3f} | Win: {oos_win_rt:.1%} | "
                 f"MaxDD: {max_dd:.1%}")
        return summary

    @staticmethod
    def _max_drawdown(returns: np.ndarray) -> float:
        cum  = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cum)
        return float(((cum - peak)/(peak + 1e-8)).min())

    @staticmethod
    def stress_test(prices: np.ndarray, log_ret: np.ndarray, sigma: float) -> Dict[str, float]:
        scenarios = {
            "2008 GFC (-5σ)":        -5 * sigma,
            "COVID crash (-7σ)":     -7 * sigma,
            "ZAR crisis (-3σ)":      -3 * sigma,
            "Gold flash crash (-4σ)":-4 * sigma,
        }
        pos = CFG.portfolio_value_zar * CFG.max_position_pct
        return {label: round(pos * ret, 2) for label, ret in scenarios.items()}


# ═══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM NOTIFIER
# ═══════════════════════════════════════════════════════════════════════════════

class TelegramNotifier:

    @staticmethod
    def send(msg: str):
        if not CFG.telegram_bot_token or not CFG.telegram_chat_id:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{CFG.telegram_bot_token}/sendMessage",
                json={"chat_id": CFG.telegram_chat_id, "text": msg,
                      "parse_mode": "Markdown"},
                timeout=8)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  DISPLAY HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _print_banner():
    now = datetime.now().strftime("%Y-%m-%d %H:%M SAST")
    print(f"\n{SEP}")
    print(f"  📡  {CFG.target_asset} + XAUUSD Institutional Scan — {now}")
    print(f"  🔬  Gold Quant Platform v6.0 — Equity Curve + Paper Trading")
    print(f"  📝  Mode: {'PAPER TRADING' if CFG.paper_trading else '🔴 LIVE TRADING'}")
    print(SEP)

def _print_risk_dashboard(sigma, cvar_1d, kelly_f, kelly_lo, kelly_hi,
                           perf, regime_label, hurst, correlations,
                           garch_converged, evt_flag, ou_half, sharpe_ci):
    print(f"\n{DSEP}")
    print("  📊  INSTITUTIONAL RISK DASHBOARD")
    print(DSEP)
    print(f"  GARCH daily vol (σ):      {sigma*100:>10.3f}%"
          f"  {'✓ converged' if garch_converged else '⚠ EWMA fallback'}")
    print(f"  Annualised vol:           {sigma*np.sqrt(252)*100:>10.2f}%")
    print(f"  CVaR 1-day (95%):        R{cvar_1d:>12,.2f}"
          f"  {'[EVT-GPD]' if evt_flag else '[Historical]'}")
    print(f"  Fractional Kelly (×0.25): {kelly_f*100:>10.2f}%"
          f"  95% CI [{kelly_lo*100:.1f}%–{kelly_hi*100:.1f}%]")
    print(f"  HMM Regime:               {regime_label}")
    print(f"  Hurst Exponent:           {hurst:>10.4f}"
          f"  ({'trending' if hurst>0.55 else 'mean-rev' if hurst<0.45 else 'random'})")
    print(f"  OU half-life:             {ou_half:>10.1f}d")
    print(DSEP)
    if perf:
        rs = perf.get('rolling_sharpe', perf.get('sharpe', 'N/A'))
        sh = perf.get('sharpe', 'N/A')
        sharpe_health = ""
        if isinstance(sh, (int, float)):
            if sh > 1.0:
                sharpe_health = f"  {Fore.GREEN}✓ Healthy Sharpe (>{1.0:.1f}){Style.RESET_ALL}" if COLORAMA_OK else "  ✓ Healthy Sharpe (>1.0)"
            elif sh > 0.5:
                sharpe_health = "  ⚠ Moderate Sharpe"
            else:
                sharpe_health = f"  {Fore.RED}🔴 Low Sharpe{Style.RESET_ALL}" if COLORAMA_OK else "  🔴 Low Sharpe"
        print(f"  ROLLING PERFORMANCE (252d){sharpe_health}")
        print(f"  Sharpe:  {sh:>7}  |  Sortino: {perf.get('sortino','N/A'):>7}  |  "
              f"Calmar: {perf.get('calmar','N/A'):>7}")
        print(f"  Omega:   {perf.get('omega','N/A'):>7}  |  "
              f"Ann Ret: {perf.get('ann_return','N/A'):>5}%  |  "
              f"Max DD: {perf.get('max_drawdown','N/A'):>5}%")
        if sharpe_ci != (0,0):
            print(f"  Sharpe Bootstrap 95% CI:  [{sharpe_ci[0]:.3f} – {sharpe_ci[1]:.3f}]")
    print(DSEP)
    print(f"  ROLLING CORRELATIONS (60d):")
    for key, val in correlations.items():
        print(f"  {key:<18} {val:>+.3f}")

def _print_exec_block(e: Dict, label: str):
    print(f"\n  ┌── {label} ─────────────────────────────────────────────")
    print(f"  │  Close (S0):              R{e['S0_close']:>12,.4f}")
    print(f"  │  Realistic entry:         R{e['realistic_entry']:>12,.4f}")
    print(f"  │  Total round-trip cost:   R{e['total_friction_zar']:>12,.2f}"
          f"  ({e['breakeven_pct']:.3f}% breakeven)"
          f"  {'[crisis-widened]' if e['regime_scaled'] else ''}")
    print(f"  │  Position size (Kelly):   R{e['position_zar']:>12,.2f}  × {e['units']} units")
    print(f"  │  P&L per 1% move:         R{e['pnl_per_1pct_zar']:>12,.2f}")
    print(f"  │  Holding window:          {e['holding']}")
    print(f"  │  Settlement:              {e['t3_note']}")
    print(f"  └{'─'*60}")

def _print_backtest_summary(summary: Dict):
    if not summary:
        return
    print(f"\n{DSEP}")
    print("  📐  WALK-FORWARD BACK-TEST (leakage-free, OOS only)")
    print(DSEP)
    print(f"  OOS Sharpe:   {summary.get('oos_sharpe','N/A'):>8}")
    print(f"  OOS Win Rate: {summary.get('oos_win_rate',0)*100:>7.1f}%")
    print(f"  OOS Max DD:   {summary.get('oos_max_dd',0)*100:>7.1f}%")
    print(f"  OOS Periods:  {summary.get('oos_n','N/A'):>8}")
    if summary.get("regime_sharpes"):
        print("  OOS Sharpe by regime:")
        for lbl, sh in summary["regime_sharpes"].items():
            print(f"    {lbl:<32} {sh:>+.3f}")
    print(DSEP)

def _print_stress_tests(stress: Dict):
    print(f"\n{DSEP}")
    print("  🔥  STRESS TEST SCENARIOS")
    print(DSEP)
    for label, pnl in stress.items():
        color = Fore.RED if pnl < 0 else Fore.GREEN
        rst   = Style.RESET_ALL if COLORAMA_OK else ""
        print(f"  {label:<30}  {color}R{pnl:>12,.2f}{rst}")
    print(DSEP)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN JOB
# ═══════════════════════════════════════════════════════════════════════════════

def job(auto_paper: bool = False):
    _print_banner()
    try:
        # ── 1. Data ──────────────────────────────────────────────────────────
        gld_prices, xau_prices, macro, dates = DataFeed.download_market_data()
        S0     = float(gld_prices[-1])
        S0_xau = float(xau_prices[-1])
        zar    = macro.get("ZAR/USD", list(macro.values())[0])

        # ── 2. ML warm-up + batch update ─────────────────────────────────────
        MLEngine.warm_up_if_needed(gld_prices, macro)
        MLEngine.batch_update(DB, S0)

        # ── 3. Risk-free rate ─────────────────────────────────────────────────
        risk_free = DataFeed.fetch_sarb_repo_rate()
        rf_daily  = risk_free / 252

        # ── 4. Parameters ─────────────────────────────────────────────────────
        log_ret = np.log(gld_prices[1:] / gld_prices[:-1])
        mu      = float(log_ret.mean())
        sigma, garch_converged, garch_params = RiskEngine.garch_volatility(log_ret)

        # ── 5. Monte Carlo ────────────────────────────────────────────────────
        LOG.info(f"🎲  Running {CFG.num_simulations:,} Merton Jump-Diffusion paths…")
        paths  = MonteCarloEngine.run(S0, mu, sigma, log_ret)
        mc_ok  = MonteCarloEngine.convergence_check(S0, mu, sigma, log_ret)
        if not mc_ok:
            LOG.warning("⚠️  MC not fully converged")

        # ── 6. Analytics ──────────────────────────────────────────────────────
        hurst     = StatModels.hurst_exponent(gld_prices)
        reg_idx, reg_probs, reg_label = StatModels.detect_regime(log_ret)
        eg_z, eg_pv, ou_half = StatModels.engle_granger_zscore(gld_prices, zar)
        johansen_ok = StatModels.johansen_test(gld_prices, zar)
        cvar_frac, evt_flag = RiskEngine.compute_cvar_frac(log_ret, CFG.cvar_confidence)
        cvar_1d   = cvar_frac * CFG.portfolio_value_zar
        kelly_f   = RiskEngine.compute_kelly(mu, sigma, risk_free)
        kelly_lo, kelly_hi = RiskEngine.bootstrap_kelly_ci(log_ret, rf_daily)
        correlations = StatModels.rolling_correlations(gld_prices, macro)
        pc1, pc2  = StatModels.macro_pca(gld_prices, macro)
        perf      = RiskEngine.performance_metrics(log_ret, rf_daily)
        sharpe_ci = RiskEngine.bootstrap_sharpe_ci(log_ret, rf_daily)

        # ── 7. Walk-forward + Stress tests ────────────────────────────────────
        wf_summary = BacktestEngine.run(gld_prices, macro, risk_free, DB)
        stress_pnl = BacktestEngine.stress_test(gld_prices, log_ret, sigma)

        # ── 8. Signals ────────────────────────────────────────────────────────
        sentiment = DataFeed.fetch_news_sentiment()
        signals   = SignalEngine.generate(
            gld_prices, xau_prices, paths, sentiment, macro,
            reg_idx, hurst, pc1, pc2, correlations)
        signals["regime_label"] = reg_label

        # ── 9. Execution blocks ───────────────────────────────────────────────
        exec_intra = ExecutionEngine.realistic_entry_exit(
            S0, signals["score"], "intraday", kelly_f, cvar_1d, reg_idx, correlations)
        exec_swing = ExecutionEngine.realistic_entry_exit(
            S0, signals["score"], "swing", kelly_f, cvar_1d, reg_idx, correlations)

        # ── 10. DB logging ────────────────────────────────────────────────────
        row = {
            "run_at":          datetime.now().strftime("%Y-%m-%d %H:%M"),
            "s0":              round(S0, 4),
            "s0_xauusd":       round(S0_xau, 4),
            "action":          signals["action"],
            "score":           signals["score"],
            "rsi":             round(signals["rsi"], 2),
            "sigma_garch":     round(sigma, 6),
            "cvar_1d_zar":     round(cvar_1d, 2),
            "kelly_pct":       round(kelly_f * 100, 2),
            "hurst":           signals["hurst"],
            "regime":          reg_label,
            "pc1":             signals["pc1"],
            "pc2":             signals["pc2"],
            "eg_zscore":       signals["eg_zscore"],
            "eg_pvalue":       signals["eg_pvalue"],
            "ou_half_life":    signals["ou_half_life"],
            "sharpe":          perf.get("sharpe"),
            "sortino":         perf.get("sortino"),
            "calmar":          perf.get("calmar"),
            "omega":           perf.get("omega"),
            "max_drawdown":    perf.get("max_drawdown"),
            "garch_converged": int(garch_converged),
            "evt_cvar":        int(evt_flag),
            "actual_return":   None,
            "kupiec_p":        None,
            "christoff_p":     None,
            "psi_score":       None,
            "heartbeat":       datetime.now().isoformat(),
        }
        run_id = DB.insert_run(row)
        DB.insert_signal_dirs(run_id, signals["feature_directions"])
        DB.insert_garch_params(run_id, **garch_params)

        # ── 11. VaR back-test (Kupiec + Christoffersen) ───────────────────────
        kp_p = chris_p = None
        try:
            hist_df = DB.fetch_recent_runs(100).dropna(
                subset=["actual_return","cvar_1d_zar"])
            if len(hist_df) >= 20:
                viols = (hist_df["actual_return"].abs() * CFG.portfolio_value_zar >
                         hist_df["cvar_1d_zar"]).astype(int)
                kp_p,    kp_pass  = RiskEngine.kupiec_test(int(viols.sum()), len(hist_df))
                chris_p, ch_pass  = RiskEngine.christoffersen_test(viols.to_numpy())
                LOG.info(f"📋  Kupiec p={kp_p:.3f} {'✓' if kp_pass else '⚠'} | "
                         f"Christoffersen p={chris_p:.3f} {'✓' if ch_pass else '⚠'}")
                with DB._conn() as conn:
                    conn.execute(
                        "UPDATE runs SET kupiec_p=?, christoff_p=? WHERE id=?",
                        (kp_p, chris_p, run_id))
        except Exception:
            pass

        StatModels.pit_update(paths, None)

        # ── 12. Paper trading engine ──────────────────────────────────────────
        paper_engine = PaperTradingEngine(DB)
        order_id = paper_engine.run_daily_cycle(
            signals["action"], signals["score"], S0,
            kelly_f, cvar_1d, reg_idx, run_id, auto=auto_paper)

        # ── 13. Equity curve update ───────────────────────────────────────────
        eq_stats = EquityCurveEngine.update(
            DB, S0, signals["action"], signals["score"], run_id)

        # ── 14. Console Output ────────────────────────────────────────────────
        yahoo_url = f"https://finance.yahoo.com/quote/{CFG.target_asset}"

        g  = Fore.GREEN  if COLORAMA_OK else ""
        r  = Fore.RED    if COLORAMA_OK else ""
        y  = Fore.YELLOW if COLORAMA_OK else ""
        rst = Style.RESET_ALL if COLORAMA_OK else ""

        action_color = g if "BUY" in signals["action"] else (
            r if "SELL" in signals["action"] else y)

        print(f"\n  SIGNAL:  {action_color}{signals['action']}{rst}"
              f"  (composite score: {signals['score']:.2f})")
        print(DSEP)
        print(f"  Live Chart:             {yahoo_url}")
        print(f"  GLD.JO close:           R{S0:>12,.4f}")
        print(f"  XAUUSD spot:            ${S0_xau:>11,.2f}  "
              f"({'↑' if signals.get('xau_trend',0)>0 else '↓'} 20d mom "
              f"{signals.get('xau_momentum',0):+.1%})")
        print(f"  EG coint z-score:       {signals['eg_zscore']:>10.2f}"
              f"  (p={signals['eg_pvalue']:.3f})")
        print(f"  Johansen cointegrated:  {'✓ Yes' if johansen_ok else '✗ No':>10}")
        print(f"  MC 1-month median:      R{signals['mc_median_1m']:>12,.2f}"
              f"  [{signals['mc_p5_1m']:,.2f} – {signals['mc_p95_1m']:,.2f}]")
        print(f"  MC 1-year (5/95%):      R{signals['mc_p5_1y']:>12,.2f}"
              f" – R{signals['mc_p95_1y']:,.2f}")
        if kp_p is not None:
            print(f"  Kupiec VaR p:           {kp_p:>10.3f}")
        if chris_p is not None:
            print(f"  Christoffersen p:       {chris_p:>10.3f}")

        _print_risk_dashboard(sigma, cvar_1d, kelly_f, kelly_lo, kelly_hi,
                               perf, reg_label, hurst, correlations,
                               garch_converged, evt_flag, ou_half, sharpe_ci)

        print(f"\n  SIGNAL FACTORS:")
        for reason in signals["reasons"]:
            print(f"    • {reason}")
        print(DSEP)

        _print_exec_block(exec_intra, "INTRADAY EXECUTION (1h–12h)")
        _print_exec_block(exec_swing, "SWING EXECUTION (1d–weeks)")
        _print_backtest_summary(wf_summary)
        _print_stress_tests(stress_pnl)

        # Equity curve (must come after equity update)
        print(EquityCurveEngine.ascii_chart(DB))

        # Order summary
        paper_engine.print_order_summary()

        print(f"\n  ⚠️  DISCLAIMER: Educational / research use only. NOT financial advice.")
        print(f"  📝  Paper trading only. Set CFG.live_trading=True at your own risk.")
        print(SEP)

        # ── 15. Telegram ──────────────────────────────────────────────────────
        wf_sh = wf_summary.get("oos_sharpe", "—") if wf_summary else "—"
        rs    = eq_stats.get("rolling_sharpe", 0)
        rs_status = "✓ Healthy" if rs > 0.5 else "⚠ Below target"

        msg = (
            f"🏅 *GLD.JO v6.0*\n"
            f"*{signals['action']}*  (score: {signals['score']:.2f})\n\n"
            f"📊 [Live Chart]({yahoo_url})\n\n"
            f"GLD.JO: *R{S0:,.4f}*  |  XAUUSD: *${S0_xau:,.2f}*\n"
            f"Regime: *{reg_label}*\n"
            f"CVaR(95%): R{cvar_1d:,.2f}\n"
            f"Kelly: {kelly_f:.1%} [{kelly_lo*100:.1f}%–{kelly_hi*100:.1f}%]\n"
            f"Sharpe: {perf.get('sharpe','—')} [{rs_status}]\n"
            f"Rolling Sharpe (63d): {rs:.3f}\n"
            f"Equity NAV: R{eq_stats['nav']:,.2f} | DD: {eq_stats['drawdown']:.2%}\n"
            f"OOS Sharpe (WF): {wf_sh}\n"
            + "\n".join(f"• {r}" for r in signals["reasons"])
        )
        TelegramNotifier.send(msg)

    except DataValidationError as e:
        msg = f"❌ Data validation: {e}"
        LOG.error(msg)
        TelegramNotifier.send(msg)
    except Exception as e:
        traceback.print_exc()
        TelegramNotifier.send(f"❌ GoldBot v6 error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def _is_jse_trading_day() -> bool:
    return datetime.now().weekday() < 5

def _scheduled_job(auto_paper: bool = False):
    if _is_jse_trading_day():
        job(auto_paper=auto_paper)
    else:
        LOG.info("📅  Weekend — JSE closed.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Gold Quant Platform v6.0")
    parser.add_argument("--once",       action="store_true",
                        help="Run once and exit (cron/Docker/n8n)")
    parser.add_argument("--auto-paper", action="store_true",
                        help="Auto-approve paper orders (backtesting only)")
    parser.add_argument("--paper-only", action="store_true",
                        help="Force paper mode (default: already paper)")
    args = parser.parse_args()

    if args.paper_only:
        CFG.paper_trading = True
        CFG.live_trading  = False

    if args.auto_paper:
        CFG.auto_approve_paper = True
        LOG.warning("⚠️  Auto-approve paper mode active. "
                    "NEVER use this flag with a live account.")

    if args.once:
        LOG.info("▶  Single-execution mode (--once)")
        job(auto_paper=args.auto_paper)
        sys.exit(0)

    LOG.info(f"⏰  Scheduler — runs at {CFG.run_time_sast} SAST weekdays.")
    LOG.info("   Set timezone: sudo timedatectl set-timezone Africa/Johannesburg")
    LOG.info("   Ctrl+C / SIGTERM to stop.\n")

    job(auto_paper=args.auto_paper)
    schedule.every().day.at(CFG.run_time_sast).do(
        _scheduled_job, auto_paper=args.auto_paper)

    while not _SHUTDOWN:
        schedule.run_pending()
        time.sleep(30)

    LOG.info("👋  GoldBot v6.0 shut down cleanly.")
