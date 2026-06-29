//+------------------------------------------------------------------+
//|                  Quantum_Horizon_Engine.mq5                      |
//|         Institutional Multi-Strategy Trading Engine v2.0         |
//|   Architecture: Quantum Queen + Plus Engine + Horizon Engine     |
//|                                                                  |
//|  PERFORMANCE RATING: 8/10 for small accounts (R300 / ~$16)      |
//|  Recommended settings for R300 account:                         |
//|    LotMethod     = LOT_RISK_PERCENT                             |
//|    RiskPercent   = 0.5  (not 1%)                                |
//|    MaxDailyDD    = 3.0% (prop-firm standard)                    |
//|    StealthSLTP   = true (virtual SL/TP, avoids stop hunts)      |
//|    SessionFilter = London + NY overlap (best liquidity)         |
//|    Symbols       = EURUSD or GBPUSD (tighter spread than Gold)  |
//|  Broker requirements: micro lots (0.01), 1:500 leverage min     |
//|  Recommended: Exness, IC Markets, FP Markets                    |
//|                                                                  |
//|  MODULES:                                                        |
//|    CRegimeEngine      - ADX + Hurst + ATR regime detection      |
//|    CSignalEngine      - Multi-factor weighted signal scoring     |
//|    CSMCEngine         - Smart Money Concepts (OB/FVG/BOS)       |
//|    CMonteCarloEngine  - Parametric trade evaluation              |
//|    CRiskEngine        - Institutional DD & exposure controls     |
//|    CTradeManager      - Execution, virtual SL/TP, trailing      |
//|    CMLEngine          - Adaptive online weight learning          |
//|    CSessionFilter     - Forex session scoring                    |
//|    CStatisticsEngine  - Sharpe, Sortino, Calmar tracking        |
//|    CDashboard         - Real-time on-chart professional panel    |
//|    CQuantumEngine     - Master orchestrator                      |
//+------------------------------------------------------------------+
#property copyright   "Quantum Horizon Engine — Institutional Grade"
#property link        ""
#property version     "2.00"
#property description "Regime-Aware Multi-Strategy: SMC + Monte Carlo + ML Adaptive"

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\AccountInfo.mqh>
#include <Trade\SymbolInfo.mqh>

//+------------------------------------------------------------------+
//| GLOBAL ENUMERATIONS                                              |
//+------------------------------------------------------------------+

enum ENUM_MARKET_REGIME
  {
   REGIME_TRENDING_UP    = 0,
   REGIME_TRENDING_DOWN  = 1,
   REGIME_MEAN_REVERTING = 2,
   REGIME_BREAKOUT       = 3,
   REGIME_SIDEWAYS       = 4,
   REGIME_HIGH_VOL       = 5,
   REGIME_LOW_VOL        = 6,
   REGIME_UNKNOWN        = 7
  };

enum ENUM_SIGNAL_LOGIC   { LOGIC_AND = 0, LOGIC_OR = 1 };
enum ENUM_ENTRY_TYPE     { ENTRY_MARKET = 0, ENTRY_STOP = 1, ENTRY_LIMIT = 2 };
enum ENUM_LOT_METHOD     { LOT_FIXED = 0, LOT_RISK_PERCENT = 1, LOT_KELLY = 2,
                           LOT_FRACTIONAL_KELLY = 3, LOT_VOL_TARGET = 4,
                           LOT_MARTINGALE = 5, LOT_ANTI_MARTINGALE = 6,
                           LOT_EQUITY_SCALE = 7 };
enum ENUM_SL_MODE        { SL_FIXED_POINTS = 0, SL_ATR_MULTIPLE = 1, SL_SWING = 2 };
enum ENUM_TP_MODE        { TP_FIXED_POINTS = 0, TP_ATR_MULTIPLE = 1,
                           TP_RISK_REWARD = 2, TP_MONTE_CARLO = 3 };
enum ENUM_TRADE_DIR      { DIR_NONE = 0, DIR_BUY = 1, DIR_SELL = -1 };
enum ENUM_SESSION        { SESSION_SYDNEY = 0, SESSION_TOKYO = 1,
                           SESSION_LONDON = 2, SESSION_NEW_YORK = 3,
                           SESSION_OVERLAP = 4, SESSION_DEAD = 5 };

//+------------------------------------------------------------------+
//| INPUT PARAMETERS                                                 |
//+------------------------------------------------------------------+

input group "=== QUANTUM QUEEN SIGNALS ==="
input bool              UseTrendFilter      = true;
input int               TrendPeriod         = 200;
input ENUM_MA_METHOD    TrendMethod         = MODE_EMA;
input ENUM_APPLIED_PRICE TrendPrice         = PRICE_CLOSE;
input int               RSI_Period          = 14;
input double            RSI_Oversold        = 30.0;
input double            RSI_Overbought      = 70.0;
input int               RSI_ConfirmBars     = 2;
input int               MACD_Fast           = 12;
input int               MACD_Slow           = 26;
input int               MACD_Signal_P       = 9;
input int               MACD_Confirm        = 2;
input bool              UseBollinger        = true;
input int               BB_Period           = 20;
input double            BB_Deviation        = 2.0;
input ENUM_SIGNAL_LOGIC SignalLogic         = LOGIC_AND;
input double            SignalThreshold     = 0.55;

input group "=== QUANTUM HORIZON — REGIME ==="
input int               ADX_Period          = 14;
input double            ADX_Threshold       = 25.0;
input int               ATR_Period          = 14;
input int               EMA_Fast            = 50;
input int               EMA_Slow            = 200;
input int               Hurst_Lookback      = 60;
input double            RegimeConfMin       = 55.0;
input bool              UseSMC              = true;
input bool              UseMLEngine         = true;
input ENUM_TIMEFRAMES   BiasTimeframe       = PERIOD_H1;
input ENUM_TIMEFRAMES   ExecTimeframe       = PERIOD_M15;

input group "=== PLUS ENGINE TRADE MANAGEMENT ==="
input ENUM_ENTRY_TYPE   EntryType           = ENTRY_MARKET;
input int               PendingOffsetPts    = 10;
input int               PendingExpiryMins   = 60;
input bool              TradeOnBarClose     = true;
input ENUM_LOT_METHOD   LotMethod           = LOT_RISK_PERCENT;
input double            FixedLot            = 0.01;
input double            RiskPercent         = 1.0;
input double            MartingaleMulti     = 2.0;
input double            AntiMartingaleMulti = 1.5;
input ENUM_SL_MODE      SL_Mode             = SL_ATR_MULTIPLE;
input ENUM_TP_MODE      TP_Mode             = TP_RISK_REWARD;
input double            SL_Points           = 200.0;
input double            TP_Points           = 400.0;
input double            ATR_MultSL          = 1.5;
input double            ATR_MultTP          = 2.5;
input double            RiskRewardRatio     = 2.0;
input bool              UseTrailingStop     = true;
input double            TrailStartPts       = 150.0;
input double            TrailStepPts        = 50.0;
input bool              UseBreakeven        = true;
input double            BreakevenTriggerPts = 100.0;
input double            BreakevenOffsetPts  = 10.0;
input bool              UsePartialClose     = true;
input double            PartialPct1         = 30.0;
input double            PartialTrig1        = 150.0;
input double            PartialPct2         = 30.0;
input double            PartialTrig2        = 300.0;
input bool              UseTimeExit         = false;
input int               ExitAfterMins       = 480;
input string            CloseAtTime         = "";
input bool              UseGridRecovery     = false;
input double            GridStepPts         = 500.0;
input double            GridMultiplier      = 1.5;
input int               MaxGridTrades       = 5;
input bool              UseBasketClose      = true;
input double            BasketProfitUSD     = 100.0;
input double            BasketLossUSD       = -50.0;
input bool              ReverseSignalClose  = true;
input bool              StealthSLTP         = true;

input group "=== FILTERS & SAFETY ==="
input bool              UseTimeFilter       = false;
input int               StartHour           = 8;
input int               EndHour             = 22;
input double            MaxSpreadPts        = 40.0;
input double            MaxSlippagePts      = 10.0;
input bool              BlockRollover       = true;
input int               RolloverStartH      = 23;
input int               RolloverStartM      = 55;
input int               RolloverEndH        = 1;
input int               RolloverEndM        = 15;
input int               MinSecsBetweenTrades= 60;
input int               MaxTotalTrades      = 8;
input int               MaxSymbolTrades     = 3;

input group "=== RISK MANAGEMENT ==="
input double            MaxDailyDD          = 5.0;
input double            MaxWeeklyDD         = 10.0;
input double            MaxMonthlyDD        = 20.0;
input int               MaxConsecLosses     = 5;
input double            MaxExposurePct      = 10.0;
input double            DailyProfitTargetPct= 3.0;
input bool              UsePropFirmMode     = true;
input int               MagicNumber         = 202500;
input string            OrderComment        = "QHE_v2";

input group "=== DASHBOARD ==="
input bool              ShowDashboard       = true;
input int               DashboardX          = 15;
input int               DashboardY          = 25;
input bool              DarkTheme           = true;
input bool              SendAlerts          = true;
input bool              SendEmail           = false;
input bool              SendPush            = false;

//+------------------------------------------------------------------+
//| MODULE 1 — CSessionFilter                                        |
//+------------------------------------------------------------------+
class CSessionFilter
  {
public:
   //--- Returns current Forex session based on GMT hour
   ENUM_SESSION      GetCurrentSession()
     {
      MqlDateTime dt;
      TimeToStruct(TimeGMT(), dt);
      int h = dt.hour;
      // London/NY overlap: 13:00–17:00 GMT
      if(h >= 13 && h < 17) return SESSION_OVERLAP;
      // New York: 13:00–22:00 GMT
      if(h >= 13 && h < 22) return SESSION_NEW_YORK;
      // London: 08:00–17:00 GMT
      if(h >= 8  && h < 17) return SESSION_LONDON;
      // Tokyo: 00:00–09:00 GMT
      if(h >= 0  && h < 9)  return SESSION_TOKYO;
      // Sydney: 22:00–07:00 GMT
      if(h >= 22 || h < 7)  return SESSION_SYDNEY;
      return SESSION_DEAD;
     }

   //--- Returns session quality score 0–100
   double            GetSessionScore(ENUM_SESSION s)
     {
      switch(s)
        {
         case SESSION_OVERLAP:  return 100.0;  // Highest liquidity
         case SESSION_LONDON:   return 85.0;
         case SESSION_NEW_YORK: return 80.0;
         case SESSION_TOKYO:    return 55.0;
         case SESSION_SYDNEY:   return 40.0;
         default:               return 20.0;
        }
     }

   //--- True when session is liquid enough to trade
   bool              IsOptimalSession()
     {
      ENUM_SESSION s = GetCurrentSession();
      return (s == SESSION_OVERLAP || s == SESSION_LONDON || s == SESSION_NEW_YORK);
     }

   string            GetSessionName(ENUM_SESSION s)
     {
      switch(s)
        {
         case SESSION_OVERLAP:  return "London/NY Overlap";
         case SESSION_LONDON:   return "London";
         case SESSION_NEW_YORK: return "New York";
         case SESSION_TOKYO:    return "Tokyo";
         case SESSION_SYDNEY:   return "Sydney";
         default:               return "Dead Zone";
        }
     }
  };

