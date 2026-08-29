import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import random

# Import MetaTrader5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# ==========================================
# 0. SESSION STATE INITIALIZATION
# ==========================================
if 'opt_train_window' not in st.session_state: st.session_state.opt_train_window = 180
if 'opt_retrain_every' not in st.session_state: st.session_state.opt_retrain_every = 20
if 'opt_n_steps' not in st.session_state: st.session_state.opt_n_steps = 2
if 'opt_bull_cutoff' not in st.session_state: st.session_state.opt_bull_cutoff = 65
if 'opt_bear_cutoff' not in st.session_state: st.session_state.opt_bear_cutoff = 71

# ==========================================
# 1. DATA AND NEWS LAYER
# ==========================================

def detect_annual_factor(index):
    """Infer annualization factor from the data's median bar interval."""
    if len(index) < 2:
        return 252
    diffs = pd.Series(index).diff().dt.total_seconds().dropna()
    median_seconds = diffs.median()
    if median_seconds <= 86_400:       # daily or finer
        return 252
    elif median_seconds <= 7 * 86_400: # weekly
        return 52
    elif median_seconds <= 31 * 86_400: # monthly
        return 12
    return 252


def fetch_market_data(symbol, source, lookback_days=365):
    """Fetches historical or live market data based on the selected source."""
    if source == "Yahoo Finance":
        end_date = datetime.now()
        start_date = end_date - timedelta(days=lookback_days)
        df = yf.download(symbol, start=start_date, end=end_date, progress=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index.name = 'Date'
        return df

    elif source == "MetaTrader 5":
        if not MT5_AVAILABLE:
            st.error("MetaTrader5 library not found. Please install via 'pip install MetaTrader5'.")
            return pd.DataFrame()

        if not mt5.initialize():
            st.error(f"MT5 initialization failed. Error code: {mt5.last_error()}")
            return pd.DataFrame()

        utc_from = datetime.now()
        rates = mt5.copy_rates_from(symbol, mt5.TIMEFRAME_D1, utc_from, lookback_days)

        if rates is None:
            st.error(f"Failed to pull data from MT5 for {symbol}. Check if symbol exists.")
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('time', inplace=True)
        df.rename(columns={
            'open': 'Open', 'high': 'High', 'low': 'Low',
            'close': 'Close', 'tick_volume': 'Volume'
        }, inplace=True)
        df.index.name = 'Date'
        return df

    return pd.DataFrame()


def fetch_news(symbol):
    """Fetches recent news headlines for the given symbol using yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        news = ticker.news
        formatted_news = []
        for article in news[:5]:
            # yfinance ≥0.2.x nests content; fall back to flat schema for older versions
            content = article.get('content', {})
            title = content.get('title') or article.get('title', 'No Title')
            link = (content.get('canonicalUrl', {}) or {}).get('url') or article.get('link', '#')
            publisher = (content.get('provider', {}) or {}).get('displayName') or article.get('publisher', 'Unknown')

            title_lower = title.lower()
            sentiment = (
                "Bullish" if any(w in title_lower for w in ['surge', 'jump', 'up', 'buy', 'growth', 'beats'])
                else "Bearish" if any(w in title_lower for w in ['drop', 'fall', 'down', 'sell', 'misses', 'lawsuit'])
                else "Neutral"
            )
            formatted_news.append({'title': title, 'link': link, 'publisher': publisher, 'sentiment': sentiment})
        return formatted_news
    except Exception:
        return []

# ==========================================
# 2. STRATEGY ENGINE
# ==========================================

def calculate_metrics(returns_series, annual_factor=252):
    """Calculates backtest performance metrics."""
    returns_series = returns_series.dropna()
    if len(returns_series) == 0:
        return {'sharpe': 0, 'max_drawdown': 0, 'total_return': 0}

    equity = (1 + returns_series).cumprod()
    std = returns_series.std()
    sharpe = (returns_series.mean() / std) * np.sqrt(annual_factor) if std != 0 else 0

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_dd = drawdown.min()

    total_ret = equity.iloc[-1] - 1

    return {'sharpe': sharpe, 'max_drawdown': max_dd, 'total_return': total_ret}


def predict_n_steps(transmat_dict, current_state, n_steps=2):
    """Computes the probability vector n steps ahead using matrix exponentiation."""
    p_from_bear = [transmat_dict.get(-1, {}).get(-1, 0.5), transmat_dict.get(-1, {}).get(1, 0.5)]
    p_from_bull = [transmat_dict.get(1, {}).get(-1, 0.5), transmat_dict.get(1, {}).get(1, 0.5)]

    T_matrix = np.array([p_from_bear, p_from_bull])
    T_n = np.linalg.matrix_power(T_matrix, n_steps)

    state_vec = np.array([1, 0]) if current_state == -1 else np.array([0, 1])
    probs_n_step = state_vec @ T_n

    return {-1: probs_n_step[0], 1: probs_n_step[1]}


def run_strategy(df, strategy_name, params):
    """Applies the selected strategy to the dataframe and returns signals and equity."""
    df = df.copy()
    metrics = None

    if strategy_name == "Simple HA Markov Model":
        train_window = params['train_window']
        retrain_every = params['retrain_every']
        bull_cutoff = params['bull_cutoff']
        bear_cutoff = params['bear_cutoff']
        n_steps = params['n_steps']
        cost_per_trade = params['cost_per_trade']

        # --- Heikin-Ashi construction ---
        ha_df = pd.DataFrame(index=df.index)
        ha_df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4

        ha_open = np.zeros(len(df))
        ha_open[0] = (df['Open'].iloc[0] + df['Close'].iloc[0]) / 2
        ha_close_vals = ha_df['HA_Close'].values
        for i in range(1, len(df)):
            ha_open[i] = (ha_open[i - 1] + ha_close_vals[i - 1]) / 2
        ha_df['HA_Open'] = ha_open

        df['HA_State'] = np.where(ha_df['HA_Close'] > ha_df['HA_Open'], 1, -1)

        # FIX 1 (lookahead bias): shift HA_State by 1 so the signal at bar j
        # is derived from bar j-1's completed candle, not bar j's close.
        df['HA_State_Lag'] = df['HA_State'].shift(1)

        df['Signal'] = 0
        df['Prob_Bull_N_Step'] = np.nan
        df['Prob_Bear_N_Step'] = np.nan

        latest_transmat = {}

        for i in range(train_window, len(df)):
            if (i - train_window) % retrain_every != 0 and i != train_window:
                continue

            # Train on lagged state so training data is also lookahead-free
            window = df['HA_State_Lag'].iloc[i - train_window: i].dropna()
            from_states = window.iloc[:-1].values
            to_states = window.iloc[1:].values

            transmat = {}
            for s in [-1, 1]:
                mask = from_states == s
                if mask.sum() == 0:
                    transmat[s] = {-1: 0.5, 1: 0.5}
                else:
                    total = mask.sum()
                    transmat[s] = {
                        1:  (to_states[mask] == 1).sum() / total,
                        -1: (to_states[mask] == -1).sum() / total,
                    }

            latest_transmat = transmat

            end = min(i + retrain_every, len(df))
            for j in range(i, end):
                # FIX 1: use lagged state — signal is based on the *previous* bar
                current_state = df['HA_State_Lag'].iloc[j]
                if pd.isna(current_state):
                    continue
                current_state = int(current_state)

                n_step_probs = predict_n_steps(transmat, current_state, n_steps)
                bull_prob = n_step_probs.get(1, 0)
                bear_prob = n_step_probs.get(-1, 0)

                df.iloc[j, df.columns.get_loc('Prob_Bull_N_Step')] = bull_prob
                df.iloc[j, df.columns.get_loc('Prob_Bear_N_Step')] = bear_prob

                if bull_prob >= bull_cutoff:
                    df.iloc[j, df.columns.get_loc('Signal')] = 1
                elif bear_prob >= bear_cutoff:
                    df.iloc[j, df.columns.get_loc('Signal')] = -1
                else:
                    df.iloc[j, df.columns.get_loc('Signal')] = 0

        df['Position'] = df['Signal'].replace(0, np.nan).ffill().fillna(0)
        df['Market_Returns'] = df['Close'].pct_change()

        # FIX 3 (transaction costs): a reversal (-1→+1 or vice versa) crosses
        # two sides (close existing + open new). Charge 2x cost for diff == 2.
        position_change = df['Position'].diff().abs()
        df['Trade_Occurred'] = position_change > 0
        df['Transaction_Costs'] = np.where(
            position_change >= 2,
            2 * cost_per_trade,
            np.where(position_change > 0, cost_per_trade, 0.0)
        )

        df['Strategy_Returns'] = (df['Market_Returns'] * df['Position'].shift(1)) - df['Transaction_Costs']
        df['Equity_Curve'] = (1 + df['Strategy_Returns']).cumprod()

        display_transmat = pd.DataFrame(latest_transmat).T
        if not display_transmat.empty:
            display_transmat.index = [f"From {'Bullish' if idx == 1 else 'Bearish'}" for idx in display_transmat.index]
            display_transmat.columns = [f"To {'Bullish' if col == 1 else 'Bearish'}" for col in display_transmat.columns]

        annual_factor = detect_annual_factor(df.index)
        perf_metrics = calculate_metrics(df['Strategy_Returns'].iloc[train_window:], annual_factor)

        metrics = {
            'transmat': display_transmat,
            'train_size': train_window,
            'test_size': len(df) - train_window,
            'sharpe': perf_metrics['sharpe'],
            'max_dd': perf_metrics['max_drawdown'],
            'model_type': f'Walk-Forward Markov ({n_steps}-Step)',
            'annual_factor': annual_factor,
        }

    return df, metrics

# ==========================================
# 3. LIVE EXECUTION LAYER (MT5)
# ==========================================

def execute_live_trade(symbol, signal, volume=0.1):
    if not MT5_AVAILABLE or not mt5.initialize():
        return False, "MT5 not initialized"

    order_type = mt5.ORDER_TYPE_BUY if signal == 1 else mt5.ORDER_TYPE_SELL if signal == -1 else None
    if order_type is None:
        return False, "No actionable signal."

    # FIX 5a: ensure the symbol is visible in Market Watch before trading
    if not mt5.symbol_select(symbol, True):
        return False, f"Could not select symbol '{symbol}' in Market Watch."

    # FIX 5b: check for an existing open position in the same direction to
    # avoid pyramiding on repeated button presses
    positions = mt5.positions_get(symbol=symbol)
    if positions:
        existing_types = {p.type for p in positions}
        # ORDER_TYPE_BUY == 0, ORDER_TYPE_SELL == 1
        if order_type in existing_types:
            direction = "BUY" if signal == 1 else "SELL"
            return False, f"A {direction} position for {symbol} already exists. Close it first."

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False, "Failed to get current tick for symbol."

    price = tick.ask if signal == 1 else tick.bid

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": price,
        "deviation": 20,
        "magic": 100000,
        "comment": "Python Framework Algorithmic Trade",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        return False, f"Order failed. Error code: {result.retcode}"

    return True, f"Successfully executed {'BUY' if signal==1 else 'SELL'} for {symbol} at {price}"

# ==========================================
# 4. STREAMLIT USER INTERFACE
# ==========================================

st.set_page_config(page_title="Algorithmic Trading Framework", layout="wide")

st.title("📈 Quantitative Trading Framework")
st.markdown("Design, backtest, and deploy algorithmic trading strategies.")

# Sidebar Configuration
with st.sidebar:
    st.header("Framework Settings")

    mode = st.radio("Operating Mode", ["Backtest", "Live Execution (MT5)"])

    if mode == "Backtest":
        data_source = st.selectbox("Data Source", ["Yahoo Finance", "MetaTrader 5"])
        symbol = st.text_input("Symbol / Ticker", value="AAPL").upper()
    else:
        st.warning("LIVE TRADING MODE ACTIVE. Signals will trigger real MT5 orders.")
        data_source = "MetaTrader 5"
        symbol = st.text_input("MT5 Symbol (e.g., EURUSD, US500)", value="EURUSD").upper()
        trade_volume = st.number_input("Trade Volume (Lots)", value=0.1, step=0.01)

    lookback = st.slider("Historical Lookback (Days)", min_value=100, max_value=3000, value=730)

    st.markdown("---")
    st.header("Strategy Formulation")
    strategy = st.selectbox("Select Strategy", ["Simple HA Markov Model"])

    params = {}
    if strategy == "Simple HA Markov Model":
        st.subheader("Walk-Forward Engine")
        params['train_window'] = st.slider("Training Window (Bars)", 30, 365, value=st.session_state.opt_train_window)
        st.session_state.opt_train_window = params['train_window']

        params['retrain_every'] = st.slider("Retrain Every (Bars)", 5, 60, value=st.session_state.opt_retrain_every)
        st.session_state.opt_retrain_every = params['retrain_every']

        params['n_steps'] = st.slider("Lookahead Horizon (N-Steps)", 1, 5, value=st.session_state.opt_n_steps)
        st.session_state.opt_n_steps = params['n_steps']

        st.subheader("Asymmetric Cutoffs")
        bull_cutoff_int = st.slider("Bull Signal Probability", 50, 99, value=st.session_state.opt_bull_cutoff)
        st.session_state.opt_bull_cutoff = bull_cutoff_int
        params['bull_cutoff'] = bull_cutoff_int / 100.0

        bear_cutoff_int = st.slider("Bear Signal Probability", 50, 99, value=st.session_state.opt_bear_cutoff)
        st.session_state.opt_bear_cutoff = bear_cutoff_int
        params['bear_cutoff'] = bear_cutoff_int / 100.0

        st.subheader("Friction Modeling")
        params['cost_per_trade'] = st.number_input("Transaction Cost Per Side (%)", value=0.1, step=0.05) / 100.0

        st.markdown("---")
        st.subheader("Strategy Optimization")

        # FIX 7: expose random seed so runs are reproducible
        opt_seed = st.number_input("Random Seed", value=42, step=1, help="Fix seed for reproducible optimization results.")

        # Display optimization messages post-rerun
        if 'opt_message' in st.session_state:
            msg = st.session_state.opt_message
            if msg['type'] == 'success':
                st.success(msg['text'])
            else:
                st.warning(msg['text'])
            del st.session_state.opt_message

        if st.button("Optimize Parameters (Out-of-Sample)", type="primary",
                     help="Splits data 60/20/20 train/val/test. Optimizes on validation, reports on unseen test set."):
            with st.spinner("Running out-of-sample parameter search..."):
                # FIX 7: seed random for reproducibility
                random.seed(int(opt_seed))

                opt_df = fetch_market_data(symbol, data_source, lookback)

                if not opt_df.empty:
                    n = len(opt_df)
                    # FIX 2 (overfitting): proper 60/20/20 train/val/test split.
                    # The optimizer searches on val only; the test slice is never
                    # touched during the search and is used purely for reporting.
                    train_end = int(n * 0.60)
                    val_end   = int(n * 0.80)

                    val_df  = opt_df.iloc[:val_end]   # train+val window for walk-forward
                    test_df = opt_df.iloc[train_end:]  # val+test; metrics reported on test slice

                    best_score = -float('inf')
                    best_p = None
                    best_val_sharpe = 0.0

                    annual_factor = detect_annual_factor(opt_df.index)

                    for _ in range(60):
                        test_p = {
                            'train_window':  random.randint(30, 180),
                            'retrain_every': random.randint(5, 40),
                            'n_steps':       random.randint(1, 4),
                            'bull_cutoff':   random.randint(50, 90) / 100.0,
                            'bear_cutoff':   random.randint(50, 90) / 100.0,
                            'cost_per_trade': params['cost_per_trade'],
                        }

                        try:
                            # Evaluate on val_df (train+val), extract val-only period
                            res_df, temp_metrics = run_strategy(val_df, strategy, test_p)
                            val_slice = res_df.iloc[train_end:]  # true out-of-train period

                            if len(val_slice) < 10:
                                continue

                            weekly = val_slice.resample('W').agg({
                                'Strategy_Returns': lambda x: (1 + x).prod() - 1,
                                'Trade_Occurred': 'sum',
                            })

                            # Hard constraint: no more than 50 trades per week on average
                            if weekly['Trade_Occurred'].max() > 50:
                                continue

                            val_metrics = calculate_metrics(
                                val_slice['Strategy_Returns'], annual_factor
                            )
                            val_sharpe = val_metrics['sharpe']
                            avg_trades = weekly['Trade_Occurred'].mean()

                            # FIX 2 (scoring): Sharpe-based objective with a mild
                            # trade-frequency penalty. No 2000x win-rate term.
                            score = val_sharpe - (avg_trades * 0.05)

                            if score > best_score:
                                best_score = score
                                best_p = test_p
                                best_val_sharpe = val_sharpe

                        except Exception:
                            continue

                    if best_p:
                        # Report out-of-sample (test) performance — data the optimizer
                        # never touched — so the number is honest.
                        res_test, _ = run_strategy(test_df, strategy, best_p)
                        test_slice = res_test.iloc[val_end - train_end:]
                        test_m = calculate_metrics(test_slice['Strategy_Returns'], annual_factor)
                        oos_sharpe = test_m['sharpe']

                        st.session_state.opt_train_window  = best_p['train_window']
                        st.session_state.opt_retrain_every = best_p['retrain_every']
                        st.session_state.opt_n_steps       = best_p['n_steps']
                        st.session_state.opt_bull_cutoff   = int(best_p['bull_cutoff'] * 100)
                        st.session_state.opt_bear_cutoff   = int(best_p['bear_cutoff'] * 100)

                        st.session_state.opt_message = {
                            'type': 'success' if oos_sharpe > 0 else 'warning',
                            'text': (
                                f"Val Sharpe: {best_val_sharpe:.2f} | "
                                f"Out-of-sample (test) Sharpe: {oos_sharpe:.2f}. "
                                + ("Parameters generalise well." if oos_sharpe > 0
                                   else "Test Sharpe is negative — treat with caution.")
                            ),
                        }

                        try:
                            st.rerun()
                        except AttributeError:
                            st.experimental_rerun()
                    else:
                        st.error("Optimizer found no parameter sets meeting the trade-frequency constraint.")

# Main Application Logic
if symbol:
    with st.spinner(f"Fetching data for {symbol} from {data_source}..."):
        df = fetch_market_data(symbol, data_source, lookback)

    if not df.empty:
        results_df, model_metrics = run_strategy(df, strategy, params)
        annual_factor = model_metrics['annual_factor'] if model_metrics else 252

        tab_backtest, tab_ml_metrics = st.tabs(["Strategy Backtest & Live Execution", "Model Training & Metrics"])

        with tab_backtest:
            if mode == "Live Execution (MT5)":
                current_signal = results_df['Signal'].iloc[-1]
                signal_text  = "BUY" if current_signal == 1 else "SELL" if current_signal == -1 else "HOLD"
                signal_color = "green" if current_signal == 1 else "red" if current_signal == -1 else "gray"

                st.markdown(
                    f"### Current Live Signal: <span style='color:{signal_color}'>{signal_text}</span>",
                    unsafe_allow_html=True,
                )
                # FIX 5b: warn user that the signal is from the last completed bar
                last_bar_time = results_df.index[-1]
                st.caption(f"Signal derived from last completed bar: {last_bar_time}. Verify it reflects current market state before executing.")

                if st.button("EXECUTE LIVE TRADE NOW", type="primary"):
                    if current_signal != 0:
                        success, msg = execute_live_trade(symbol, current_signal, trade_volume)
                        if success:
                            st.success(msg)
                        else:
                            st.error(msg)
                    else:
                        st.warning("Current signal is HOLD. No trade executed.")
            else:
                total_return   = (results_df['Equity_Curve'].iloc[-1] - 1) * 100
                buy_hold_return = ((results_df['Close'].iloc[-1] - results_df['Close'].iloc[0]) / results_df['Close'].iloc[0]) * 100

                cols = st.columns(5)
                cols[0].metric("Strategy Return",  f"{total_return:.2f}%")
                cols[1].metric("Buy & Hold Return", f"{buy_hold_return:.2f}%")
                cols[2].metric("Sharpe Ratio",      f"{model_metrics['sharpe']:.2f}" if model_metrics else "N/A")
                cols[3].metric("Max Drawdown",      f"{model_metrics['max_dd']*100:.2f}%" if model_metrics else "N/A")
                cols[4].metric("Total Data Points", len(results_df))

            st.markdown("### Market Data & Strategy Visualization")

            fig = make_subplots(
                rows=3, cols=1, shared_xaxes=True,
                vertical_spacing=0.08,
                row_heights=[0.5, 0.25, 0.25],
                subplot_titles=(
                    f"{symbol} Price Action",
                    f"{params.get('n_steps', 2)}-Step Ahead Probabilities",
                    "Strategy Equity Curve (Net of Fees)",
                ),
            )

            fig.add_trace(go.Candlestick(
                x=results_df.index,
                open=results_df['Open'], high=results_df['High'],
                low=results_df['Low'],  close=results_df['Close'],
                name="Price",
            ), row=1, col=1)

            buy_signals  = results_df[results_df['Signal'] == 1]
            sell_signals = results_df[results_df['Signal'] == -1]
            fig.add_trace(go.Scatter(
                x=buy_signals.index, y=buy_signals['Close'] * 0.98,
                mode='markers', marker=dict(symbol='triangle-up', color='green', size=10),
                name='Buy Signal',
            ), row=1, col=1)
            fig.add_trace(go.Scatter(
                x=sell_signals.index, y=sell_signals['Close'] * 1.02,
                mode='markers', marker=dict(symbol='triangle-down', color='red', size=10),
                name='Sell Signal',
            ), row=1, col=1)

            fig.add_trace(go.Scatter(
                x=results_df.index, y=results_df['Prob_Bull_N_Step'],
                name="Prob Bull Next", line=dict(color='green', width=1),
            ), row=2, col=1)
            fig.add_trace(go.Scatter(
                x=results_df.index, y=results_df['Prob_Bear_N_Step'],
                name="Prob Bear Next", line=dict(color='red', width=1),
            ), row=2, col=1)
            fig.add_hline(y=params.get('bull_cutoff', 0.65), line_dash="dot", line_color="green", opacity=0.5, row=2, col=1)
            fig.add_hline(y=params.get('bear_cutoff', 0.71), line_dash="dot", line_color="red",   opacity=0.5, row=2, col=1)

            fig.add_trace(go.Scatter(
                x=results_df.index, y=results_df['Equity_Curve'],
                name="Strategy Equity", line=dict(color='purple', width=2),
            ), row=3, col=1)

            fig.update_layout(
                height=850,
                xaxis_rangeslider_visible=False,
                template="plotly_dark",
                margin=dict(l=20, r=20, t=40, b=20),
            )
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("### Fundamental News & Sentiment")
            news_data = fetch_news(symbol)
            if news_data:
                for article in news_data:
                    sentiment_color = "green" if article['sentiment'] == "Bullish" else "red" if article['sentiment'] == "Bearish" else "gray"
                    with st.container():
                        col1, col2 = st.columns([4, 1])
                        with col1:
                            st.markdown(f"**[{article['title']}]({article['link']})** - *{article['publisher']}*")
                        with col2:
                            st.markdown(f"<span style='color:{sentiment_color}; font-weight:bold'>{article['sentiment']}</span>", unsafe_allow_html=True)
                        st.divider()
            else:
                st.info("No recent news found for this symbol.")

        with tab_ml_metrics:
            if model_metrics is not None:
                st.markdown(f"## {model_metrics.get('model_type', 'Markov Model')} Insights")
                st.write(
                    f"This model uses **walk-forward optimization**, retraining every "
                    f"{params['retrain_every']} bars using a rolling {params['train_window']}-bar "
                    f"historical window. Signals are derived from the **prior bar's** Heikin-Ashi "
                    f"state to eliminate lookahead bias."
                )
                st.info(
                    f"Sharpe ratio is annualized using a factor of **{annual_factor}** "
                    f"(inferred from data frequency). No risk-free rate is subtracted."
                )

                st.divider()

                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("### Latest State Transition Probabilities")
                    st.write("Probability of transitioning between states in the *most recent* training window.")
                    st.dataframe(model_metrics['transmat'].style.format("{:.2%}"))

                with col2:
                    st.markdown("### Model Details & Friction")
                    st.write(f"- **Lookahead Horizon:** {params['n_steps']} steps (matrix exponentiation)")
                    st.write(f"- **Transaction Costs:** {params['cost_per_trade']*100:.3f}% per side; reversals charged 2×")
                    st.write(f"- **Total Signal Changes:** {int(results_df['Trade_Occurred'].sum())}")
                    st.write(f"- **Annualization Factor:** {annual_factor}")
                    st.info(
                        "The optimizer uses a 60/20/20 train/val/test split. "
                        "Val Sharpe guides the search; Test Sharpe is reported as the honest out-of-sample estimate."
                    )
