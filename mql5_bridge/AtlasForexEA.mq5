//+------------------------------------------------------------------+
//|                                                 AtlasForexEA.mq5  |
//|        Execution bridge for the Atlas Capital Python signal desk  |
//|                                                                  |
//|  Reads atlas_signals.csv (written by signal_export.py), validates |
//|  freshness + the session entry window, then places market orders  |
//|  with the supplied Stop Loss, Take Profit and recovery lot size.  |
//|                                                                  |
//|  THE EA OWNS EXECUTION & RISK. Python only proposes signals.      |
//+------------------------------------------------------------------+
#property copyright "Atlas Capital"
#property version   "1.00"
#property strict

#include <Trade/Trade.mqh>
CTrade trade;

//--- Inputs --------------------------------------------------------
input string  InpSignalFile      = "atlas_signals.csv"; // filename in Common\Files
input int     InpMagicNumber     = 20260601;            // must match Python
input int     InpMaxStaleMinutes = 15;     // ignore signals older than this
input bool    InpRespectSession  = true;   // only enter within entry window
input double  InpMaxLot          = 5.0;    // hard broker-side lot ceiling
input double  InpMinLot          = 0.01;   // broker minimum (skip if below)
input bool    InpDryRun          = false;  // false = live orders on demo/real
input int     InpPollSeconds     = 30;     // how often to re-read the file

//--- State ---------------------------------------------------------
datetime g_lastPoll = 0;

//+------------------------------------------------------------------+
int OnInit()
  {
   trade.SetExpertMagicNumber(InpMagicNumber);
   PrintFormat("AtlasForexEA initialised | file=%s | dryRun=%s | magic=%d",
               InpSignalFile, (string)InpDryRun, InpMagicNumber);
   EventSetTimer(InpPollSeconds);
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason) { EventKillTimer(); }

//+------------------------------------------------------------------+
//| Poll the signal file on a timer                                  |
//+------------------------------------------------------------------+
void OnTimer()
  {
   ProcessSignalFile();
  }

//+------------------------------------------------------------------+
//| Read and act on the CSV signal feed                              |
//+------------------------------------------------------------------+
void ProcessSignalFile()
  {
   // FILE_COMMON lets MT5 read from the shared Common\Files folder —
   // the same location Python writes atlas_signals.csv on Windows.
   if(!FileIsExist(InpSignalFile, FILE_COMMON))
     {
      Print("Signal file not found in Common\\Files: ", InpSignalFile);
      Print("Make sure you clicked 'Export to MT5' in the Atlas Capital app.");
      return;
     }

   int h = FileOpen(InpSignalFile, FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON, ',');
   if(h == INVALID_HANDLE)
     {
      Print("Cannot open signal file, err=", GetLastError());
      return;
     }

   //--- header row: skip the 15 known columns
   string header[15];
   for(int c=0; c<15 && !FileIsLineEnding(h); c++)
      header[c] = FileReadString(h);

   int placed = 0, skipped = 0;
   while(!FileIsEnding(h))
     {
      string symbol   = FileReadString(h);
      if(symbol == "") break;
      string dir      = FileReadString(h);
      double entry    = (double)FileReadString(h);
      double sl       = (double)FileReadString(h);
      double tp       = (double)FileReadString(h);
      double lot      = (double)FileReadString(h);
      double rr       = (double)FileReadString(h);
      int    entFrom  = (int)FileReadString(h);
      int    entTo    = (int)FileReadString(h);
      int    exFrom   = (int)FileReadString(h);
      int    exTo     = (int)FileReadString(h);
      double conf     = (double)FileReadString(h);
      int    recovery = (int)FileReadString(h);
      double deficit  = (double)FileReadString(h);
      string genUtc   = FileReadString(h);

      if(HandleSignal(symbol, dir, entry, sl, tp, lot, entFrom, entTo,
                      conf, recovery, deficit, genUtc))
         placed++;
      else
         skipped++;
     }
   FileClose(h);
   PrintFormat("Signal batch processed: %d placed, %d skipped", placed, skipped);
  }