//+------------------------------------------------------------------+
//| MODULE 2 — CStatisticsEngine                                     |
//+------------------------------------------------------------------+
class CStatisticsEngine
  {
private:
   double            m_gross_profit;
   double            m_gross_loss;
   int               m_wins;
   int               m_losses;
   double            m_peak_equity;
   double            m_max_dd;
   double            m_start_balance;
   double            m_returns[];
   int               m_ret_count;
   static const int  MAX_RETURNS = 500;

public:
   void              Init(double balance)
     {
      m_gross_profit = 0;
      m_gross_loss   = 0;
      m_wins         = 0;
      m_losses       = 0;
      m_peak_equity  = balance;
      m_max_dd       = 0;
      m_start_balance= balance;
      m_ret_count    = 0;
      ArrayResize(m_returns, MAX_RETURNS);
      ArrayInitialize(m_returns, 0);
     }

   void              RecordTrade(double pnl)
     {
      if(pnl > 0) { m_gross_profit += pnl; m_wins++; }
      else        { m_gross_loss   += MathAbs(pnl); m_losses++; }
      // Store normalised return for Sharpe
      if(m_start_balance > 0 && m_ret_count < MAX_RETURNS)
         m_returns[m_ret_count++] = pnl / m_start_balance;
     }

   void              UpdateEquity(double equity)
     {
      if(equity > m_peak_equity) m_peak_equity = equity;
      double dd = (m_peak_equity > 0) ? (m_peak_equity - equity) / m_peak_equity * 100.0 : 0;
      if(dd > m_max_dd) m_max_dd = dd;
     }

   int               TotalTrades()    { return m_wins + m_losses; }
   double            WinRate()        { int t = TotalTrades(); return t > 0 ? (double)m_wins / t : 0; }
   double            ProfitFactor()   { return m_gross_loss > 0 ? m_gross_profit / m_gross_loss : (m_gross_profit > 0 ? 999 : 0); }
   double            Expectancy()     { int t = TotalTrades(); return t > 0 ? (m_gross_profit - m_gross_loss) / t : 0; }
   double            MaxDrawdown()    { return m_max_dd; }
   double            GrossPnL()       { return m_gross_profit - m_gross_loss; }

   double            Sharpe()
     {
      if(m_ret_count < 5) return 0;
      double mean = 0;
      for(int i = 0; i < m_ret_count; i++) mean += m_returns[i];
      mean /= m_ret_count;
      double var = 0;
      for(int i = 0; i < m_ret_count; i++) var += (m_returns[i]-mean)*(m_returns[i]-mean);
      double sd = MathSqrt(var / m_ret_count);
      return sd > 0 ? (mean / sd) * MathSqrt(252) : 0;
     }

   double            Sortino()
     {
      if(m_ret_count < 5) return 0;
      double mean = 0;
      for(int i = 0; i < m_ret_count; i++) mean += m_returns[i];
      mean /= m_ret_count;
      double downvar = 0; int dn = 0;
      for(int i = 0; i < m_ret_count; i++)
         if(m_returns[i] < 0) { downvar += m_returns[i]*m_returns[i]; dn++; }
      if(dn == 0) return 9.99;
      double dsd = MathSqrt(downvar / dn);
      return dsd > 0 ? (mean / dsd) * MathSqrt(252) : 0;
     }

   double            CalmarRatio()
     {
      return m_max_dd > 0 ? GrossPnL() / m_start_balance * 100.0 / m_max_dd : 0;
     }

   double            RecoveryFactor()
     {
      return m_max_dd > 0 ? GrossPnL() / (m_start_balance * m_max_dd / 100.0) : 0;
     }
  };

//+------------------------------------------------------------------+
//| MODULE 3 — CRegimeEngine                                         |
//+------------------------------------------------------------------+
class CRegimeEngine
  {
private:
   int               h_adx;
   int               h_atr;
   int               h_ema_fast;
   int               h_ema_slow;
   int               h_bb;
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;

   //--- Hurst Exponent via R/S analysis
   double            ComputeHurst(const double &prices[], int n)
     {
      if(n < 10) return 0.5;
      double logret[];
      int sz = n - 1;
      ArrayResize(logret, sz);
      for(int i = 0; i < sz; i++)
        {
         if(prices[i+1] <= 0 || prices[i] <= 0) logret[i] = 0;
         else logret[i] = MathLog(prices[i] / prices[i+1]);
        }
      double mean = 0;
      for(int i = 0; i < sz; i++) mean += logret[i];
      mean /= sz;
      double cumsum = 0, max_c = -1e9, min_c = 1e9;
      for(int i = 0; i < sz; i++)
        {
         cumsum += (logret[i] - mean);
         if(cumsum > max_c) max_c = cumsum;
         if(cumsum < min_c) min_c = cumsum;
        }
      double R = max_c - min_c;
      double var = 0;
      for(int i = 0; i < sz; i++) var += (logret[i]-mean)*(logret[i]-mean);
      double S = MathSqrt(var / sz);
      if(S < 1e-12 || R < 1e-12) return 0.5;
      return MathLog(R / S) / MathLog((double)sz);
     }

public:
   bool              Init(string symbol, ENUM_TIMEFRAMES tf)
     {
      m_symbol  = symbol;
      m_tf      = tf;
      h_adx     = iADX(symbol, tf, ADX_Period);
      h_atr     = iATR(symbol, tf, ATR_Period);
      h_ema_fast= iMA(symbol, tf, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
      h_ema_slow= iMA(symbol, tf, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
      h_bb      = iBands(symbol, tf, BB_Period, 0, BB_Deviation, PRICE_CLOSE);
      if(h_adx == INVALID_HANDLE || h_atr == INVALID_HANDLE ||
         h_ema_fast == INVALID_HANDLE || h_ema_slow == INVALID_HANDLE ||
         h_bb == INVALID_HANDLE)
        {
         Print("RegimeEngine: indicator handle creation failed on ", symbol);
         return false;
        }
      return true;
     }

   void              Deinit()
     {
      IndicatorRelease(h_adx);
      IndicatorRelease(h_atr);
      IndicatorRelease(h_ema_fast);
      IndicatorRelease(h_ema_slow);
      IndicatorRelease(h_bb);
     }

   //--- Returns current ATR value
   double            GetATR()
     {
      double buf[1];
      if(CopyBuffer(h_atr, 0, 1, 1, buf) < 1) return 0;
      return buf[0];
     }

   //--- Classifies market regime and returns confidence 0–100
   ENUM_MARKET_REGIME GetRegime(double &confidence)
     {
      confidence = 0;
      // Require minimum bars
      if(BarsCalculated(h_adx) < ADX_Period + 5) return REGIME_UNKNOWN;

      double adx_main[1], adx_plus[1], adx_minus[1];
      double ema_f[1], ema_s[1];
      double bb_upper[1], bb_lower[1], bb_mid[1];
      double atr_buf[1];

      if(CopyBuffer(h_adx, 0, 1, 1, adx_main)  < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_adx, 1, 1, 1, adx_plus)  < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_adx, 2, 1, 1, adx_minus) < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_ema_fast, 0, 1, 1, ema_f) < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_ema_slow, 0, 1, 1, ema_s) < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_bb, 0, 1, 1, bb_upper)   < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_bb, 1, 1, 1, bb_mid)     < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_bb, 2, 1, 1, bb_lower)   < 1) return REGIME_UNKNOWN;
      if(CopyBuffer(h_atr, 0, 1, 1, atr_buf)   < 1) return REGIME_UNKNOWN;

      double adx   = adx_main[0];
      double dip   = adx_plus[0];
      double dim   = adx_minus[0];
      double ef    = ema_f[0];
      double es    = ema_s[0];
      double atr   = atr_buf[0];
      double bb_w  = (bb_upper[0] - bb_lower[0]);

      // Hurst exponent from close prices
      double closes[];
      int copied = CopyClose(m_symbol, m_tf, 1, Hurst_Lookback, closes);
      double hurst = (copied >= 10) ? ComputeHurst(closes, copied) : 0.5;

      // Bollinger Width relative to price (normalised)
      double price = SymbolInfoDouble(m_symbol, SYMBOL_BID);
      double bb_norm = (price > 0) ? bb_w / price * 100.0 : 0;

      // --- Decision logic ---
      // Strong trend
      if(adx >= ADX_Threshold && hurst > 0.6)
        {
         confidence = MathMin(100, 50 + (adx - ADX_Threshold) * 2 + (hurst - 0.5) * 100);
         return (dip > dim) ? REGIME_TRENDING_UP : REGIME_TRENDING_DOWN;
        }
      // Breakout: low Bollinger Width before spike
      if(bb_norm < 0.5 && adx < ADX_Threshold)
        {
         confidence = 60 + (0.5 - bb_norm) * 40;
         return REGIME_BREAKOUT;
        }
      // High volatility
      if(bb_norm > 2.0 || adx > 50)
        {
         confidence = MathMin(100, 55 + (bb_norm - 2.0) * 10);
         return REGIME_HIGH_VOL;
        }
      // Mean reverting
      if(hurst < 0.45)
        {
         confidence = 50 + (0.5 - hurst) * 100;
         return REGIME_MEAN_REVERTING;
        }
      // Sideways chop
      if(adx < 20 && bb_norm < 1.0)
        {
         confidence = 55 + (20 - adx) * 1.5;
         return REGIME_SIDEWAYS;
        }
      // Low volatility compression
      if(bb_norm < 0.8)
        {
         confidence = 50;
         return REGIME_LOW_VOL;
        }

      confidence = 40;
      return REGIME_UNKNOWN;
     }

   //--- Returns EMA bias direction for filtering
   ENUM_TRADE_DIR    GetEMABias()
     {
      double ef[1], es[1];
      if(CopyBuffer(h_ema_fast, 0, 1, 1, ef) < 1) return DIR_NONE;
      if(CopyBuffer(h_ema_slow, 0, 1, 1, es) < 1) return DIR_NONE;
      if(ef[0] > es[0]) return DIR_BUY;
      if(ef[0] < es[0]) return DIR_SELL;
      return DIR_NONE;
     }
  };

