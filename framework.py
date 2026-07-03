"""
===============================================================
  QUANTITATIVE TRADING FRAMEWORK  –  v7.6 (Non-Linear Price)
  Multivariate Forecasting via OLS, Random Forest, & SVR
===============================================================
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import random

# ── Optional ML Libraries ─────────────────────────────────────
try:
    import xgboost as xgb  # type: ignore
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
except ImportError:
    HMM_AVAILABLE = False

try:
    from statsmodels.tsa.stattools import adfuller
    STATSMODELS_AVAILABLE = True
except ImportError:
    STATSMODELS_AVAILABLE = False

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.svm import SVR
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    import optuna  # type: ignore
    OPTUNA_AVAILABLE = True
    optuna.logging.set_verbosity(optuna.logging.WARNING)
except ImportError:
    OPTUNA_AVAILABLE = False
    optuna = None  # type: ignore

# ── Optional External Integrations ────────────────────────────
try:
    import MetaTrader5 as mt5  # type: ignore
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# ============================================================
# 0.  SESSION STATE
# ============================================================
DEFAULTS = dict(
    opt_train_window=180,
    opt_retrain_every=20,
    opt_n_steps=3,
    opt_pred_comp_cutoff=0.60,
    opt_pred_bear_cutoff=0.60,
    opt_pred_adx_cutoff=25.0,
    opt_smooth_span=5,
    use_arima_entry=False,
    price_model_type="Random Forest"
)
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ============================================================
# 1.  DATA & NEWS LAYER
# ============================================================

def detect_annual_factor(index):
    if len(index) < 2: return 252
    diffs = pd.Series(index).diff().dt.total_seconds().dropna()
    med = diffs.median()
    if med <= 86_400: return 252
    elif med <= 7 * 86_400: return 52
    elif med <= 31 * 86_400: return 12
    return 252

def fetch_market_data(symbol, source, lookback_days=365):
    if source == "Yahoo Finance":
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)
        df = yf.download(symbol, start=start_date, end=end_date, progress=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index.name = "Date"
        return df

    elif source == "MetaTrader 5":
        if not MT5_AVAILABLE:
            st.error("MetaTrader5 library not found.")
            return pd.DataFrame()
        if not mt5.initialize():
            st.error(f"MT5 init failed. Error: {mt5.last_error()}")
            return pd.DataFrame()
        utc_from = datetime.now()
        rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_D1, utc_from, lookback_days)
        if rates is None:
            st.error(f"MT5 returned no data for {symbol}.")
            return pd.DataFrame()
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df.set_index("time", inplace=True)
        df.rename(columns={"open": "Open", "high": "High", "low": "Low",
                            "close": "Close", "tick_volume": "Volume"}, inplace=True)
        df.index.name = "Date"
        return df
    return pd.DataFrame()

# ============================================================
# 2.  TECHNICAL INDICATORS & FORECASTERS
# ============================================================

def compute_atr(df, period=14):
    high, low, prev_close = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low  - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def compute_adx(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_high = high.shift(1)
    prev_low  = low.shift(1)
    plus_dm  = np.where((high - prev_high) > (prev_low - low), np.maximum(high - prev_high, 0), 0)
    minus_dm = np.where((prev_low - low) > (high - prev_high), np.maximum(prev_low - low, 0), 0)
    tr_raw = pd.concat([high - low, (high - close.shift(1)).abs(), (low  - close.shift(1)).abs()], axis=1).max(axis=1)
    atr14 = tr_raw.ewm(span=period, adjust=False).mean()
    plus_dm_s  = pd.Series(plus_dm,  index=df.index).ewm(span=period, adjust=False).mean()
    minus_dm_s = pd.Series(minus_dm, index=df.index).ewm(span=period, adjust=False).mean()
    plus_di  = 100 * plus_dm_s  / atr14
    minus_di = 100 * minus_dm_s / atr14
    dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx, plus_di, minus_di

def compute_200sma(df):
    return df["Close"].rolling(200, min_periods=1).mean()

def calculate_metrics(returns_series, annual_factor=252):
    returns_series = returns_series.dropna()
    if len(returns_series) == 0:
        return {"sharpe": 0, "max_drawdown": 0, "total_return": 0, "win_rate": 0, "profit_factor": 0, "num_trades": 0}
    equity = (1 + returns_series).cumprod()
    std = returns_series.std()
    sharpe = (returns_series.mean() / std) * np.sqrt(annual_factor) if std != 0 else 0
    rolling_max = equity.cummax()
    max_dd = ((equity - rolling_max) / rolling_max).min()
    total_ret = equity.iloc[-1] - 1
    wins   = returns_series[returns_series > 0]
    losses = returns_series[returns_series < 0]
    win_rate = len(wins) / len(returns_series) if len(returns_series) else 0
    pf = (wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 1e-9 else np.nan
    return {
        "sharpe": sharpe, "max_drawdown": max_dd, "total_return": total_ret,
        "win_rate": win_rate, "profit_factor": pf, "num_trades": len(returns_series),
    }

def predict_n_steps(transmat_dict, current_state, n_steps=2):
    p_from_bear = [transmat_dict.get(-1, {}).get(-1, 0.5), transmat_dict.get(-1, {}).get(1, 0.5)]
    p_from_bull = [transmat_dict.get(1,  {}).get(-1, 0.5), transmat_dict.get(1,  {}).get(1, 0.5)]
    T = np.array([p_from_bear, p_from_bull])
    Tn = np.linalg.matrix_power(T, n_steps)
    vec = np.array([1, 0]) if current_state == -1 else np.array([0, 1])
    probs = vec @ Tn
    return {-1: probs[0], 1: probs[1]}

def calc_position_size(account_balance, risk_pct, atr_value, price, point_value=1.0):
    risk_amount = account_balance * risk_pct
    if atr_value <= 0 or price <= 0: return 0.0
    stop_distance = atr_value
    if point_value * stop_distance <= 0: return 0.0
    return risk_amount / (stop_distance * point_value)

# --- Fast ARIMA(1,1,0) via Logit Transformation ---
def fast_ar1_forecast_logit(series, n_steps, max_val=1.0):
    eps = 1e-4
    s = np.clip(series, eps, max_val - eps)
    if max_val > 1.0: s = s / max_val
    y = np.log(s / (1.0 - s))
    dy = np.diff(y)
    
    if len(dy) < 3 or np.std(dy) < 1e-6:
        pred_y = y[-1]
    else:
        X, Y = dy[:-1], dy[1:]
        var_x = np.var(X)
        phi = 0 if var_x < 1e-8 else np.cov(X, Y)[0, 1] / var_x
        phi = np.clip(phi, -0.95, 0.95)
        
        last_dy, pred_y = dy[-1], y[-1]
        for _ in range(n_steps):
            next_dy = phi * last_dy
            pred_y += next_dy
            last_dy = next_dy
            
    pred_raw = 1.0 / (1.0 + np.exp(-pred_y))
    if max_val > 1.0: pred_raw *= max_val
    return pred_raw

# --- Multivariate Price Projection: Linear vs Non-Linear ---
def multivariate_price_forecast(model_type, prices, bulls, bears, adxs, n_steps, pred_bull, pred_bear, pred_adx):
    """
    Maps historical changes in (Bull, Bear, ADX) to historical log returns,
    then predicts future log returns based on forecasted changes in those indicators.
    """
    log_p = np.log(prices)
    n_samples = len(prices) - n_steps
    
    if n_samples < 10:
        return prices[-1]
        
    Y = np.zeros(n_samples)
    X_features = np.zeros((n_samples, 3)) # dBull, dBear, dADX
    
    # Train: Map historical changes in indicators to historical changes in log price
    for i in range(n_samples):
        Y[i] = log_p[i + n_steps] - log_p[i]
        X_features[i, 0] = bulls[i + n_steps] - bulls[i]
        X_features[i, 1] = bears[i + n_steps] - bears[i]
        X_features[i, 2] = adxs[i + n_steps] - adxs[i]
        
    # Project: Calculate expected change from Current -> ARIMA Forecast
    d_bull = pred_bull - bulls[-1]
    d_bear = pred_bear - bears[-1]
    d_adx  = pred_adx - adxs[-1]
    X_curr = np.array([[d_bull, d_bear, d_adx]])
    
    if model_type == "Direct OLS":
        # Solve for Beta weights linearly
        X_ols = np.column_stack((np.ones(n_samples), X_features))
        beta = np.linalg.pinv(X_ols.T @ X_ols + np.eye(4)*1e-6) @ X_ols.T @ Y
        pred_log_ret = np.array([1.0, d_bull, d_bear, d_adx]) @ beta
        
    elif model_type == "Random Forest" and SKLEARN_AVAILABLE:
        # Non-Linear Trees
        rf = RandomForestRegressor(n_estimators=20, max_depth=3, random_state=42)
        rf.fit(X_features, Y)
        pred_log_ret = rf.predict(X_curr)[0]
        
    elif model_type == "Support Vector Regression (SVR)" and SKLEARN_AVAILABLE:
        # Non-Linear Kernel Map (requires scaled features)
        scaler_X = StandardScaler()
        scaler_Y = StandardScaler()
        X_scaled = scaler_X.fit_transform(X_features)
        Y_scaled = scaler_Y.fit_transform(Y.reshape(-1, 1)).flatten()
        
        svr = SVR(kernel='rbf', C=1.0, epsilon=0.1)
        svr.fit(X_scaled, Y_scaled)
        
        pred_scaled = svr.predict(scaler_X.transform(X_curr))
        pred_log_ret = scaler_Y.inverse_transform(pred_scaled.reshape(-1, 1))[0, 0]
        
    else:
        # Fallback to linear if Sklearn not installed
        X_ols = np.column_stack((np.ones(n_samples), X_features))
        beta = np.linalg.pinv(X_ols.T @ X_ols + np.eye(4)*1e-6) @ X_ols.T @ Y
        pred_log_ret = np.array([1.0, d_bull, d_bear, d_adx]) @ beta
    
    return np.exp(log_p[-1] + pred_log_ret)

# ============================================================
# 3.  BACKTESTING EXITS
# ============================================================

def apply_sl_tp_exits(df_in, sl_atr_mult=1.5, tp_atr_mult=3.0, trail_atr_mult=2.0, use_trailing=True, cost_per_trade=0.001):
    df = df_in.copy()
    n = len(df)
    equity  = np.ones(n)
    pnl     = np.zeros(n)
    active  = np.zeros(n, dtype=bool)
    entry_p = np.full(n, np.nan)
    sl_p    = np.full(n, np.nan)
    tp_p    = np.full(n, np.nan)

    in_trade, direction, entry_price, sl_price, tp_price, trail_extreme, eq_val = False, 0, 0.0, 0.0, 0.0, 0.0, 1.0
    atr_vals, close, high, low, signals = df["ATR"].values, df["Close"].values, df["High"].values, df["Low"].values, df["Signal_Filtered"].values

    for i in range(1, n):
        c, h, l = close[i], high[i], low[i]
        atr = atr_vals[i] if not np.isnan(atr_vals[i]) else atr_vals[max(0, i-1)]

        if in_trade:
            active[i] = True
            entry_p[i], sl_p[i], tp_p[i] = entry_price, sl_price, tp_price

            if use_trailing:
                if direction == 1:
                    if h > trail_extreme:
                        trail_extreme = h
                        sl_price = max(sl_price, trail_extreme - trail_atr_mult * atr)
                elif direction == -1:
                    if l < trail_extreme:
                        trail_extreme = l
                        sl_price = min(sl_price, trail_extreme + trail_atr_mult * atr)

            exited, exit_price = False, c
            if direction == 1:
                sl_hit, tp_hit = (l <= sl_price), (h >= tp_price)
                if sl_hit and tp_hit: exit_price, exited = sl_price, True
                elif sl_hit: exit_price, exited = sl_price, True
                elif tp_hit: exit_price, exited = tp_price, True
            elif direction == -1:
                sl_hit, tp_hit = (h >= sl_price), (l <= tp_price)
                if sl_hit and tp_hit: exit_price, exited = sl_price, True
                elif sl_hit: exit_price, exited = sl_price, True
                elif tp_hit: exit_price, exited = tp_price, True

            if exited:
                trade_ret = direction * (exit_price - entry_price) / entry_price - cost_per_trade
                eq_val *= (1 + trade_ret)
                pnl[i] = trade_ret
                in_trade, direction = False, 0

            sig = signals[i]
            if not exited and sig != 0 and sig != direction:
                if in_trade:
                    trade_ret = direction * (c - entry_price) / entry_price - 2 * cost_per_trade
                    eq_val *= (1 + trade_ret)
                    pnl[i] = trade_ret
                direction, entry_price = int(sig), c
                sl_price = c - direction * sl_atr_mult * atr
                tp_price = c + direction * tp_atr_mult * atr
                trail_extreme, in_trade = c, True
        else:
            sig = signals[i]
            if sig != 0: 
                direction, entry_price = int(sig), c
                sl_price = c - direction * sl_atr_mult * atr
                tp_price = c + direction * tp_atr_mult * atr
                trail_extreme, in_trade = c, True
                eq_val *= (1 - cost_per_trade)

        equity[i] = eq_val

    df["Equity_Curve_SL"], df["Trade_PnL"], df["Trade_Active"] = equity, pnl, active
    df["SL_Price"], df["TP_Price"] = sl_p, tp_p
    return df

# ============================================================
# 4.  MAIN STRATEGY ENGINE
# ============================================================

def run_ensemble_strategy(df, params, risk_params=None):
    if risk_params is None: risk_params = {}
    df = df.copy()
    metrics = {}

    train_window  = params["train_window"]
    retrain_every = params["retrain_every"]
    n_steps       = params["n_steps"]
    cost_per_trade = params["cost_per_trade"]
    price_model   = params.get("price_model_type", "Direct OLS")
    
    pred_comp_cutoff = params.get("pred_comp_cutoff", 0.60)
    pred_bear_cutoff = params.get("pred_bear_cutoff", 0.60)
    pred_adx_cutoff  = params.get("pred_adx_cutoff", 25.0)

    # Core Indicators
    df["ATR"] = compute_atr(df, period=risk_params.get("atr_period", 14))
    df["ADX"], df["Plus_DI"], df["Minus_DI"] = compute_adx(df, period=14)
    df["SMA200"] = compute_200sma(df)
    df["Market_Returns"] = df["Close"].pct_change()

    # Heikin-Ashi
    ha_close = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4
    ha_open  = np.zeros(len(df))
    ha_open[0] = (df["Open"].iloc[0] + df["Close"].iloc[0]) / 2
    for i in range(1, len(df)): ha_open[i] = (ha_open[i-1] + ha_close.iloc[i-1]) / 2
    df["HA_State"] = np.where(ha_close.values > ha_open, 1, -1)
    df["HA_State_Lag"] = df["HA_State"].shift(1)

    df["Prob_Bull_XGB"] = 0.5; df["Prob_Bear_XGB"] = 0.5
    df["Prob_Bull_HMM"] = 0.5; df["Prob_Bear_HMM"] = 0.5
    df["Prob_Bull_Markov"] = 0.5; df["Prob_Bear_Markov"] = 0.5
    
    ml_features = pd.DataFrame(index=df.index)
    ml_features["Ret"] = df["Market_Returns"].fillna(0)
    ml_features["ATR_Pct"] = (df["ATR"] / df["Close"]).fillna(0)
    ml_features["ADX"] = df["ADX"].fillna(0)
    ml_features["DI_Spread"] = (df["Plus_DI"] - df["Minus_DI"]).fillna(0)
    ml_features["SMA_Dist"] = ((df["Close"] - df["SMA200"]) / df["SMA200"]).fillna(0)
    
    target_series_xgb = df["HA_State"].shift(-n_steps)

    # Walk-Forward Training Loop
    for i in range(train_window, len(df)):
        if (i - train_window) % retrain_every != 0 and i != train_window:
            continue

        # 1. Train XGB
        xgb_model = None
        if XGB_AVAILABLE:
            train_end_idx = i - n_steps
            if train_end_idx > i - train_window:
                X_train = ml_features.iloc[i - train_window : train_end_idx].dropna()
                y_train = target_series_xgb.loc[X_train.index]
                valid_mask = y_train.notna()
                X_train, y_train = X_train[valid_mask], y_train[valid_mask]
                if len(X_train) >= 20:
                    y_train_bin = (y_train == 1).astype(int)
                    if len(y_train_bin.unique()) > 1:
                        xgb_model = xgb.XGBClassifier(n_estimators=50, max_depth=3, learning_rate=0.1, random_state=42, eval_metric="logloss")
                        xgb_model.fit(X_train, y_train_bin)

        # 2. Train HMM
        hmm_model, hmm_transmat, hmm_bull_state, hmm_bear_state = None, None, None, None
        if HMM_AVAILABLE:
            X_train_hmm = ml_features[["Ret", "ATR_Pct"]].iloc[i - train_window : i].dropna()
            if len(X_train_hmm) >= 30:
                hmm_model = GaussianHMM(n_components=2, covariance_type="full", n_iter=100, random_state=42)
                hmm_model.fit(X_train_hmm)
                hmm_bull_state = np.argmax(hmm_model.means_[:, 0])
                hmm_bear_state = 1 - hmm_bull_state
                hmm_transmat = hmm_model.transmat_

        # 3. Train Markov
        window_markov = df["HA_State_Lag"].iloc[i - train_window: i + 1].dropna()
        from_s, to_s = window_markov.iloc[:-1].values, window_markov.iloc[1:].values
        markov_transmat = {}
        for s in [-1, 1]:
            mask = from_s == s
            if mask.sum() == 0: markov_transmat[s] = {-1: 0.5, 1: 0.5}
            else: markov_transmat[s] = {1: (to_s[mask] == 1).sum() / mask.sum(), -1: (to_s[mask] == -1).sum() / mask.sum()}

        # Predict Forward Chunk
        end = min(i + retrain_every, len(df))
        for j in range(i, end):
            p_xgb_bull, p_xgb_bear = 0.5, 0.5
            if xgb_model is not None:
                X_test = ml_features.iloc[[j]]
                if not X_test.isna().any().any():
                    probs = xgb_model.predict_proba(X_test)
                    p_xgb_bull = probs[0, 1] if probs.shape[1] == 2 else (1.0 if xgb_model.classes_[0] == 1 else 0.0)
                    p_xgb_bear = 1.0 - p_xgb_bull
            df.iloc[j, df.columns.get_loc("Prob_Bull_XGB")] = p_xgb_bull
            df.iloc[j, df.columns.get_loc("Prob_Bear_XGB")] = p_xgb_bear

            p_hmm_bull, p_hmm_bear = 0.5, 0.5
            if hmm_model is not None:
                start_idx = max(0, j - 10)
                X_seq = ml_features[["Ret", "ATR_Pct"]].iloc[start_idx : j+1]
                if not X_seq.isna().any().any() and len(X_seq) > 0:
                    curr_probs = hmm_model.predict_proba(X_seq)[-1]
                    T_n = np.linalg.matrix_power(hmm_transmat, n_steps)
                    n_step_probs = curr_probs @ T_n
                    p_hmm_bull = n_step_probs[hmm_bull_state]
                    p_hmm_bear = n_step_probs[hmm_bear_state]
            df.iloc[j, df.columns.get_loc("Prob_Bull_HMM")] = p_hmm_bull
            df.iloc[j, df.columns.get_loc("Prob_Bear_HMM")] = p_hmm_bear

            p_markov_bull, p_markov_bear = 0.5, 0.5
            cs = df["HA_State_Lag"].iloc[j]
            if not pd.isna(cs):
                probs_markov = predict_n_steps(markov_transmat, int(cs), n_steps)
                p_markov_bull = probs_markov.get(1, 0.5)
                p_markov_bear = probs_markov.get(-1, 0.5)
            df.iloc[j, df.columns.get_loc("Prob_Bull_Markov")] = p_markov_bull
            df.iloc[j, df.columns.get_loc("Prob_Bear_Markov")] = p_markov_bear

    # ── Smoothing & Decoupled Composite Generation ──
    span = params.get('smooth_span', 5)
    df['Smooth_XGB_Bull'] = df['Prob_Bull_XGB'].ewm(span=span, adjust=False).mean()
    df['Smooth_XGB_Bear'] = df['Prob_Bear_XGB'].ewm(span=span, adjust=False).mean()
    df['Smooth_HMM_Bull'] = df['Prob_Bull_HMM'].ewm(span=span, adjust=False).mean()
    df['Smooth_HMM_Bear'] = df['Prob_Bear_HMM'].ewm(span=span, adjust=False).mean()
    df['Smooth_Markov_Bull'] = df['Prob_Bull_Markov'].ewm(span=span, adjust=False).mean()
    df['Smooth_Markov_Bear'] = df['Prob_Bear_Markov'].ewm(span=span, adjust=False).mean()

    avail_bull_cols = ['Smooth_Markov_Bull']
    avail_bear_cols = ['Smooth_Markov_Bear']
    if XGB_AVAILABLE:
        avail_bull_cols.append('Smooth_XGB_Bull'); avail_bear_cols.append('Smooth_XGB_Bear')
    if HMM_AVAILABLE:
        avail_bull_cols.append('Smooth_HMM_Bull'); avail_bear_cols.append('Smooth_HMM_Bear')
        
    df['Prob_Consensus_Bull'] = df[avail_bull_cols].mean(axis=1)
    df['Prob_Consensus_Bear'] = df[avail_bear_cols].mean(axis=1)
    
    # ── ARIMA Assumptions Check & Metrics ──
    if STATSMODELS_AVAILABLE:
        try:
            # Price Stationarity (Log Returns)
            raw_close = df['Close'].dropna().values
            log_close = np.log(raw_close)
            diff_log_close = np.diff(log_close)
            metrics["adf_price_raw"] = adfuller(raw_close)[1]
            metrics["adf_price_diff"] = adfuller(diff_log_close)[1]
            
            raw_adx = df['ADX'].dropna().values
            log_adx = np.log(np.clip(raw_adx/100, 1e-4, 1-1e-4) / (1 - np.clip(raw_adx/100, 1e-4, 1-1e-4)))
            diff_log_adx = np.diff(log_adx)
            metrics["adf_adx_raw"] = adfuller(raw_adx)[1]
            metrics["adf_adx_diff"] = adfuller(diff_log_adx)[1]
            
            raw_comp = df['Prob_Consensus_Bull'].dropna().values
            log_comp = np.log(np.clip(raw_comp, 1e-4, 1-1e-4) / (1 - np.clip(raw_comp, 1e-4, 1-1e-4)))
            diff_log_comp = np.diff(log_comp)
            metrics["adf_comp_raw"] = adfuller(raw_comp)[1]
            metrics["adf_comp_diff"] = adfuller(diff_log_comp)[1]
        except Exception:
            pass
            
    # ── ARIMA Dual Logit & Multivariate Price Forecasting Pipeline ──
    pred_c_bull = np.zeros(len(df))
    pred_c_bear = np.zeros(len(df))
    pred_a = np.zeros(len(df))
    pred_p = np.zeros(len(df))
    
    comp_bull_arr = df['Prob_Consensus_Bull'].values
    comp_bear_arr = df['Prob_Consensus_Bear'].values
    adx_arr = df['ADX'].values
    price_arr = df['Close'].values
    
    for k in range(train_window, len(df)):
        # Calculate Rolling 60-bar window for better multivariate stability
        win_start = max(0, k - 60)
        
        w_comp_bull = comp_bull_arr[win_start:k+1]
        w_comp_bear = comp_bear_arr[win_start:k+1]
        w_adx = adx_arr[win_start:k+1]
        w_price = price_arr[win_start:k+1]
        
        # Bounded Indicator forecasts via Logit AR(1)
        pred_c_bull[k] = fast_ar1_forecast_logit(w_comp_bull, n_steps=n_steps, max_val=1.0)
        pred_c_bear[k] = fast_ar1_forecast_logit(w_comp_bear, n_steps=n_steps, max_val=1.0)
        pred_a[k] = fast_ar1_forecast_logit(w_adx, n_steps=n_steps, max_val=100.0)
        
        # Multivariate Price Forecasting using predicted change in Consensus & ADX
        pred_p[k] = multivariate_price_forecast(
            price_model, w_price, w_comp_bull, w_comp_bear, w_adx, n_steps=n_steps,
            pred_bull=pred_c_bull[k], pred_bear=pred_c_bear[k], pred_adx=pred_a[k]
        )
        
    df['ARIMA_Pred_Comp_Bull'] = pred_c_bull
    df['ARIMA_Pred_Comp_Bear'] = pred_c_bear
    df['ARIMA_Pred_ADX'] = pred_a
    df['ARIMA_Pred_Price'] = pred_p
    
    # ── ARIMA Directional Accuracy Metrics ──
    actual_comp_bull_future = df['Prob_Consensus_Bull'].shift(-n_steps)
    actual_comp_bear_future = df['Prob_Consensus_Bear'].shift(-n_steps)
    actual_adx_future = df['ADX'].shift(-n_steps)
    actual_price_future = df['Close'].shift(-n_steps)
    
    valid_idx = actual_comp_bull_future.notna() & (df.index >= df.index[train_window])
    
    actual_comp_bull_dir = np.sign(actual_comp_bull_future[valid_idx] - df['Prob_Consensus_Bull'][valid_idx])
    pred_comp_bull_dir = np.sign(df['ARIMA_Pred_Comp_Bull'][valid_idx] - df['Prob_Consensus_Bull'][valid_idx])
    
    actual_comp_bear_dir = np.sign(actual_comp_bear_future[valid_idx] - df['Prob_Consensus_Bear'][valid_idx])
    pred_comp_bear_dir = np.sign(df['ARIMA_Pred_Comp_Bear'][valid_idx] - df['Prob_Consensus_Bear'][valid_idx])
    
    actual_adx_dir = np.sign(actual_adx_future[valid_idx] - df['ADX'][valid_idx])
    pred_adx_dir = np.sign(df['ARIMA_Pred_ADX'][valid_idx] - df['ADX'][valid_idx])
    
    actual_price_dir = np.sign(actual_price_future[valid_idx] - df['Close'][valid_idx])
    pred_price_dir = np.sign(df['ARIMA_Pred_Price'][valid_idx] - df['Close'][valid_idx])
    
    if len(pred_comp_bull_dir) > 0:
        metrics["arima_comp_bull_acc"] = (actual_comp_bull_dir == pred_comp_bull_dir).mean()
        metrics["arima_comp_bear_acc"] = (actual_comp_bear_dir == pred_comp_bear_dir).mean()
        metrics["arima_adx_acc"] = (actual_adx_dir == pred_adx_dir).mean()
        metrics["arima_price_acc"] = (actual_price_dir == pred_price_dir).mean()

    # ── Highlight Logic (Symmetric Bounded Cutoffs) ──
    pred_adx_rising = df['ARIMA_Pred_ADX'] > df['ARIMA_Pred_ADX'].shift(1)
    
    zone_mask_bull = (df['ARIMA_Pred_Comp_Bull'] >= pred_comp_cutoff) & \
                     (df['ARIMA_Pred_ADX'] >= pred_adx_cutoff) & \
                     pred_adx_rising
                     
    zone_mask_bear = (df['ARIMA_Pred_Comp_Bear'] >= pred_bear_cutoff) & \
                     (df['ARIMA_Pred_ADX'] >= pred_adx_cutoff) & \
                     pred_adx_rising
                
    df['Highlight_Zone_Bull'] = zone_mask_bull.fillna(False)
    df['Highlight_Zone_Bear'] = zone_mask_bear.fillna(False)

    cross_up_bull = df['Highlight_Zone_Bull'] & (~df['Highlight_Zone_Bull'].shift(1).fillna(False))
    cross_up_bear = df['Highlight_Zone_Bear'] & (~df['Highlight_Zone_Bear'].shift(1).fillna(False))

    df['Signal'] = 0
    df.loc[cross_up_bull, 'Signal'] = 1
    df.loc[cross_up_bear, 'Signal'] = -1

    # ── Apply Filters & Mechanics ───────────────────────────────────
    sig = df["Signal"].values.copy().astype(float)
    if risk_params.get("use_sma_bias", True):
        sma_vals = df["SMA200"].values
        sig = np.where((sig == 1) & (df["Close"].values < sma_vals), 0, sig)
        sig = np.where((sig == -1) & (df["Close"].values > sma_vals), 0, sig)

    df["Signal_Filtered"] = sig.astype(int)

    # ── Standard Position Tracker ────────────────────────────
    df["Position"] = df["Signal_Filtered"].replace(0, np.nan).ffill().fillna(0)
    pos_change = df["Position"].diff().abs()
    df["Trade_Occurred"] = pos_change > 0
    df["Transaction_Costs"] = np.where(pos_change >= 2, 2 * cost_per_trade, np.where(pos_change > 0, cost_per_trade, 0.0))
    df["Strategy_Returns"] = (df["Market_Returns"] * df["Position"].shift(1)) - df["Transaction_Costs"]
    df["Equity_Curve"] = (1 + df["Strategy_Returns"]).cumprod()

    # ── ATR Backtester SL/TP ────────────────────────────────
    df = apply_sl_tp_exits(
        df, 
        sl_atr_mult=risk_params.get("sl_atr_mult", 1.5), 
        tp_atr_mult=risk_params.get("tp_atr_mult", 3.0), 
        trail_atr_mult=risk_params.get("trail_atr_mult", 2.0), 
        use_trailing=risk_params.get("use_trailing_stop", True), 
        cost_per_trade=cost_per_trade
    )

    # ── Metrics Export ──────────────────────────────────────
    annual_factor = detect_annual_factor(df.index)
    sl_returns = df["Trade_PnL"].replace(0, np.nan).dropna()
    perf = calculate_metrics(sl_returns, annual_factor)

    metrics.update({
        "train_size": train_window,
        "test_size": len(df) - train_window,
        "sharpe": perf["sharpe"],
        "max_dd": perf["max_drawdown"],
        "win_rate": perf["win_rate"],
        "profit_factor": perf["profit_factor"],
        "num_trades": perf["num_trades"],
        "annual_factor": annual_factor,
        "active_pred_comp_cutoff": pred_comp_cutoff,
        "active_pred_bear_cutoff": pred_bear_cutoff,
    })

    return df, metrics

# ============================================================
# 5.  STREAMLIT UI
# ============================================================

st.set_page_config(page_title="Quant Trading Framework v7.6", layout="wide", page_icon="📈")
st.title("📈 Quant Trading Framework v7.6 — Non-Linear Price Forecaster")

with st.sidebar:
    st.header("⚙️ Framework Settings")
    mode = st.radio("Operating Mode", ["Backtest", "Live Execution (MT5)"])

    if mode == "Backtest":
        data_source = st.selectbox("Data Source", ["Yahoo Finance", "MetaTrader 5"])
        symbol = st.text_input("Symbol / Ticker", value="GC=F").upper()
    else:
        st.warning("⚠️ LIVE MODE – real orders will be sent.")
        data_source = "MetaTrader 5"
        symbol = st.text_input("MT5 Symbol", value="XAUUSD").upper()

    lookback = st.slider("Lookback (Days)", 100, 3000, 730)

    st.markdown("---")
    st.header("📐 Strategy Engine")
    st.info("Using **Ensemble Consensus** (XGBoost + Gaussian HMM + Markov) — **Symmetric Bull/Bear**")
    params = {}

    st.subheader("Walk-Forward Model")
    params["train_window"]  = st.slider("Train Window (Bars)", 30, 365, st.session_state.opt_train_window)
    st.session_state.opt_train_window = params["train_window"]
    params["retrain_every"] = st.slider("Retrain Every (Bars)", 5, 60, st.session_state.opt_retrain_every)
    st.session_state.opt_retrain_every = params["retrain_every"]
    params["n_steps"]       = st.slider("Prediction Horizon (N-Steps)", 1, 10, st.session_state.opt_n_steps)
    st.session_state.opt_n_steps = params["n_steps"]
    
    st.subheader("Forecasting Engine")
    params["use_arima_entry"] = st.checkbox("Enable Early-Entry Signal Forecasting", value=st.session_state.use_arima_entry)
    st.session_state.use_arima_entry = params["use_arima_entry"]
    
    model_options = ["Direct OLS", "Random Forest", "Support Vector Regression (SVR)"]
    default_idx = model_options.index(st.session_state.price_model_type) if st.session_state.price_model_type in model_options else 1
    params["price_model_type"] = st.selectbox("Price Projection Model", model_options, index=default_idx, help="Model mapping forecast changes to future price.")
    st.session_state.price_model_type = params["price_model_type"]
    
    st.subheader("Forecast Target Thresholds")
    params["smooth_span"] = st.slider("EMA Smoothing Span", 2, 20, st.session_state.opt_smooth_span)
    st.session_state.opt_smooth_span = params["smooth_span"]
    
    pred_comp_int = st.slider("Predicted Bull Cutoff (%)", 50, 99, int(st.session_state.opt_pred_comp_cutoff * 100))
    st.session_state.opt_pred_comp_cutoff = pred_comp_int / 100.0
    params["pred_comp_cutoff"] = pred_comp_int / 100.0

    pred_bear_int = st.slider("Predicted Bear Cutoff (%)", 50, 99, int(st.session_state.opt_pred_bear_cutoff * 100))
    st.session_state.opt_pred_bear_cutoff = pred_bear_int / 100.0
    params["pred_bear_cutoff"] = pred_bear_int / 100.0
    
    pred_adx_val = st.slider("Predicted ADX Rising Cutoff", 10.0, 50.0, float(st.session_state.opt_pred_adx_cutoff))
    st.session_state.opt_pred_adx_cutoff = pred_adx_val
    params["pred_adx_cutoff"] = pred_adx_val

    st.subheader("Transaction Cost")
    params["cost_per_trade"] = st.number_input("Cost Per Side (%)", value=0.07, step=0.01) / 100.0

    st.markdown("---")
    st.header("🛡️ Risk Management")
    risk_params = {}
    risk_params["account_balance"] = st.number_input("Account Balance ($)", value=10_000, step=500)
    risk_params["risk_pct"]        = st.number_input("Risk Per Trade (%)", value=1.0, min_value=0.1, step=0.1) / 100.0
    risk_params["atr_period"]      = st.slider("ATR Period", 5, 50, 14)
    risk_params["sl_atr_mult"]     = st.slider("Stop-Loss × ATR", 0.5, 4.0, 1.5, 0.1)
    risk_params["tp_atr_mult"]     = st.slider("Take-Profit × ATR", 1.0, 8.0, 3.0, 0.25)
    risk_params["use_trailing_stop"] = st.checkbox("Enable Trailing Stop", value=True)
    if risk_params["use_trailing_stop"]:
        risk_params["trail_atr_mult"] = st.slider("Trailing Stop × ATR", 0.5, 4.0, 2.0, 0.1)
    else:
        risk_params["trail_atr_mult"] = 2.0
    risk_params["point_value"] = st.number_input("Contract Point Value ($)", value=1.0, step=1.0)

    st.markdown("---")
    st.header("🔎 Additional Filters")
    risk_params["use_sma_bias"]    = st.checkbox("200-SMA Trend Bias", value=True)

    # ────────────────────────────────────────────────────────────
    # OPTUNA OPTIMIZER ENGINE (BIDIRECTIONAL)
    # ────────────────────────────────────────────────────────────
    st.markdown("---")
    st.header("🔬 Optuna Hyper-Optimizer")
    
    if not OPTUNA_AVAILABLE:
        st.warning("Please run `pip install optuna` to enable the advanced Optuna Optimizer.")
    else:
        opt_seed = st.number_input("Random Seed", value=42, step=1)
        
        if "opt_message" in st.session_state:
            msg = st.session_state.opt_message
            if msg["type"] == "success": st.success(msg["text"])
            else: st.warning(msg["text"])
            del st.session_state.opt_message

        if st.button("Optimize Cutoffs (Optuna OOS)", type="primary", help="Finds the optimal Consensus and ADX cutoffs using Optuna."):
            with st.spinner("Running Optuna study on validation set…"):
                random.seed(int(opt_seed))
                opt_df = fetch_market_data(symbol, data_source, lookback)
                
                if not opt_df.empty:
                    n = len(opt_df)
                    train_end = int(n * 0.60)
                    val_end   = int(n * 0.80)
                    
                    val_df  = opt_df.iloc[:val_end]
                    test_df = opt_df.iloc[train_end:] 
                    af = detect_annual_factor(opt_df.index)

                    def objective(trial):
                        tp = {
                            "train_window": trial.suggest_int("train_window", 60, 180, step=30),
                            "retrain_every": trial.suggest_int("retrain_every", 10, 30, step=10),
                            "n_steps": trial.suggest_int("n_steps", 1, 5),
                            "smooth_span": trial.suggest_int("smooth_span", 2, 8),
                            "pred_comp_cutoff": trial.suggest_float("pred_comp_cutoff", 0.50, 0.85, step=0.05),
                            "pred_bear_cutoff": trial.suggest_float("pred_bear_cutoff", 0.50, 0.85, step=0.05),
                            "pred_adx_cutoff": trial.suggest_float("pred_adx_cutoff", 15.0, 35.0, step=2.5),
                            "use_arima_entry": params["use_arima_entry"],
                            "price_model_type": params["price_model_type"],
                            "cost_per_trade": params["cost_per_trade"]
                        }
                        
                        try:
                            res, temp_met = run_ensemble_strategy(val_df, tp, risk_params)
                            vslice = res.iloc[train_end:]
                            if len(vslice) < 10: raise optuna.TrialPruned()

                            wk = vslice.resample("W").agg({"Trade_PnL": lambda x: (1+x).prod()-1, "Trade_Occurred": "sum"})
                            if wk["Trade_Occurred"].max() > 50: raise optuna.TrialPruned()

                            vm = calculate_metrics(vslice["Trade_PnL"].replace(0, np.nan).dropna(), af)
                            score = vm["sharpe"] - (wk["Trade_Occurred"].mean() * 0.05)
                            return score
                        except Exception:
                            raise optuna.TrialPruned()

                    study = optuna.create_study(direction="maximize")
                    study.optimize(objective, n_trials=30)

                    if len(study.trials) > 0 and study.best_trial:
                        best_p = study.best_trial.params
                        best_p["use_arima_entry"] = params["use_arima_entry"]
                        best_p["price_model_type"] = params["price_model_type"]
                        best_p["cost_per_trade"] = params["cost_per_trade"]
                        best_vs = study.best_value
                        
                        rt, _ = run_ensemble_strategy(test_df, best_p, risk_params)
                        ts = rt.iloc[val_end - train_end:]
                        min_test_bars = best_p["train_window"] + 20

                        if len(ts) < min_test_bars:
                            st.session_state.opt_message = {
                                "type": "warning",
                                "text": f"Dataset too short for OOS test. Need ≥ {min_test_bars} bars in test split."
                            }
                        else:
                            tm_val = calculate_metrics(ts["Trade_PnL"].replace(0, np.nan).dropna(), af)
                            oos = tm_val["sharpe"]
                            
                            st.session_state.opt_train_window = best_p["train_window"]
                            st.session_state.opt_retrain_every = best_p["retrain_every"]
                            st.session_state.opt_n_steps = best_p["n_steps"]
                            st.session_state.opt_smooth_span = best_p["smooth_span"]
                            st.session_state.opt_pred_comp_cutoff = best_p["pred_comp_cutoff"]
                            st.session_state.opt_pred_bear_cutoff = best_p["pred_bear_cutoff"]
                            st.session_state.opt_pred_adx_cutoff = best_p["pred_adx_cutoff"]

                            msg_type = "success" if oos > 0 else "warning"
                            st.session_state.opt_message = {
                                "type": msg_type,
                                "text": f"Val Sharpe: {best_vs:.2f}  |  OOS Test Sharpe: {oos:.2f}. " + 
                                        ("Model generalizes well." if oos > 0 else "Negative OOS – Proceed with caution.")
                            }
                        
                        try:
                            st.rerun()
                        except AttributeError:
                            st.experimental_rerun()
                    else:
                        st.error("No parameter set passed constraints during optimization.")


if symbol:
    with st.spinner(f"Fetching {symbol} from {data_source}…"):
        df = fetch_market_data(symbol, data_source, lookback)

    if df.empty:
        st.error("No data returned. Check symbol and data source.")
        st.stop()

    with st.spinner("Running Strategy Engine…"):
        results_df, model_metrics = run_ensemble_strategy(df, params, risk_params)

    af = model_metrics["annual_factor"] if model_metrics else 252
    latest_atr   = results_df["ATR"].iloc[-1] if "ATR" in results_df.columns else 0.0
    latest_price = results_df["Close"].iloc[-1]
    
    pos_units = calc_position_size(
        risk_params["account_balance"], risk_params["risk_pct"],
        latest_atr * risk_params["sl_atr_mult"], latest_price,
        point_value=risk_params.get("point_value", 1.0)
    )

    tab_titles = ["📈 Backtest & Signals", "🔮 Forecast Engine Performance", "🤝 Model Consensus", "🔬 Indicators & Filters"]
    tabs = st.tabs(tab_titles)

    with tabs[0]:
        eq_last    = results_df["Equity_Curve_SL"].iloc[-1]
        total_ret  = (eq_last - 1) * 100
        bh_ret     = ((results_df["Close"].iloc[-1] - results_df["Close"].iloc[0]) / results_df["Close"].iloc[0]) * 100

        c = st.columns(7)
        c[0].metric("Strategy Return",  f"{total_ret:+.2f}%")
        c[1].metric("Buy & Hold",        f"{bh_ret:+.2f}%")
        c[2].metric("Raw Excess vs B&H", f"{total_ret - bh_ret:+.2f}%")
        c[3].metric("Sharpe",            f"{model_metrics['sharpe']:.2f}" if model_metrics else "N/A")
        c[4].metric("Max Drawdown",      f"{model_metrics['max_dd']*100:.2f}%" if model_metrics else "N/A")
        c[5].metric("Win Rate",          f"{model_metrics.get('win_rate',0)*100:.1f}%")
        c[6].metric("Suggested Size",    f"{pos_units:,.1f} units")

        atr_upper = results_df["Close"] + risk_params["sl_atr_mult"] * results_df["ATR"]
        atr_lower = results_df["Close"] - risk_params["sl_atr_mult"] * results_df["ATR"]

        fig = make_subplots(
            rows=4, cols=1, shared_xaxes=True,
            vertical_spacing=0.04,
            row_heights=[0.45, 0.18, 0.18, 0.19],
            subplot_titles=(
                f"{symbol} Price  |  200-SMA  |  Forecast-Weighted Target Price",
                "Consensus Predictive Probability Zones (Longs & Shorts)",
                "Predicted ADX Engine",
                "Equity Curve (ATR SL/TP exits)",
            ),
        )

        fig.add_trace(go.Candlestick(
            x=results_df.index, open=results_df["Open"], high=results_df["High"],
            low=results_df["Low"], close=results_df["Close"], name="Price", 
            increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        ), row=1, col=1)

        fig.add_trace(go.Scatter(x=results_df.index, y=results_df["SMA200"], name="200-SMA", line=dict(color="gold", width=1.5, dash="dot")), row=1, col=1)
        
        if params["use_arima_entry"]:
            future_dates = pd.Series(results_df.index).shift(-params['n_steps']).values
            valid_mask = ~pd.isna(future_dates)
            fig.add_trace(go.Scatter(x=future_dates[valid_mask], y=results_df["ARIMA_Pred_Price"].values[valid_mask], name=f"Multivariate Pred (t+{params['n_steps']})", line=dict(color="yellow", width=1.5, dash="dot")), row=1, col=1)

        buys  = results_df[results_df["Signal_Filtered"] == 1]
        sells = results_df[results_df["Signal_Filtered"] == -1]
        fig.add_trace(go.Scatter(x=buys.index, y=buys["Low"] * 0.997, mode="markers", marker=dict(symbol="triangle-up", color="lime", size=9), name="Filtered Buy"), row=1, col=1)
        fig.add_trace(go.Scatter(x=sells.index, y=sells["High"] * 1.003, mode="markers", marker=dict(symbol="triangle-down", color="orangered", size=9), name="Filtered Sell"), row=1, col=1)

        # ── Decoupled Visual Highlights (Green = Bull, Red = Bear) ──
        p_c_cutoff = params["pred_comp_cutoff"]
        p_bear_cutoff = params["pred_bear_cutoff"]
        p_a_cutoff = params["pred_adx_cutoff"]
        
        # Bullish Regions
        in_bull = False
        bull_start = None
        for date, is_hl in zip(results_df.index, results_df["Highlight_Zone_Bull"]):
            if is_hl and not in_bull:
                in_bull = True; bull_start = date
            elif not is_hl and in_bull:
                in_bull = False
                fig.add_vrect(x0=bull_start, x1=date, fillcolor="green", opacity=0.15, layer="below", line_width=0, row=1, col=1)
                fig.add_vrect(x0=bull_start, x1=date, fillcolor="green", opacity=0.15, layer="below", line_width=0, row=2, col=1)
                fig.add_vrect(x0=bull_start, x1=date, fillcolor="green", opacity=0.15, layer="below", line_width=0, row=3, col=1)
        if in_bull: 
            fig.add_vrect(x0=bull_start, x1=results_df.index[-1], fillcolor="green", opacity=0.15, layer="below", line_width=0, row=1, col=1)
            fig.add_vrect(x0=bull_start, x1=results_df.index[-1], fillcolor="green", opacity=0.15, layer="below", line_width=0, row=2, col=1)
            fig.add_vrect(x0=bull_start, x1=results_df.index[-1], fillcolor="green", opacity=0.15, layer="below", line_width=0, row=3, col=1)

        # Bearish Regions
        in_bear = False
        bear_start = None
        for date, is_hl in zip(results_df.index, results_df["Highlight_Zone_Bear"]):
            if is_hl and not in_bear:
                in_bear = True; bear_start = date
            elif not is_hl and in_bear:
                in_bear = False
                fig.add_vrect(x0=bear_start, x1=date, fillcolor="red", opacity=0.15, layer="below", line_width=0, row=1, col=1)
                fig.add_vrect(x0=bear_start, x1=date, fillcolor="red", opacity=0.15, layer="below", line_width=0, row=2, col=1)
                fig.add_vrect(x0=bear_start, x1=date, fillcolor="red", opacity=0.15, layer="below", line_width=0, row=3, col=1)
        if in_bear: 
            fig.add_vrect(x0=bear_start, x1=results_df.index[-1], fillcolor="red", opacity=0.15, layer="below", line_width=0, row=1, col=1)
            fig.add_vrect(x0=bear_start, x1=results_df.index[-1], fillcolor="red", opacity=0.15, layer="below", line_width=0, row=2, col=1)
            fig.add_vrect(x0=bear_start, x1=results_df.index[-1], fillcolor="red", opacity=0.15, layer="below", line_width=0, row=3, col=1)

        # Consensus Subplot - Showing both Bullish and Bearish Composite Signals
        fig.add_hline(y=p_c_cutoff, line_dash="dot", line_color="lime", opacity=0.5, row=2, col=1)
        fig.add_hline(y=p_bear_cutoff, line_dash="dot", line_color="tomato", opacity=0.5, row=2, col=1)
        
        fig.add_trace(go.Scatter(x=results_df.index, y=results_df["Prob_Consensus_Bull"], name="Composite Bull", line=dict(color="lime", width=2)), row=2, col=1)
        fig.add_trace(go.Scatter(x=results_df.index, y=results_df["Prob_Consensus_Bear"], name="Composite Bear", line=dict(color="tomato", width=2)), row=2, col=1)
        
        if params["use_arima_entry"]:
            fig.add_trace(go.Scatter(x=results_df.index, y=results_df["ARIMA_Pred_Comp_Bull"], name=f"Logit Forecast (t+{params['n_steps']})", line=dict(color="lime", width=1.5, dash="dot")), row=2, col=1)
            fig.add_trace(go.Scatter(x=results_df.index, y=results_df["ARIMA_Pred_Comp_Bear"], name=f"Logit Forecast (t+{params['n_steps']})", line=dict(color="tomato", width=1.5, dash="dot")), row=2, col=1)

        # ADX Subplot
        fig.add_hline(y=p_a_cutoff, line_dash="dash", line_color="white", opacity=0.4, row=3, col=1)
        fig.add_trace(go.Scatter(x=results_df.index, y=results_df["ADX"], name="ADX", line=dict(color="orange", width=1.5)), row=3, col=1)
        if params["use_arima_entry"]:
            fig.add_trace(go.Scatter(x=results_df.index, y=results_df["ARIMA_Pred_ADX"], name=f"Logit Forecast (t+{params['n_steps']})", line=dict(color="cyan", width=1.5, dash="dot")), row=3, col=1)

        # Equity
        fig.add_trace(go.Scatter(x=results_df.index, y=results_df["Equity_Curve_SL"], name="Strategy Equity", line=dict(color="mediumpurple", width=2), fill="tozeroy", fillcolor="rgba(147,112,219,0.12)"), row=4, col=1)
        fig.add_trace(go.Scatter(x=results_df.index, y=results_df["Close"] / results_df["Close"].iloc[0], name="Buy & Hold", line=dict(color="steelblue", width=1, dash="dot")), row=4, col=1)

        fig.update_layout(height=950, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=20, r=20, t=50, b=20), legend=dict(orientation="h", y=-0.04))
        st.plotly_chart(fig, use_container_width=True)

    with tabs[1]:
        st.subheader("Multivariate Price Forecast & Performance")
        st.write("Predicting probabilities and prices requires transformations to make the underlying series mathematically stationary.")
        
        st.markdown("""
        **The Engine Pipeline:**
        1. **Bounded Data (Probabilities, ADX) via Logit Transformation:** $Y = \\ln(\\frac{p}{1-p})$ stretches bounded data to $\\pm\\infty$. Followed by Fast OLS AR(1) projection and Sigmoid Inverse mapping.
        2. **Unbounded Data (Price) via Multivariate Forecast Mapping:** By mapping the expected *changes* in our accurate Bull/Bear/ADX forecasts to historical log returns using the selected multivariate algorithm, we project a high-fidelity price trajectory based on shifting market momentum instead of raw autoregression.
        """)
        
        st.divider()
        
        st.markdown("### Forecast Engine Performance")
        st.write("This panel tracks how often the forecast models correctly predicted the **Direction** (Up/Down) of the target variables over the requested lookahead horizon.")
        
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown("#### Model Architecture")
            st.write(f"- **Horizon:** t+{params['n_steps']} steps")
            st.write(f"- **AR(1) Predictors:** Window=30 lags")
            st.write(f"- **Price Predictors:** $\\Delta$ Bull, $\\Delta$ Bear, $\\Delta$ ADX")
            
        with c2:
            st.markdown("#### Bull Consensus")
            st.write("*Fast OLS AR(1) via Logit*")
            if "arima_comp_bull_acc" in model_metrics:
                st.metric(f"t+{params['n_steps']} Accuracy", f"{model_metrics['arima_comp_bull_acc'] * 100:.1f}%")
            if "adf_comp_diff" in model_metrics:
                st.write(f"ADF (Diff): {model_metrics['adf_comp_diff']:.4f}")
                
        with c3:
            st.markdown("#### Bear Consensus")
            st.write("*Fast OLS AR(1) via Logit*")
            if "arima_comp_bear_acc" in model_metrics:
                st.metric(f"t+{params['n_steps']} Accuracy", f"{model_metrics['arima_comp_bear_acc'] * 100:.1f}%")
            if "adf_comp_diff" in model_metrics:
                st.write(f"ADF (Diff): {model_metrics['adf_comp_diff']:.4f}")

        with c4:
            st.markdown("#### ADX Indicator")
            st.write("*Fast OLS AR(1) via Logit*")
            if "arima_adx_acc" in model_metrics:
                st.metric(f"t+{params['n_steps']} Accuracy", f"{model_metrics['arima_adx_acc'] * 100:.1f}%")
            if "adf_adx_diff" in model_metrics:
                st.write(f"ADF (Diff): {model_metrics['adf_adx_diff']:.4f}")

        with c5:
            st.markdown("#### Raw Price")
            st.write(f"*{params['price_model_type']}*")
            if "arima_price_acc" in model_metrics:
                st.metric(f"t+{params['n_steps']} Accuracy", f"{model_metrics['arima_price_acc'] * 100:.1f}%")
            if "adf_price_diff" in model_metrics:
                st.write(f"ADF (Log Returns): {model_metrics['adf_price_diff']:.4f}")

    with tabs[2]:
        st.subheader("Model Consensus & Synchronization")
        st.write("Visualizing the independent smoothed probability waves merging into separate composite crossover signals for Bulls and Bears.")
        
        c_osc1, c_osc2 = st.columns(2)
        with c_osc1:
            st.markdown("#### Bullish Oscillator Convergence")
            fig_ens_bull = go.Figure()
            if XGB_AVAILABLE: fig_ens_bull.add_trace(go.Scatter(x=results_df.index, y=results_df["Smooth_XGB_Bull"], name="XGBoost", line=dict(color="orange", width=1, dash="dot"), opacity=0.7))
            if HMM_AVAILABLE: fig_ens_bull.add_trace(go.Scatter(x=results_df.index, y=results_df["Smooth_HMM_Bull"], name="Gaussian HMM", line=dict(color="cyan", width=1, dash="dot"), opacity=0.7))
            fig_ens_bull.add_trace(go.Scatter(x=results_df.index, y=results_df["Smooth_Markov_Bull"], name="Markov Model", line=dict(color="hotpink", width=1, dash="dot"), opacity=0.7))
            fig_ens_bull.add_trace(go.Scatter(x=results_df.index, y=results_df["Prob_Consensus_Bull"], name="Composite Bull", line=dict(color="lime", width=3)))
            fig_ens_bull.add_hline(y=p_c_cutoff, line_dash="dash", line_color="lime", opacity=0.8)
            fig_ens_bull.update_layout(height=350, template="plotly_dark", margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig_ens_bull, use_container_width=True)
            
        with c_osc2:
            st.markdown("#### Bearish Oscillator Convergence")
            fig_ens_bear = go.Figure()
            if XGB_AVAILABLE: fig_ens_bear.add_trace(go.Scatter(x=results_df.index, y=results_df["Smooth_XGB_Bear"], name="XGBoost", line=dict(color="orange", width=1, dash="dot"), opacity=0.7))
            if HMM_AVAILABLE: fig_ens_bear.add_trace(go.Scatter(x=results_df.index, y=results_df["Smooth_HMM_Bear"], name="Gaussian HMM", line=dict(color="cyan", width=1, dash="dot"), opacity=0.7))
            fig_ens_bear.add_trace(go.Scatter(x=results_df.index, y=results_df["Smooth_Markov_Bear"], name="Markov Model", line=dict(color="hotpink", width=1, dash="dot"), opacity=0.7))
            fig_ens_bear.add_trace(go.Scatter(x=results_df.index, y=results_df["Prob_Consensus_Bear"], name="Composite Bear", line=dict(color="tomato", width=3)))
            fig_ens_bear.add_hline(y=p_bear_cutoff, line_dash="dash", line_color="tomato", opacity=0.8)
            fig_ens_bear.update_layout(height=350, template="plotly_dark", margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig_ens_bear, use_container_width=True)
        
        ca, cb = st.columns(2)
        with ca:
            st.markdown("### Execution Details")
            st.write(f"- **ARIMA Signal Filter:** {'✅ Enabled' if params['use_arima_entry'] else '❌ Disabled'}")
            st.write(f"- **Lookahead Horizon:** {params['n_steps']} bars")
            st.write(f"- **Transaction cost:** {params['cost_per_trade']*100:.3f}% per side")
            st.write(f"- **Total completed trades:** {model_metrics.get('num_trades', 0)}")
            pf = model_metrics.get("profit_factor", 0)
            st.write(f"- **Profit factor:** {pf:.2f}" if pf and not np.isnan(pf) else "- **Profit factor:** N/A")

    with tabs[3]:
        st.subheader("Dynamic Position Sizing")
        st.info(
            f"**Account Balance:** ${risk_params['account_balance']:,.0f}  |  "
            f"**Risk/Trade:** {risk_params['risk_pct']*100:.2f}%  |  "
            f"**ATR:** {latest_atr:.4f}  |  "
            f"**SL distance:** {latest_atr * risk_params['sl_atr_mult']:.4f}  |  "
            f"**Suggested size:** {pos_units:,.1f} units"
        )
        
        st.subheader("Filter Status on Last Signal Bars")
        sig_rows = results_df[results_df["Signal"] != 0].tail(10).copy()
        if not sig_rows.empty:
            sig_rows["SMA_Pass"] = np.where(sig_rows["Signal"] == 1, sig_rows["Close"] > sig_rows["SMA200"], sig_rows["Close"] < sig_rows["SMA200"])
            
            if params["use_arima_entry"]:
                sig_rows["ADX_Pass"] = sig_rows["ARIMA_Pred_ADX"] >= params["pred_adx_cutoff"]
            else:
                sig_rows["ADX_Pass"] = sig_rows["ADX"] >= params["pred_adx_cutoff"]
                
            sig_rows["Final"] = sig_rows["Signal_Filtered"].map({1: "✅ BUY", -1: "✅ SELL", 0: "❌ FILTERED"})
            
            display_cols = ["Close", "ARIMA_Pred_Price", "SMA200", "ATR", "ADX", "SMA_Pass"]
            if params["use_arima_entry"]: display_cols.append("ARIMA_Pred_ADX")
            display_cols.extend(["ADX_Pass", "Signal", "Final"])
                
            st.dataframe(sig_rows[[c for c in display_cols if c in sig_rows.columns]].style.format({"Close": "{:.4f}", "ARIMA_Pred_Price": "{:.4f}", "SMA200": "{:.4f}", "ATR": "{:.4f}", "ADX": "{:.1f}", "ARIMA_Pred_ADX": "{:.1f}"}))