//+------------------------------------------------------------------+
//| Validate + execute a single signal. Returns true if order sent.  |
//+------------------------------------------------------------------+
bool HandleSignal(string symbol, string dir, double entry, double sl, double tp,
                  double lot, int entFrom, int entTo, double conf,
                  int recovery, double deficit, string genUtc)
  {
   //--- 1. Freshness check
   if(IsStale(genUtc))
     { PrintFormat("[%s] SKIP — signal stale", symbol); return false; }

   //--- 2. Session window check (UTC hour)
   if(InpRespectSession && !InEntryWindow(entFrom, entTo))
     { PrintFormat("[%s] SKIP — outside entry window %02d-%02dh UTC",
                   symbol, entFrom, entTo); return false; }

   //--- 3. Symbol availability
   if(!SymbolSelect(symbol, true))
     { PrintFormat("[%s] SKIP — symbol not in Market Watch", symbol); return false; }

   //--- 4. Lot normalisation against broker constraints
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step   = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   lot = MathMin(lot, MathMin(InpMaxLot, maxLot));
   if(step > 0) lot = MathFloor(lot/step)*step;
   if(lot < MathMax(InpMinLot, minLot))
     {
      PrintFormat("[%s] SKIP — sized lot %.4f below broker minimum %.2f "
                  "(account too small for this instrument)", symbol, lot, minLot);
      return false;
     }

   //--- 5. Avoid stacking duplicate positions per symbol+magic
   if(HasOpenPosition(symbol))
     { PrintFormat("[%s] SKIP — position already open", symbol); return false; }

   //--- 6. Execute
   string tag = StringFormat("Atlas %s conf=%.0f%%%s", dir, conf*100,
                             recovery==1 ? StringFormat(" RECOVERY def=%.2f", deficit) : "");

   if(InpDryRun)
     {
      PrintFormat("[DRY RUN] %s %s lot=%.2f SL=%.5f TP=%.5f | %s",
                  symbol, dir, lot, sl, tp, tag);
      return true;
     }

   bool ok = false;
   if(dir == "LONG")
      ok = trade.Buy(lot, symbol, 0.0, sl, tp, tag);
   else if(dir == "SHORT")
      ok = trade.Sell(lot, symbol, 0.0, sl, tp, tag);

   if(ok)
      PrintFormat("[%s] ORDER SENT %s lot=%.2f SL=%.5f TP=%.5f | %s",
                  symbol, dir, lot, sl, tp, tag);
   else
      PrintFormat("[%s] ORDER FAILED err=%d", symbol, trade.ResultRetcode());
   return ok;
  }

//+------------------------------------------------------------------+
//| Helpers                                                          |
//+------------------------------------------------------------------+
bool IsStale(string genUtc)
  {
   //--- genUtc is ISO-8601 e.g. 2026-06-01T13:45:00+00:00
   //--- crude parse: pull date + time, compare to server time (assumed UTC)
   datetime sigTime = ParseIso(genUtc);
   if(sigTime == 0) return false;            // unparseable → don't block
   long ageSec = (long)(TimeGMT() - sigTime);
   return (ageSec > InpMaxStaleMinutes*60);
  }

datetime ParseIso(string iso)
  {
   //--- expects "YYYY-MM-DDTHH:MM:SS..."
   if(StringLen(iso) < 19) return 0;
   string d = StringSubstr(iso, 0, 10);   // YYYY-MM-DD
   string t = StringSubstr(iso, 11, 8);   // HH:MM:SS
   StringReplace(d, "-", ".");
   return StringToTime(d + " " + t);
  }

bool InEntryWindow(int fromH, int toH)
  {
   MqlDateTime now; TimeGMT(now);
   int h = now.hour;
   if(fromH <= toH) return (h >= fromH && h < toH);
   return (h >= fromH || h < toH);          // window wraps midnight
  }

bool HasOpenPosition(string symbol)
  {
   for(int i=PositionsTotal()-1; i>=0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) == symbol &&
         PositionGetInteger(POSITION_MAGIC) == InpMagicNumber)
         return true;
     }
   return false;
  }
//+------------------------------------------------------------------+