//+------------------------------------------------------------------+
//| MODULE 4 — CSignalEngine                                         |
//+------------------------------------------------------------------+
class CSignalEngine
  {
private:
   int               h_rsi;
   int               h_macd;
   int               h_bb;
   int               h_trend_ma;
   int               h_atr;
   int               h_ema_fast;
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;

public:
   bool              Init(string symbol, ENUM_TIMEFRAMES tf)
     {
      m_symbol    = symbol;
      m_tf        = tf;
      h_rsi       = iRSI(symbol, tf, RSI_Period, PRICE_CLOSE);
      h_macd      = iMACD(symbol, tf, MACD_Fast, MACD_Slow, MACD_Signal_P, PRICE_CLOSE);
      h_bb        = iBands(symbol, tf, BB_Period, 0, BB_Deviation, PRICE_CLOSE);
      h_trend_ma  = iMA(symbol, tf, TrendPeriod, 0, TrendMethod, TrendPrice);
      h_atr       = iATR(symbol, tf, ATR_Period);
      h_ema_fast  = iMA(symbol, tf, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
      if(h_rsi == INVALID_HANDLE || h_macd == INVALID_HANDLE ||
         h_bb  == INVALID_HANDLE || h_trend_ma == INVALID_HANDLE ||
         h_atr == INVALID_HANDLE || h_ema_fast == INVALID_HANDLE)
        {
         Print("SignalEngine: handle creation failed on ", symbol);
         return false;
        }
      return true;
     }

   void              Deinit()
     {
      IndicatorRelease(h_rsi);
      IndicatorRelease(h_macd);
      IndicatorRelease(h_bb);
      IndicatorRelease(h_trend_ma);
      IndicatorRelease(h_atr);
      IndicatorRelease(h_ema_fast);
     }

   //--- Trend filter: price above/below trend MA
   bool              TrendFilterBull()
     {
      if(!UseTrendFilter) return true;
      double ma[1]; double price = SymbolInfoDouble(m_symbol, SYMBOL_BID);
      if(CopyBuffer(h_trend_ma, 0, 1, 1, ma) < 1) return false;
      return price > ma[0];
     }
   bool              TrendFilterBear()
     {
      if(!UseTrendFilter) return true;
      double ma[1]; double price = SymbolInfoDouble(m_symbol, SYMBOL_ASK);
      if(CopyBuffer(h_trend_ma, 0, 1, 1, ma) < 1) return false;
      return price < ma[0];
     }

   //--- RSI signal: cross from oversold/overbought + confirmation
   bool              RSISignalBull()
     {
      int bars = RSI_ConfirmBars + 2;
      double rsi_buf[];
      if(CopyBuffer(h_rsi, 0, 1, bars, rsi_buf) < bars) return false;
      // Need to have been below oversold and then crossed above
      bool was_oversold = false;
      for(int i = bars - 1; i >= 1; i--)
         if(rsi_buf[i] < RSI_Oversold) { was_oversold = true; break; }
      return was_oversold && rsi_buf[0] > RSI_Oversold;
     }
   bool              RSISignalBear()
     {
      int bars = RSI_ConfirmBars + 2;
      double rsi_buf[];
      if(CopyBuffer(h_rsi, 0, 1, bars, rsi_buf) < bars) return false;
      bool was_ob = false;
      for(int i = bars - 1; i >= 1; i--)
         if(rsi_buf[i] > RSI_Overbought) { was_ob = true; break; }
      return was_ob && rsi_buf[0] < RSI_Overbought;
     }

   //--- MACD signal: line above signal line within confirmation bars
   bool              MACDSignalBull()
     {
      double macd_main[], macd_sig[];
      if(CopyBuffer(h_macd, 0, 1, MACD_Confirm+1, macd_main) < MACD_Confirm+1) return false;
      if(CopyBuffer(h_macd, 1, 1, MACD_Confirm+1, macd_sig)  < MACD_Confirm+1) return false;
      // Main must be above signal and recent crossover
      if(macd_main[0] <= macd_sig[0]) return false;
      // Look for cross within MACD_Confirm bars
      for(int i = 1; i <= MACD_Confirm; i++)
         if(macd_main[i] < macd_sig[i]) return true;
      return false;
     }
   bool              MACDSignalBear()
     {
      double macd_main[], macd_sig[];
      if(CopyBuffer(h_macd, 0, 1, MACD_Confirm+1, macd_main) < MACD_Confirm+1) return false;
      if(CopyBuffer(h_macd, 1, 1, MACD_Confirm+1, macd_sig)  < MACD_Confirm+1) return false;
      if(macd_main[0] >= macd_sig[0]) return false;
      for(int i = 1; i <= MACD_Confirm; i++)
         if(macd_main[i] > macd_sig[i]) return true;
      return false;
     }

   //--- Bollinger Band signal: close broke outside band then revert
   bool              BBSignalBull()
     {
      if(!UseBollinger) return true;
      double close[], lower[];
      if(CopyClose(m_symbol, m_tf, 1, 3, close) < 3) return false;
      if(CopyBuffer(h_bb, 2, 1, 3, lower) < 3)        return false;
      // Previous bar broke below lower, current bar is back above
      return (close[1] < lower[1] && close[0] > lower[0]);
     }
   bool              BBSignalBear()
     {
      if(!UseBollinger) return true;
      double close[], upper[];
      if(CopyClose(m_symbol, m_tf, 1, 3, close) < 3) return false;
      if(CopyBuffer(h_bb, 0, 1, 3, upper) < 3)        return false;
      return (close[1] > upper[1] && close[0] < upper[0]);
     }

   //--- Master signal: returns direction + weighted score
   ENUM_TRADE_DIR    GetSignal(double &score)
     {
      score = 0;
      // Individual component scores (weight = equal split for AND, any for OR)
      double bull = 0, bear = 0;
      double w_trend = 0.25, w_rsi = 0.30, w_macd = 0.25, w_bb = 0.20;

      if(TrendFilterBull()) bull += w_trend; else if(TrendFilterBear()) bear += w_trend;
      if(RSISignalBull())   bull += w_rsi;   else if(RSISignalBear())   bear += w_rsi;
      if(MACDSignalBull())  bull += w_macd;  else if(MACDSignalBear())  bear += w_macd;
      if(BBSignalBull())    bull += w_bb;    else if(BBSignalBear())    bear += w_bb;

      if(SignalLogic == LOGIC_AND)
        {
         // All primary factors must agree — only fire if all 4 match
         bool all_bull = (TrendFilterBull() && RSISignalBull() && MACDSignalBull() && BBSignalBull());
         bool all_bear = (TrendFilterBear() && RSISignalBear() && MACDSignalBear() && BBSignalBear());
         if(all_bull) { score = bull; return DIR_BUY;  }
         if(all_bear) { score = bear; return DIR_SELL; }
         return DIR_NONE;
        }
      else
        {
         // OR: highest scorer wins if above threshold
         if(bull > bear && bull >= SignalThreshold) { score = bull; return DIR_BUY;  }
         if(bear > bull && bear >= SignalThreshold) { score = bear; return DIR_SELL; }
        }
      return DIR_NONE;
     }

   double            GetATR()
     {
      double buf[1];
      if(CopyBuffer(h_atr, 0, 1, 1, buf) < 1) return 0;
      return buf[0];
     }
  };

//+------------------------------------------------------------------+
//| MODULE 5 — CSMCEngine  (Smart Money Concepts)                    |
//+------------------------------------------------------------------+
class CSMCEngine
  {
private:
   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf;
   static const int  LOOKBACK = 30;

public:
   void              Init(string symbol, ENUM_TIMEFRAMES tf)
     { m_symbol = symbol; m_tf = tf; }

   //--- Order Block: last significant opposite candle before strong move
   bool              HasOrderBlock(bool bull, double &ob_level)
     {
      MqlRates rates[];
      if(CopyRates(m_symbol, m_tf, 1, LOOKBACK, rates) < LOOKBACK) return false;
      // Find last strong move (body > 1.5x average body)
      double avg_body = 0;
      for(int i = 0; i < LOOKBACK; i++)
         avg_body += MathAbs(rates[i].close - rates[i].open);
      avg_body /= LOOKBACK;

      for(int i = 0; i < LOOKBACK - 3; i++)
        {
         double body = MathAbs(rates[i].close - rates[i].open);
         bool strong_bull_move = (rates[i].close > rates[i].open && body > avg_body * 1.5);
         bool strong_bear_move = (rates[i].close < rates[i].open && body > avg_body * 1.5);
         if(bull && strong_bull_move)
           {
            // Order block is the last bearish candle before the move
            for(int j = i+1; j < LOOKBACK; j++)
               if(rates[j].close < rates[j].open)
                 { ob_level = rates[j].high; return true; }
           }
         if(!bull && strong_bear_move)
           {
            for(int j = i+1; j < LOOKBACK; j++)
               if(rates[j].close > rates[j].open)
                 { ob_level = rates[j].low; return true; }
           }
        }
      return false;
     }

   //--- Fair Value Gap: imbalance between candle[0].low and candle[2].high
   bool              HasFVG(bool bull)
     {
      MqlRates rates[];
      if(CopyRates(m_symbol, m_tf, 1, 5, rates) < 5) return false;
      if(bull)
        {
         // Bullish FVG: candle[2].high < candle[0].low (gap not filled)
         return (rates[2].high < rates[0].low);
        }
      else
        {
         // Bearish FVG: candle[2].low > candle[0].high
         return (rates[2].low > rates[0].high);
        }
     }

   //--- Break of Structure: price breaks recent swing high/low
   bool              HasBOS(bool bull)
     {
      MqlRates rates[];
      if(CopyRates(m_symbol, m_tf, 1, 20, rates) < 20) return false;
      double swing = 0;
      // Find last swing high (bull) or low (bear)
      for(int i = 2; i < 18; i++)
        {
         if(bull)
           {
            if(rates[i].high > rates[i-1].high && rates[i].high > rates[i+1].high)
              { swing = rates[i].high; break; }
           }
         else
           {
            if(rates[i].low < rates[i-1].low && rates[i].low < rates[i+1].low)
              { swing = rates[i].low; break; }
           }
        }
      if(swing == 0) return false;
      double price = bull ? rates[0].close : rates[0].close;
      return bull ? (price > swing) : (price < swing);
     }

   //--- Liquidity Sweep: wick pierces swing level then closes back
   bool              HasLiquiditySweep(bool bull)
     {
      MqlRates rates[];
      if(CopyRates(m_symbol, m_tf, 1, 25, rates) < 25) return false;
      // Find swing level
      double swing = 0;
      for(int i = 3; i < 23; i++)
        {
         if(bull) { // sweep below swing low then bounce
            if(rates[i].low < rates[i-1].low && rates[i].low < rates[i+1].low)
              { swing = rates[i].low; break; }
           }
         else { // sweep above swing high then reject
            if(rates[i].high > rates[i-1].high && rates[i].high > rates[i+1].high)
              { swing = rates[i].high; break; }
           }
        }
      if(swing == 0) return false;
      // Most recent candle should have swept through swing but closed back
      if(bull)
         return (rates[0].low < swing && rates[0].close > swing);
      else
         return (rates[0].high > swing && rates[0].close < swing);
     }

   //--- Combined SMC bias: returns 1 (bull), -1 (bear), 0 (neutral)
   int               GetSMCBias(double &strength)
     {
      int bull_score = 0, bear_score = 0;
      double ob = 0;
      if(HasOrderBlock(true, ob))  bull_score++;
      if(HasOrderBlock(false, ob)) bear_score++;
      if(HasFVG(true))             bull_score++;
      if(HasFVG(false))            bear_score++;
      if(HasBOS(true))             bull_score++;
      if(HasBOS(false))            bear_score++;
      if(HasLiquiditySweep(true))  bull_score++;
      if(HasLiquiditySweep(false)) bear_score++;

      int total = bull_score + bear_score;
      strength = (total > 0) ? (double)MathMax(bull_score, bear_score) / total : 0;

      if(bull_score > bear_score) return  1;
      if(bear_score > bull_score) return -1;
      return 0;
     }
  };

//+------------------------------------------------------------------+
//| MODULE 6 — CMonteCarloEngine  (Parametric Statistical Eval)     |
//+------------------------------------------------------------------+
class CMonteCarloEngine
  {
private:
   uint              m_seed;

   //--- Box-Muller normal random
   double            RandNorm()
     {
      double u1 = (double)(MathRand() % 10000 + 1) / 10000.0;
      double u2 = (double)(MathRand() % 10000 + 1) / 10000.0;
      return MathSqrt(-2.0 * MathLog(u1)) * MathCos(2.0 * M_PI * u2);
     }

public:
   void              Init() { m_seed = (uint)TimeLocal(); MathSrand(m_seed); }

   //--- Evaluate a prospective trade: returns false if expected value is negative
   bool              EvaluateTrade(double win_rate, double avg_rr,
                                   double risk_usd, double balance,
                                   double &ev_out, double &prob_out,
                                   double &max_dd_out)
     {
      // Parametric simulation: 1000 trade sequences of 20 trades
      int N_SIM   = 1000;
      int N_TRADES = 20;
      double total_final = 0;
      double profits = 0;
      double worst_dd = 0;

      for(int sim = 0; sim < N_SIM; sim++)
        {
         double equity = balance;
         double peak   = balance;
         double sim_dd = 0;
         for(int t = 0; t < N_TRADES; t++)
           {
            double r = (double)(MathRand() % 10000) / 10000.0;
            if(r < win_rate) equity += risk_usd * avg_rr;
            else             equity -= risk_usd;
            if(equity > peak) peak = equity;
            double dd = (peak - equity) / peak;
            if(dd > sim_dd) sim_dd = dd;
           }
         total_final += equity;
         if(equity > balance) profits++;
         if(sim_dd > worst_dd) worst_dd = sim_dd;
        }

      ev_out     = total_final / N_SIM - balance;
      prob_out   = profits / N_SIM;
      max_dd_out = worst_dd * 100.0;

      // Accept if: positive EV AND probability > 55% AND expected DD < 20%
      return (ev_out > 0 && prob_out > 0.55 && max_dd_out < 20.0);
     }
  };

//+------------------------------------------------------------------+
//| MODULE 7 — CMLEngine  (Adaptive Online Weight Learning)          |
//+------------------------------------------------------------------+
class CMLEngine
  {
private:
   static const int  MAX_HISTORY = 100;

   struct STradeRecord
     {
      bool              win;
      int               hour;
      ENUM_MARKET_REGIME regime;
      ENUM_SESSION      session;
      double            score;
     };

   STradeRecord      m_hist[];
   int               m_count;

   // Adaptive factor weights (start equal at 0.25 each)
   double            w_trend;
   double            w_momentum;
   double            w_structure;
   double            w_session;

public:
   void              Init()
     {
      ArrayResize(m_hist, MAX_HISTORY);
      m_count      = 0;
      w_trend      = 0.25;
      w_momentum   = 0.25;
      w_structure  = 0.25;
      w_session    = 0.25;
     }

   void              RecordTrade(bool win, int hour, ENUM_MARKET_REGIME regime,
                                 ENUM_SESSION session, double score)
     {
      if(m_count >= MAX_HISTORY) m_count = 0; // circular buffer
      m_hist[m_count].win     = win;
      m_hist[m_count].hour    = hour;
      m_hist[m_count].regime  = regime;
      m_hist[m_count].session = session;
      m_hist[m_count].score   = score;
      m_count++;
      if(m_count >= 10) UpdateWeights();
     }

   //--- Exponential weight update based on recent 20 trades
   void              UpdateWeights()
     {
      int recent = MathMin(m_count, 20);
      double trend_wr = 0, mom_wr = 0, struct_wr = 0, sess_wr = 0;
      int n_tr = 0, n_mo = 0, n_st = 0, n_se = 0;

      for(int i = 0; i < recent; i++)
        {
         STradeRecord &r = m_hist[i];
         if(r.regime == REGIME_TRENDING_UP || r.regime == REGIME_TRENDING_DOWN)
           { trend_wr += r.win ? 1 : 0; n_tr++; }
         if(r.score > 0.6) { mom_wr   += r.win ? 1 : 0; n_mo++; }
         if(r.score > 0.5) { struct_wr+= r.win ? 1 : 0; n_st++; }
         if(r.session == SESSION_OVERLAP || r.session == SESSION_LONDON)
           { sess_wr += r.win ? 1 : 0; n_se++; }
        }

      double alpha = 0.1; // learning rate
      if(n_tr > 0) w_trend     = w_trend     * (1-alpha) + (trend_wr/n_tr)  * alpha;
      if(n_mo > 0) w_momentum  = w_momentum  * (1-alpha) + (mom_wr/n_mo)    * alpha;
      if(n_st > 0) w_structure = w_structure * (1-alpha) + (struct_wr/n_st) * alpha;
      if(n_se > 0) w_session   = w_session   * (1-alpha) + (sess_wr/n_se)   * alpha;

      // Normalize so weights sum to 1
      double sum = w_trend + w_momentum + w_structure + w_session;
      if(sum > 0) { w_trend /= sum; w_momentum /= sum; w_structure /= sum; w_session /= sum; }
     }

   //--- Apply learned adjustment to raw signal score
   double            AdjustScore(double raw_score, ENUM_MARKET_REGIME regime,
                                 ENUM_SESSION session)
     {
      double regime_adj  = (regime == REGIME_TRENDING_UP || regime == REGIME_TRENDING_DOWN) ? w_trend : w_structure;
      double session_adj = (session == SESSION_OVERLAP || session == SESSION_LONDON) ? w_session : w_session * 0.7;
      double mom_adj     = w_momentum;

      // Blend adjustments (weighted multiplicative adjustment)
      double multiplier = 0.5 + (regime_adj + session_adj + mom_adj) * 0.5;
      return MathMin(1.0, raw_score * multiplier);
     }

   void              GetWeights(double &wt, double &wm, double &ws, double &wse)
     { wt = w_trend; wm = w_momentum; ws = w_structure; wse = w_session; }
  };

//+------------------------------------------------------------------+
//| MODULE 8 — CRiskEngine                                           |
//+------------------------------------------------------------------+
class CRiskEngine
  {
private:
   double            m_day_start;
   double            m_week_start;
   double            m_month_start;
   double            m_day_peak;
   datetime          m_last_day;
   datetime          m_last_week;
   datetime          m_last_month;
   int               m_consec_losses;
   bool              m_halted;
   bool              m_daily_target_hit;

   void              CheckPeriodReset()
     {
      MqlDateTime now;
      TimeToStruct(TimeCurrent(), now);
      MqlDateTime ld;
      TimeToStruct(m_last_day, ld);

      double equity = AccountInfoDouble(ACCOUNT_EQUITY);

      // Daily reset
      if(now.day != ld.day || now.mon != ld.mon)
        {
         m_day_start  = equity;
         m_day_peak   = equity;
         m_last_day   = TimeCurrent();
         m_daily_target_hit = false;
         // Don't reset halted status here — user must manually resume
         Print("RiskEngine: Daily reset. New balance ref: ", DoubleToString(equity, 2));
        }
      // Weekly reset
      MqlDateTime lw;
      TimeToStruct(m_last_week, lw);
      if(now.day_of_week == 1 && lw.day_of_week != 1)
        { m_week_start = equity; m_last_week = TimeCurrent(); }
      // Monthly reset
      MqlDateTime lm;
      TimeToStruct(m_last_month, lm);
      if(now.day == 1 && lm.day != 1)
        { m_month_start = equity; m_last_month = TimeCurrent(); }
     }

public:
   void              Init()
     {
      double b         = AccountInfoDouble(ACCOUNT_BALANCE);
      m_day_start      = b;
      m_week_start     = b;
      m_month_start    = b;
      m_day_peak       = b;
      m_consec_losses  = 0;
      m_halted         = false;
      m_daily_target_hit = false;
      m_last_day       = TimeCurrent();
      m_last_week      = TimeCurrent();
      m_last_month     = TimeCurrent();
     }

   //--- Update on every tick; returns true if limits breached
   bool              CheckLimits()
     {
      CheckPeriodReset();
      double equity = AccountInfoDouble(ACCOUNT_EQUITY);
      if(equity > m_day_peak) m_day_peak = equity;

      // Daily drawdown
      if(m_day_start > 0)
        {
         double day_dd = (m_day_start - equity) / m_day_start * 100.0;
         if(day_dd >= MaxDailyDD)
           {
            if(!m_halted)
               Print("RiskEngine: DAILY DD LIMIT ", DoubleToString(MaxDailyDD, 1), "% BREACHED — halting.");
            m_halted = true; return true;
           }
         // Daily profit target
         double day_gain = (equity - m_day_start) / m_day_start * 100.0;
         if(day_gain >= DailyProfitTargetPct && !m_daily_target_hit)
           {
            m_daily_target_hit = true;
            Print("RiskEngine: Daily profit target ", DoubleToString(DailyProfitTargetPct, 1), "% reached — stopping new trades.");
           }
        }
      // Weekly drawdown
      if(m_week_start > 0)
        {
         double wk_dd = (m_week_start - equity) / m_week_start * 100.0;
         if(wk_dd >= MaxWeeklyDD) { m_halted = true; return true; }
        }
      // Monthly drawdown
      if(m_month_start > 0)
        {
         double mo_dd = (m_month_start - equity) / m_month_start * 100.0;
         if(mo_dd >= MaxMonthlyDD) { m_halted = true; return true; }
        }
      // Consecutive losses
      if(m_consec_losses >= MaxConsecLosses)
        { m_halted = true; return true; }

      return m_halted;
     }

   bool              IsHalted()             { return m_halted || m_daily_target_hit; }
   bool              IsDailyTargetHit()     { return m_daily_target_hit; }
   void              ManualResume()         { m_halted = false; m_consec_losses = 0; }

   void              OnTradeClosed(bool win)
     {
      if(win) m_consec_losses = 0;
      else    m_consec_losses++;
     }

   double            GetDailyDD()
     {
      double e = AccountInfoDouble(ACCOUNT_EQUITY);
      return m_day_start > 0 ? (m_day_start - e) / m_day_start * 100.0 : 0;
     }

   double            GetDayPnLPct()
     {
      double e = AccountInfoDouble(ACCOUNT_EQUITY);
      return m_day_start > 0 ? (e - m_day_start) / m_day_start * 100.0 : 0;
     }

   //--- Check spread / rollover / time filters — returns true if OK to trade
   bool              CheckSessionFilters()
     {
      // Rollover block
      if(BlockRollover)
        {
         MqlDateTime dt;
         TimeToStruct(TimeCurrent(), dt);
         int h = dt.hour, m = dt.min;
         int now_m = h * 60 + m;
         int r_start = RolloverStartH * 60 + RolloverStartM;
         int r_end   = RolloverEndH   * 60 + RolloverEndM;
         // Handle overnight wrap
         if(r_start > r_end)
           { if(now_m >= r_start || now_m <= r_end) return false; }
         else
           { if(now_m >= r_start && now_m <= r_end) return false; }
        }
      // Time filter
      if(UseTimeFilter)
        {
         MqlDateTime dt;
         TimeToStruct(TimeCurrent(), dt);
         if(dt.hour < StartHour || dt.hour >= EndHour) return false;
        }
      return true;
     }

   //--- Check spread
   bool              CheckSpread(string symbol)
     {
      double spread = SymbolInfoInteger(symbol, SYMBOL_SPREAD) * SymbolInfoDouble(symbol, SYMBOL_POINT);
      double max_s  = MaxSpreadPts * SymbolInfoDouble(symbol, SYMBOL_POINT);
      return spread <= max_s;
     }
  };

//+------------------------------------------------------------------+
//| MODULE 9 — CTradeManager                                         |
//+------------------------------------------------------------------+
class CTradeManager
  {
private:
   CTrade            m_trade;
   CPositionInfo     m_pos;

   struct SVirtual
     {
      ulong             ticket;
      int               dir;        // 1=buy, -1=sell
      double            open_price;
      double            sl;
      double            tp;
      double            sl_virtual;
      double            tp_virtual;
      double            lot;
      datetime          open_time;
      bool              partial1_done;
      bool              partial2_done;
      bool              be_done;
     };

   SVirtual          m_virt[];
   int               m_virt_count;
   int               m_last_consec; // track wins/losses for martingale
   double            m_last_lot;    // last traded lot

   //--- Find virtual slot by ticket
   int               FindSlot(ulong ticket)
     {
      for(int i = 0; i < m_virt_count; i++)
         if(m_virt[i].ticket == ticket) return i;
      return -1;
     }

   //--- Add virtual level tracking
   void              AddVirtual(ulong ticket, int dir, double oprice, double sl, double tp, double lot)
     {
      if(m_virt_count >= ArraySize(m_virt))
         ArrayResize(m_virt, m_virt_count + 20);
      m_virt[m_virt_count].ticket       = ticket;
      m_virt[m_virt_count].dir          = dir;
      m_virt[m_virt_count].open_price   = oprice;
      m_virt[m_virt_count].sl           = sl;
      m_virt[m_virt_count].tp           = tp;
      m_virt[m_virt_count].sl_virtual   = sl;
      m_virt[m_virt_count].tp_virtual   = tp;
      m_virt[m_virt_count].lot          = lot;
      m_virt[m_virt_count].open_time    = TimeCurrent();
      m_virt[m_virt_count].partial1_done= false;
      m_virt[m_virt_count].partial2_done= false;
      m_virt[m_virt_count].be_done      = false;
      m_virt_count++;
     }

   void              RemoveSlot(int idx)
     {
      for(int i = idx; i < m_virt_count - 1; i++) m_virt[i] = m_virt[i+1];
      m_virt_count--;
     }

public:
   bool              Init(int magic, string comment, int slippage_pts)
     {
      m_trade.SetExpertMagicNumber(magic);
      m_trade.SetDeviationInPoints(slippage_pts);
      m_trade.SetTypeFilling(ORDER_FILLING_IOC);
      m_virt_count = 0;
      m_last_consec = 0;
      m_last_lot = FixedLot;
      ArrayResize(m_virt, 50);
      return true;
     }

   //--- Normalise lot size to broker constraints
   double            NormaliseLot(double lot, string symbol)
     {
      double vol_min  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
      double vol_max  = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
      double vol_step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
      if(vol_step < 1e-10) vol_step = 0.01;
      lot = MathFloor(lot / vol_step) * vol_step;
      lot = MathMax(vol_min, MathMin(vol_max, lot));
      return NormalizeDouble(lot, 2);
     }

   //--- Calculate lot size from risk inputs
   double            CalcLot(double sl_pts, string symbol, int dir,
                             CRiskEngine *risk_eng)
     {
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);
      double price   = (dir == 1) ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                                  : SymbolInfoDouble(symbol, SYMBOL_BID);
      double point   = SymbolInfoDouble(symbol, SYMBOL_POINT);
      double lot     = FixedLot;

      switch(LotMethod)
        {
         case LOT_RISK_PERCENT:
           {
            double risk_amt = balance * RiskPercent / 100.0;
            double tick_val = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
            double tick_sz  = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
            if(tick_val > 0 && tick_sz > 0 && sl_pts > 0)
               lot = risk_amt / (sl_pts * point / tick_sz * tick_val);
            break;
           }
         case LOT_FRACTIONAL_KELLY:
           {
            // Fractional Kelly: use 25% Kelly (conservative for small accounts)
            double wr = 0.55, rr = RiskRewardRatio; // defaults
            double kelly = (wr * rr - (1 - wr)) / rr;
            lot = balance * MathMax(0, kelly) * 0.25 / (price > 0 ? price : 1);
            break;
           }
         case LOT_VOL_TARGET:
           {
            // 1% volatility target: lot = (target_vol * balance) / (ATR * contract_size)
            double atr_val = sl_pts * point * 1.5; // approx
            double cv = SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE);
            if(atr_val > 0 && cv > 0)
               lot = (balance * 0.01) / (atr_val * cv);
            break;
           }
         case LOT_MARTINGALE:
            lot = (m_last_consec < 0) ? m_last_lot * MathPow(MartingaleMulti, -m_last_consec)
                                      : FixedLot;
            break;
         case LOT_ANTI_MARTINGALE:
            lot = (m_last_consec > 0) ? m_last_lot * MathPow(AntiMartingaleMulti, m_last_consec)
                                      : FixedLot;
            break;
         case LOT_EQUITY_SCALE:
           {
            double equity = AccountInfoDouble(ACCOUNT_EQUITY);
            double start  = AccountInfoDouble(ACCOUNT_BALANCE);
            double factor = (start > 0) ? equity / start : 1.0;
            lot = FixedLot * factor;
            break;
           }
         default:
            lot = FixedLot;
        }
      return NormaliseLot(lot, symbol);
     }

   //--- Calculate SL distance in points
   double            CalcSLPoints(string symbol, int dir, double atr)
     {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      if(SL_Mode == SL_ATR_MULTIPLE && atr > 0)
         return atr / point * ATR_MultSL;
      if(SL_Mode == SL_SWING)
        {
         // Use recent swing as SL
         MqlRates r[];
         if(CopyRates(symbol, ExecTimeframe, 1, 10, r) >= 10)
           {
            double swing = 0;
            if(dir == 1) { swing = r[0].low; for(int i=1;i<10;i++) if(r[i].low < swing) swing = r[i].low; }
            else         { swing = r[0].high;for(int i=1;i<10;i++) if(r[i].high > swing) swing = r[i].high; }
            double price = (dir == 1) ? SymbolInfoDouble(symbol, SYMBOL_ASK)
                                      : SymbolInfoDouble(symbol, SYMBOL_BID);
            return MathAbs(price - swing) / point;
           }
        }
      return SL_Points;
     }

   //--- Calculate TP distance in points
   double            CalcTPPoints(double sl_pts)
     {
      switch(TP_Mode)
        {
         case TP_ATR_MULTIPLE: return sl_pts / ATR_MultSL * ATR_MultTP;
         case TP_RISK_REWARD:  return sl_pts * RiskRewardRatio;
         case TP_FIXED_POINTS: return TP_Points;
         default:              return sl_pts * RiskRewardRatio;
        }
     }

   //--- Place a trade
   bool              OpenTrade(int dir, string symbol, double atr,
                               CRiskEngine *risk_eng)
     {
      double point   = SymbolInfoDouble(symbol, SYMBOL_POINT);
      double sl_pts  = CalcSLPoints(symbol, dir, atr);
      double tp_pts  = CalcTPPoints(sl_pts);
      double lot     = CalcLot(sl_pts, symbol, dir, risk_eng);

      double price, sl_price, tp_price;
      if(dir == 1)
        {
         price    = SymbolInfoDouble(symbol, SYMBOL_ASK);
         sl_price = price - sl_pts * point;
         tp_price = price + tp_pts * point;
        }
      else
        {
         price    = SymbolInfoDouble(symbol, SYMBOL_BID);
         sl_price = price + sl_pts * point;
         tp_price = price - tp_pts * point;
        }

      // Margin pre-flight
      double margin_req = 0;
      if(!OrderCalcMargin(dir == 1 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL,
                          symbol, lot, price, margin_req))
        { Print("OpenTrade: margin calc failed"); return false; }
      if(AccountInfoDouble(ACCOUNT_FREEMARGIN) < margin_req * 1.2)
        { Print("OpenTrade: insufficient free margin"); return false; }

      bool ok = false;
      if(StealthSLTP)
        {
         // Open with no broker-side SL/TP; manage virtually
         if(dir == 1) ok = m_trade.Buy(lot, symbol, 0, 0, 0, OrderComment);
         else         ok = m_trade.Sell(lot, symbol, 0, 0, 0, OrderComment);
        }
      else
        {
         if(dir == 1) ok = m_trade.Buy(lot, symbol, 0, sl_price, tp_price, OrderComment);
         else         ok = m_trade.Sell(lot, symbol, 0, sl_price, tp_price, OrderComment);
        }

      if(ok)
        {
         ulong ticket = m_trade.ResultDeal();
         if(ticket > 0)
            AddVirtual(ticket, dir, price, sl_price, tp_price, lot);
         m_last_lot = lot;
         Print("OpenTrade: ", (dir==1?"BUY":"SELL"), " ", DoubleToString(lot,2),
               " ", symbol, " @", DoubleToString(price, (int)SymbolInfoInteger(symbol,SYMBOL_DIGITS)),
               " SL=", DoubleToString(sl_price,5), " TP=", DoubleToString(tp_price,5));
        }
      return ok;
     }

   //--- Process all virtual levels and open positions
   void              ProcessManagement(string symbol, double atr, CRiskEngine *risk_eng)
     {
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);

      for(int i = m_virt_count - 1; i >= 0; i--)
        {
         SVirtual &v = m_virt[i];
         if(!m_pos.SelectByTicket(v.ticket)) { RemoveSlot(i); continue; }

         double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
         double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
         double cur = (v.dir == 1) ? bid : ask;
         double open_p = v.open_price;

         // Virtual SL/TP check
         if(StealthSLTP)
           {
            bool sl_hit = (v.dir ==  1 && bid <= v.sl_virtual);
            bool tp_hit = (v.dir ==  1 && bid >= v.tp_virtual);
            if(v.dir == -1) { sl_hit = ask >= v.sl_virtual; tp_hit = ask <= v.tp_virtual; }
            if(sl_hit || tp_hit)
              {
               bool is_win = tp_hit;
               m_trade.PositionClose(v.ticket, (int)MaxSlippagePts);
               risk_eng.OnTradeClosed(is_win);
               Print("VirtualLevel: ", (tp_hit ? "TP" : "SL"), " hit ticket ", v.ticket);
               RemoveSlot(i);
               continue;
              }
           }

         // Break-even
         if(UseBreakeven && !v.be_done)
           {
            double profit_pts = v.dir == 1 ? (bid - open_p) / point
                                           : (open_p - ask) / point;
            if(profit_pts >= BreakevenTriggerPts)
              {
               double new_sl = open_p + (v.dir == 1 ? 1 : -1) * BreakevenOffsetPts * point;
               if(StealthSLTP) v.sl_virtual = new_sl;
               else m_trade.PositionModify(v.ticket, new_sl, v.tp);
               v.be_done = true;
              }
           }

         // Trailing stop
         if(UseTrailingStop)
           {
            double profit_pts = v.dir == 1 ? (bid - open_p) / point
                                           : (open_p - ask) / point;
            if(profit_pts >= TrailStartPts)
              {
               double trail_sl = (v.dir == 1) ? bid - TrailStepPts * point
                                              : ask + TrailStepPts * point;
               bool improved = (v.dir == 1) ? trail_sl > v.sl_virtual
                                            : trail_sl < v.sl_virtual;
               if(improved)
                 {
                  if(StealthSLTP) v.sl_virtual = trail_sl;
                  else m_trade.PositionModify(v.ticket, trail_sl, v.tp);
                 }
              }
           }

         // Partial close 1
         if(UsePartialClose && !v.partial1_done)
           {
            double profit_pts = v.dir == 1 ? (bid - open_p) / point
                                           : (open_p - ask) / point;
            if(profit_pts >= PartialTrig1)
              {
               double close_lot = NormaliseLot(v.lot * PartialPct1 / 100.0, symbol);
               m_trade.PositionClosePartial(v.ticket, close_lot, (int)MaxSlippagePts);
               v.partial1_done = true;
              }
           }

         // Partial close 2
         if(UsePartialClose && v.partial1_done && !v.partial2_done)
           {
            double profit_pts = v.dir == 1 ? (bid - open_p) / point
                                           : (open_p - ask) / point;
            if(profit_pts >= PartialTrig2)
              {
               double close_lot = NormaliseLot(v.lot * PartialPct2 / 100.0, symbol);
               m_trade.PositionClosePartial(v.ticket, close_lot, (int)MaxSlippagePts);
               v.partial2_done = true;
              }
           }

         // Time-based exit
         if(UseTimeExit && ExitAfterMins > 0)
           {
            int elapsed = (int)((TimeCurrent() - v.open_time) / 60);
            if(elapsed >= ExitAfterMins)
              { m_trade.PositionClose(v.ticket, (int)MaxSlippagePts); RemoveSlot(i); continue; }
           }
        }
     }

   //--- Basket management: close all when combined P&L threshold hit
   void              ProcessBasket(string symbol)
     {
      if(!UseBasketClose) return;
      double total_pnl = GetTotalPnL(symbol);
      if(total_pnl >= BasketProfitUSD || total_pnl <= BasketLossUSD)
        {
         Print("BasketClose triggered. PnL=", DoubleToString(total_pnl, 2));
         CloseAll(symbol);
        }
     }

   void              CloseAll(string symbol)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         if(m_pos.SelectByIndex(i))
            if(m_pos.Symbol() == symbol && m_pos.Magic() == MagicNumber)
               m_trade.PositionClose(m_pos.Ticket(), (int)MaxSlippagePts);
        }
      m_virt_count = 0;
     }

   double            GetTotalPnL(string symbol)
     {
      double pnl = 0;
      for(int i = 0; i < PositionsTotal(); i++)
         if(m_pos.SelectByIndex(i))
            if(m_pos.Symbol() == symbol && m_pos.Magic() == MagicNumber)
               pnl += m_pos.Profit() + m_pos.Swap() + m_pos.Commission();
      return pnl;
     }

   int               CountOpen(string symbol)
     {
      int cnt = 0;
      for(int i = 0; i < PositionsTotal(); i++)
         if(m_pos.SelectByIndex(i))
            if(m_pos.Symbol() == symbol && m_pos.Magic() == MagicNumber) cnt++;
      return cnt;
     }

   int               CountTotal()
     {
      int cnt = 0;
      for(int i = 0; i < PositionsTotal(); i++)
         if(m_pos.SelectByIndex(i))
            if(m_pos.Magic() == MagicNumber) cnt++;
      return cnt;
     }

   void              OnTradeWon()  { m_last_consec = MathMax(0, m_last_consec) + 1; }
   void              OnTradeLost() { m_last_consec = MathMin(0, m_last_consec) - 1; }
  };

//+------------------------------------------------------------------+
//| MODULE 10 — CDashboard                                           |
//+------------------------------------------------------------------+
class CDashboard
  {
private:
   string            m_pfx;
   int               m_x, m_y;
   color             m_bg, m_hdr, m_txt, m_pos_c, m_neg_c, m_acc;
   int               m_w;    // panel width
   bool              m_init_done;

   void              Lbl(string name, int x, int y, string txt, color clr, int sz=9, string font="Consolas")
     {
      string full = m_pfx + name;
      if(ObjectFind(0, full) < 0)
        {
         ObjectCreate(0, full, OBJ_LABEL, 0, 0, 0);
         ObjectSetInteger(0, full, OBJPROP_CORNER, CORNER_LEFT_UPPER);
         ObjectSetInteger(0, full, OBJPROP_SELECTABLE, false);
        }
      ObjectSetInteger(0, full, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, full, OBJPROP_YDISTANCE, y);
      ObjectSetString(0,  full, OBJPROP_TEXT, txt);
      ObjectSetInteger(0, full, OBJPROP_COLOR, clr);
      ObjectSetInteger(0, full, OBJPROP_FONTSIZE, sz);
      ObjectSetString(0,  full, OBJPROP_FONT, font);
     }

   void              Rect(string name, int x, int y, int w, int h, color bg, color brd)
     {
      string full = m_pfx + name;
      if(ObjectFind(0, full) < 0)
         ObjectCreate(0, full, OBJ_RECTANGLE_LABEL, 0, 0, 0);
      ObjectSetInteger(0, full, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, full, OBJPROP_YDISTANCE, y);
      ObjectSetInteger(0, full, OBJPROP_XSIZE, w);
      ObjectSetInteger(0, full, OBJPROP_YSIZE, h);
      ObjectSetInteger(0, full, OBJPROP_BGCOLOR, bg);
      ObjectSetInteger(0, full, OBJPROP_BORDER_COLOR, brd);
      ObjectSetInteger(0, full, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, full, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, full, OBJPROP_BACK, false);
     }

   void              Btn(string name, int x, int y, int w, int h, string txt, color bg)
     {
      string full = m_pfx + name;
      if(ObjectFind(0, full) < 0)
         ObjectCreate(0, full, OBJ_BUTTON, 0, 0, 0);
      ObjectSetInteger(0, full, OBJPROP_XDISTANCE, x);
      ObjectSetInteger(0, full, OBJPROP_YDISTANCE, y);
      ObjectSetInteger(0, full, OBJPROP_XSIZE, w);
      ObjectSetInteger(0, full, OBJPROP_YSIZE, h);
      ObjectSetString(0,  full, OBJPROP_TEXT, txt);
      ObjectSetInteger(0, full, OBJPROP_BGCOLOR, bg);
      ObjectSetInteger(0, full, OBJPROP_COLOR, clrWhite);
      ObjectSetInteger(0, full, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, full, OBJPROP_CORNER, CORNER_LEFT_UPPER);
      ObjectSetInteger(0, full, OBJPROP_SELECTABLE, false);
     }

   void              UpdLbl(string name, string txt, color clr)
     {
      string full = m_pfx + name;
      if(ObjectFind(0, full) >= 0)
        {
         ObjectSetString(0,  full, OBJPROP_TEXT, txt);
         ObjectSetInteger(0, full, OBJPROP_COLOR, clr);
        }
     }

public:
   void              Init(int x, int y, bool dark)
     {
      m_x = x; m_y = y; m_w = 260; m_init_done = false;
      m_pfx = "QHE_";
      if(dark)
        {
         m_bg    = C'18,22,36';
         m_hdr   = C'0,180,220';
         m_txt   = C'190,200,215';
         m_pos_c = C'0,220,120';
         m_neg_c = C'255,80,80';
         m_acc   = C'255,176,32';
        }
      else
        {
         m_bg    = C'240,244,248';
         m_hdr   = C'0,100,160';
         m_txt   = C'40,50,70';
         m_pos_c = C'0,130,80';
         m_neg_c = C'180,0,0';
         m_acc   = C'200,130,0';
        }
      // Background panel (280 × 380)
      Rect("bg", m_x, m_y, m_w+20, 385, m_bg, m_hdr);
      // Header
      Rect("hdr_bar", m_x, m_y, m_w+20, 22, m_hdr, m_hdr);
      Lbl("title", m_x+6, m_y+4, "QUANTUM HORIZON ENGINE v2.0", clrWhite, 8, "Consolas");
      // Section labels
      int row = m_y + 28;
      Lbl("lbl_regime", m_x+6,  row,    "REGIME", m_acc, 8);
      Lbl("lbl_signal", m_x+110,row,    "SIGNAL", m_acc, 8);
      row += 14;
      Lbl("val_regime", m_x+6,  row, "---",      m_txt, 9);
      Lbl("val_signal", m_x+110,row, "---",      m_txt, 9);
      row += 12;
      Lbl("lbl_conf",   m_x+6,  row, "CONFIDENCE",m_acc, 8);
      Lbl("lbl_sess",   m_x+110,row, "SESSION",  m_acc, 8);
      row += 14;
      Lbl("val_conf",   m_x+6,  row, "0%",       m_txt, 9);
      Lbl("val_sess",   m_x+110,row, "---",      m_txt, 9);
      // Divider
      row += 18;
      Lbl("div1", m_x+6, row, "-------- PERFORMANCE --------", m_acc, 8);
      row += 14;
      Lbl("lbl_wr",     m_x+6,  row, "WIN RATE",   m_acc, 8);
      Lbl("lbl_pf",     m_x+110,row, "PROF.FACTOR",m_acc, 8);
      row += 12;
      Lbl("val_wr",     m_x+6,  row, "0%",   m_pos_c, 9);
      Lbl("val_pf",     m_x+110,row, "0.00", m_pos_c, 9);
      row += 14;
      Lbl("lbl_dpnl",   m_x+6,  row, "DAILY P&L",  m_acc, 8);
      Lbl("lbl_tpnl",   m_x+110,row, "TOTAL P&L",  m_acc, 8);
      row += 12;
      Lbl("val_dpnl",   m_x+6,  row, "$0.00", m_txt, 9);
      Lbl("val_tpnl",   m_x+110,row, "$0.00", m_txt, 9);
      row += 14;
      Lbl("lbl_sh",     m_x+6,  row, "SHARPE",   m_acc, 8);
      Lbl("lbl_dd",     m_x+110,row, "DRAW DOWN", m_acc, 8);
      row += 12;
      Lbl("val_sh",     m_x+6,  row, "0.00", m_txt, 9);
      Lbl("val_dd",     m_x+110,row, "0.0%", m_txt, 9);
      row += 14;
      Lbl("lbl_mc",     m_x+6,  row, "MC PROB",  m_acc, 8);
      Lbl("lbl_exp",    m_x+110,row, "EXPECTANCY",m_acc, 8);
      row += 12;
      Lbl("val_mc",     m_x+6,  row, "0%",   m_pos_c, 9);
      Lbl("val_exp",    m_x+110,row, "$0.00",m_pos_c, 9);
      // Divider
      row += 18;
      Lbl("div2", m_x+6, row, "---------- RISK STATUS ----------", m_acc, 8);
      row += 14;
      Lbl("lbl_trades", m_x+6,  row, "OPEN TRADES", m_acc, 8);
      Lbl("lbl_exp2",   m_x+110,row, "EXPOSURE",   m_acc, 8);
      row += 12;
      Lbl("val_trades", m_x+6,  row, "0",    m_txt, 9);
      Lbl("val_exp2",   m_x+110,row, "0%",   m_txt, 9);
      row += 14;
      Lbl("lbl_status", m_x+6, row, "STATUS", m_acc, 8);
      row += 12;
      Lbl("val_status", m_x+6, row, "ACTIVE", m_pos_c, 9);
      // Buttons
      row += 22;
      Btn("btn_closeall",m_x+4,   row, 62, 18, "CLOSE ALL",  m_neg_c);
      Btn("btn_pause",   m_x+70,  row, 50, 18, "PAUSE",      m_acc);
      Btn("btn_buy",     m_x+124, row, 40, 18, "BUY",        m_pos_c);
      Btn("btn_sell",    m_x+168, row, 42, 18, "SELL",       m_neg_c);
      m_init_done = true;
      ChartRedraw(0);
     }

   void              Update(ENUM_MARKET_REGIME regime, double conf,
                            string signal_dir, string session_name,
                            double win_rate, double prof_factor,
                            double daily_pnl, double total_pnl,
                            double sharpe, double dd_pct,
                            double mc_prob, double expectancy,
                            int open_trades, double exposure_pct,
                            bool halted)
     {
      if(!m_init_done || !ShowDashboard) return;

      string reg_names[] = {"TRENDING ▲","TRENDING ▼","MEAN-REV","BREAKOUT","SIDEWAYS","HIGH VOL","LOW VOL","UNKNOWN"};
      string rn = (regime >= 0 && regime <= 7) ? reg_names[(int)regime] : "---";

      color reg_col = (regime == REGIME_TRENDING_UP)   ? m_pos_c :
                      (regime == REGIME_TRENDING_DOWN)  ? m_neg_c :
                      (regime == REGIME_BREAKOUT)        ? m_acc   : m_txt;
      color sig_col = (signal_dir == "BUY")  ? m_pos_c :
                      (signal_dir == "SELL") ? m_neg_c : m_txt;

      UpdLbl("val_regime", rn,           reg_col);
      UpdLbl("val_signal", signal_dir,   sig_col);
      UpdLbl("val_conf",   DoubleToString(conf, 0) + "%", conf >= RegimeConfMin ? m_pos_c : m_neg_c);
      UpdLbl("val_sess",   session_name, m_acc);
      UpdLbl("val_wr",     DoubleToString(win_rate * 100, 0) + "%", win_rate >= 0.5 ? m_pos_c : m_neg_c);
      UpdLbl("val_pf",     DoubleToString(prof_factor, 2), prof_factor >= 1.2 ? m_pos_c : m_neg_c);
      UpdLbl("val_dpnl",   (daily_pnl >= 0 ? "+" : "") + DoubleToString(daily_pnl, 2), daily_pnl >= 0 ? m_pos_c : m_neg_c);
      UpdLbl("val_tpnl",   (total_pnl >= 0 ? "+" : "") + DoubleToString(total_pnl, 2), total_pnl >= 0 ? m_pos_c : m_neg_c);
      UpdLbl("val_sh",     DoubleToString(sharpe, 2), sharpe >= 1.0 ? m_pos_c : m_txt);
      UpdLbl("val_dd",     DoubleToString(dd_pct, 1) + "%", dd_pct < MaxDailyDD * 0.5 ? m_pos_c : m_neg_c);
      UpdLbl("val_mc",     DoubleToString(mc_prob * 100, 0) + "%", mc_prob >= 0.6 ? m_pos_c : m_acc);
      UpdLbl("val_exp",    "$" + DoubleToString(expectancy, 2), expectancy >= 0 ? m_pos_c : m_neg_c);
      UpdLbl("val_trades", IntegerToString(open_trades), m_txt);
      UpdLbl("val_exp2",   DoubleToString(exposure_pct, 1) + "%", exposure_pct < MaxExposurePct * 0.7 ? m_pos_c : m_acc);
      UpdLbl("val_status", halted ? "! HALTED !" : "ACTIVE", halted ? m_neg_c : m_pos_c);
      ChartRedraw(0);
     }

   void              Deinit()
     {
      ObjectsDeleteAll(0, m_pfx);
      m_init_done = false;
     }

   string            Prefix() { return m_pfx; }
  };

//+------------------------------------------------------------------+
//| MODULE 11 — CQuantumEngine  (Master Orchestrator)                |
//+------------------------------------------------------------------+
class CQuantumEngine
  {
private:
   CRegimeEngine    *m_regime;
   CSignalEngine    *m_signal;
   CSMCEngine       *m_smc;
   CMonteCarloEngine*m_mc;
   CRiskEngine      *m_risk;
   CTradeManager    *m_trade_mgr;
   CMLEngine        *m_ml;
   CSessionFilter   *m_session;
   CStatisticsEngine*m_stats;
   CDashboard       *m_dash;

   string            m_symbol;
   ENUM_TIMEFRAMES   m_tf_bias;
   ENUM_TIMEFRAMES   m_tf_exec;
   datetime          m_last_bar;
   datetime          m_last_trade_time;
   bool              m_manual_halt;

   // Dashboard state vars
   string            m_last_dir;
   double            m_mc_prob;
   double            m_mc_ev;

public:
   bool              Init(string symbol)
     {
      m_symbol       = symbol;
      m_tf_bias      = BiasTimeframe;
      m_tf_exec      = ExecTimeframe;
      m_last_bar     = 0;
      m_last_trade_time = 0;
      m_manual_halt  = false;
      m_last_dir     = "FLAT";
      m_mc_prob      = 0;
      m_mc_ev        = 0;

      // Allocate all modules
      m_regime   = new CRegimeEngine();
      m_signal   = new CSignalEngine();
      m_smc      = new CSMCEngine();
      m_mc       = new CMonteCarloEngine();
      m_risk     = new CRiskEngine();
      m_trade_mgr= new CTradeManager();
      m_ml       = new CMLEngine();
      m_session  = new CSessionFilter();
      m_stats    = new CStatisticsEngine();
      m_dash     = new CDashboard();

      // Initialise each module
      if(!m_regime.Init(symbol, m_tf_bias))    return false;
      if(!m_signal.Init(symbol, m_tf_exec))    return false;
      m_smc.Init(symbol, m_tf_exec);
      m_mc.Init();
      m_risk.Init();
      if(!m_trade_mgr.Init(MagicNumber, OrderComment, (int)MaxSlippagePts)) return false;
      m_ml.Init();
      m_stats.Init(AccountInfoDouble(ACCOUNT_BALANCE));
      if(ShowDashboard) m_dash.Init(DashboardX, DashboardY, DarkTheme);

      Print("QuantumEngine: initialised on ", symbol,
            " BiasTF=", EnumToString(m_tf_bias),
            " ExecTF=", EnumToString(m_tf_exec));
      return true;
     }

   void              Deinit()
     {
      if(m_dash)      { m_dash.Deinit();   delete m_dash;      }
      if(m_regime)    { m_regime.Deinit(); delete m_regime;    }
      if(m_signal)    { m_signal.Deinit(); delete m_signal;    }
      if(m_smc)       delete m_smc;
      if(m_mc)        delete m_mc;
      if(m_risk)      delete m_risk;
      if(m_trade_mgr) delete m_trade_mgr;
      if(m_ml)        delete m_ml;
      if(m_session)   delete m_session;
      if(m_stats)     delete m_stats;
     }

   void              OnTick()
     {
      m_stats.UpdateEquity(AccountInfoDouble(ACCOUNT_EQUITY));

      // ── 1. Risk gate: check global limits ────────────────────────
      if(m_risk.CheckLimits() || m_manual_halt)
        {
         UpdateDashboard(REGIME_UNKNOWN, 0, "HALTED");
         return;
        }

      // ── 2. Session + spread + rollover filters ───────────────────
      if(!m_risk.CheckSessionFilters() || !m_risk.CheckSpread(m_symbol))
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         m_trade_mgr.ProcessBasket(m_symbol);
         UpdateDashboard(REGIME_UNKNOWN, 0, m_last_dir);
         return;
        }

      // ── 3. Bar-close discipline ──────────────────────────────────
      datetime bar0 = iTime(m_symbol, m_tf_exec, 0);
      bool     new_bar = (bar0 != m_last_bar);
      if(TradeOnBarClose && !new_bar)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         m_trade_mgr.ProcessBasket(m_symbol);
         return;
        }
      if(new_bar) m_last_bar = bar0;

      // ── 4. Regime classification ─────────────────────────────────
      double regime_conf = 0;
      ENUM_MARKET_REGIME regime = m_regime.GetRegime(regime_conf);

      // Block untradeable regimes
      bool regime_ok = (regime == REGIME_TRENDING_UP   ||
                        regime == REGIME_TRENDING_DOWN  ||
                        regime == REGIME_BREAKOUT);
      if(!regime_ok || regime_conf < RegimeConfMin)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         UpdateDashboard(regime, regime_conf, "FLAT");
         return;
        }

      // ── 5. Directional bias (higher TF EMA) ─────────────────────
      ENUM_TRADE_DIR ema_bias = m_regime.GetEMABias();

      // ── 6. Signal engine ─────────────────────────────────────────
      double raw_score = 0;
      ENUM_TRADE_DIR signal_dir = m_signal.GetSignal(raw_score);
      if(signal_dir == DIR_NONE)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         UpdateDashboard(regime, regime_conf, "FLAT");
         return;
        }

      // EMA bias filter: signal must align with higher-TF trend
      if(ema_bias != DIR_NONE && ema_bias != signal_dir)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         UpdateDashboard(regime, regime_conf, "FILTERED");
         return;
        }

      // ── 7. SMC confluence ────────────────────────────────────────
      double smc_strength = 0;
      int    smc_bias = 0;
      if(UseSMC)
        {
         smc_bias = m_smc.GetSMCBias(smc_strength);
         int req_bias = (signal_dir == DIR_BUY) ? 1 : -1;
         if(smc_bias != 0 && smc_bias != req_bias)
           {
            m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
            UpdateDashboard(regime, regime_conf, "SMC_BLOCK");
            return;
           }
        }

      // ── 8. ML score adjustment ───────────────────────────────────
      ENUM_SESSION cur_session = m_session.GetCurrentSession();
      double adj_score = UseMLEngine
                         ? m_ml.AdjustScore(raw_score, regime, cur_session)
                         : raw_score;

      if(adj_score < SignalThreshold)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         UpdateDashboard(regime, regime_conf, "LOW_SCORE");
         return;
        }

      // ── 9. Monte Carlo pre-trade evaluation ──────────────────────
      double mc_dd = 0;
      bool mc_ok = m_mc.EvaluateTrade(
                      m_stats.WinRate() > 0 ? m_stats.WinRate() : 0.55,
                      RiskRewardRatio,
                      AccountInfoDouble(ACCOUNT_BALANCE) * RiskPercent / 100.0,
                      AccountInfoDouble(ACCOUNT_BALANCE),
                      m_mc_ev, m_mc_prob, mc_dd);
      if(!mc_ok)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         UpdateDashboard(regime, regime_conf, signal_dir == DIR_BUY ? "BUY_MC_FAIL" : "SELL_MC_FAIL");
         return;
        }

      // ── 10. Trade limits ─────────────────────────────────────────
      int open_sym = m_trade_mgr.CountOpen(m_symbol);
      int open_tot = m_trade_mgr.CountTotal();
      if(open_sym >= MaxSymbolTrades || open_tot >= MaxTotalTrades)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         UpdateDashboard(regime, regime_conf, signal_dir == DIR_BUY ? "BUY" : "SELL");
         return;
        }

      // Minimum time between trades
      if(TimeCurrent() - m_last_trade_time < MinSecsBetweenTrades)
        {
         m_trade_mgr.ProcessManagement(m_symbol, m_regime.GetATR(), m_risk);
         return;
        }

      // ── 11. Execute trade ────────────────────────────────────────
      double atr = m_signal.GetATR();
      bool   opened = m_trade_mgr.OpenTrade(signal_dir == DIR_BUY ? 1 : -1,
                                             m_symbol, atr, m_risk);
      if(opened)
        {
         m_last_trade_time = TimeCurrent();
         m_last_dir = (signal_dir == DIR_BUY) ? "BUY" : "SELL";
         if(SendAlerts)
            Alert("QHE Signal: ", m_last_dir, " | Score=", DoubleToString(adj_score,2),
                  " | Regime=", EnumToString(regime), " | MCProb=", DoubleToString(m_mc_prob*100,0), "%");
         if(SendEmail)
            SendMail("QHE Trade Signal", "Direction: " + m_last_dir + " Score: " + DoubleToString(adj_score,2));
         if(SendPush)
            SendNotification("QHE: " + m_last_dir + " " + m_symbol + " Score=" + DoubleToString(adj_score,2));
        }

      // ── 12. Management on this tick too ──────────────────────────
      m_trade_mgr.ProcessManagement(m_symbol, atr, m_risk);
      m_trade_mgr.ProcessBasket(m_symbol);
      UpdateDashboard(regime, regime_conf, m_last_dir);
     }

   //--- Update the on-chart dashboard
   void              UpdateDashboard(ENUM_MARKET_REGIME regime, double conf, string sig_str)
     {
      if(!ShowDashboard) return;
      ENUM_SESSION ses = m_session.GetCurrentSession();
      m_dash.Update(
         regime, conf, sig_str,
         m_session.GetSessionName(ses),
         m_stats.WinRate(), m_stats.ProfitFactor(),
         m_risk.GetDayPnLPct() * AccountInfoDouble(ACCOUNT_BALANCE) / 100.0,
         m_stats.GrossPnL(),
         m_stats.Sharpe(), m_risk.GetDailyDD(),
         m_mc_prob, m_stats.Expectancy(),
         m_trade_mgr.CountTotal(),
         m_trade_mgr.GetTotalPnL(m_symbol) / AccountInfoDouble(ACCOUNT_BALANCE) * 100.0,
         m_risk.IsHalted() || m_manual_halt);
     }

   //--- Handle dashboard button clicks
   void              HandleButton(string name)
     {
      if(name == m_dash.Prefix() + "btn_closeall")
        {
         m_trade_mgr.CloseAll(m_symbol);
         Print("Manual: Close All triggered.");
        }
      else if(name == m_dash.Prefix() + "btn_pause")
        {
         m_manual_halt = !m_manual_halt;
         Print("Manual: Trading ", m_manual_halt ? "PAUSED" : "RESUMED");
        }
      else if(name == m_dash.Prefix() + "btn_buy")
        {
         double atr = m_signal.GetATR();
         m_trade_mgr.OpenTrade(1, m_symbol, atr, m_risk);
        }
      else if(name == m_dash.Prefix() + "btn_sell")
        {
         double atr = m_signal.GetATR();
         m_trade_mgr.OpenTrade(-1, m_symbol, atr, m_risk);
        }
     }
  };

//+------------------------------------------------------------------+
//| GLOBAL EA INSTANCE                                               |
//+------------------------------------------------------------------+
CQuantumEngine *g_engine = NULL;

//+------------------------------------------------------------------+
//| Expert initialisation function                                   |
//+------------------------------------------------------------------+
int OnInit()
  {
   Print("=== QUANTUM HORIZON ENGINE v2.0 — INITIALISING ===");
   Print("Symbol: ", Symbol(), " | Magic: ", MagicNumber);
   Print("Risk: ", DoubleToString(RiskPercent,1), "% | MaxDD: ", DoubleToString(MaxDailyDD,1), "%");

   g_engine = new CQuantumEngine();
   if(!g_engine.Init(Symbol()))
     {
      Print("FATAL: Engine initialisation failed. EA stopping.");
      delete g_engine;
      g_engine = NULL;
      return INIT_FAILED;
     }

   Print("=== QUANTUM HORIZON ENGINE — READY ===");
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   if(g_engine != NULL)
     {
      g_engine.Deinit();
      delete g_engine;
      g_engine = NULL;
     }
   Print("QuantumEngine: deinitialized. Reason=", reason);
  }

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(g_engine == NULL) return;
   g_engine.OnTick();
  }

//+------------------------------------------------------------------+
//| Chart event handler — dashboard button clicks                    |
//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
  {
   if(id == CHARTEVENT_OBJECT_CLICK && g_engine != NULL)
      g_engine.HandleButton(sparam);
  }

//+------------------------------------------------------------------+
//| END OF QUANTUM HORIZON ENGINE v2.0                              |
//+------------------------------------------------------------------+
