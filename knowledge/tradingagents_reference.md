# TradingAgents (Tauric Research) — Reference & Integration Notes

External project: <https://github.com/TauricResearch/TradingAgents> · License **Apache-2.0** · Python **3.12**.

This note distils what the repo is, how it works, and how it could plug into the Atlas
Capital stack. It exists so future sessions don't have to re-research it. **We have NOT
vendored or added it as a dependency** — see the decision at the bottom.

> Sandbox note: this environment's egress policy blocks `git clone github.com` (403). The
> repo was read over the web, not cloned. To actually pull it you must run the clone on a
> machine with open internet (see "How to run it yourself").

---

## What it is (and how it differs from Atlas)

A **multi-agent LLM framework** that role-plays a trading firm. It is *qualitative LLM
reasoning*, the opposite end of the spectrum from Atlas's deterministic quant engine.

| | **Atlas Capital (this repo)** | **TradingAgents** |
|---|---|---|
| Signal source | 31 deterministic strategies + ML ensemble | LLM agents debating |
| Risk authority | Hard-cap `RiskManager` (immutable) | LLM "risk manager" agent (prompted) |
| Cost to run | ~free (local compute) | **Paid LLM API calls every decision** |
| Determinism | Reproducible | Non-deterministic (model sampling + live data) |
| Execution | Async brokers (paper/mt5/investec) | Simulated exchange only |

They are **complementary, not overlapping**. Natural fit: TradingAgents becomes *one more
signal* feeding Atlas's `EnsembleStrategy`, while Atlas's hard-cap `RiskManager` keeps final
sizing authority. Never let the LLM agent override the hard caps.

---

## Architecture (LangGraph)

State flows unidirectionally through a directed graph of agent nodes:

```
Analysts → Researchers (bull vs bear debate) → Trader → Risk team → Portfolio manager → exchange
```

- **Analyst team:** Fundamentals · Sentiment (Reddit/StockTwits) · News (macro) · Technical (MACD/RSI).
- **Researcher team:** Bullish + bearish researchers debate each thesis for `max_debate_rounds`.
- **Trader agent:** composes the reports into action + magnitude + timing.
- **Risk team + Portfolio manager:** approve/reject before the (simulated) trade.

Core object: `tradingagents.graph.trading_graph.TradingAgentsGraph`. Entry call:
`ta.propagate(ticker, date)` → returns `(state, decision)`.

### Memory / reflection
- **Decision log** (always on): appends each run to `~/.tradingagents/memory/trading_memory.md`;
  on a later same-ticker run it computes realised return + alpha vs SPY and injects recent
  lessons into the Portfolio Manager prompt. (This is the same *file-based learning* idea
  Atlas uses with `knowledge/` + the weight store — worth noting the convergence.)
- **Checkpoint resume** (opt-in): per-ticker SQLite at `~/.tradingagents/cache/checkpoints/<TICKER>.db`;
  crashed runs resume from the last node.

---

## Configuration (`default_config.py` defaults)

| Key | Default |
|---|---|
| `llm_provider` | `"openai"` (also: google, anthropic, deepseek, groq, ollama, openai_compatible) |
| `deep_think_llm` | `"gpt-5.5"` (heavy reasoning node) |
| `quick_think_llm` | `"gpt-5.4-mini"` (cheap nodes) |
| `temperature` | `None` (set `0.0` for max reproducibility) |
| `max_debate_rounds` | `1` |
| `max_risk_discuss_rounds` | `1` |
| `max_recur_limit` | `100` |
| `checkpoint_enabled` | `False` |
| `news_article_limit` | `20` |
| `results_dir` / `data_cache_dir` | `~/.tradingagents/logs` · `~/.tradingagents/cache` |

Env overrides use the `TRADINGAGENTS_*` prefix; provider keys are `OPENAI_API_KEY`,
`ANTHROPIC_API_KEY`, etc. **Anthropic is a supported provider** — Atlas could point it at a
Claude model.

---

## How to run it yourself (on an open-internet machine)

```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
conda create -n tradingagents python=3.12 && conda activate tradingagents
pip install .
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY, etc.
tradingagents                        # interactive CLI
```

Minimal Python:
```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

cfg = DEFAULT_CONFIG.copy()
cfg["llm_provider"] = "anthropic"
cfg["temperature"]  = 0.0
ta = TradingAgentsGraph(debug=True, config=cfg)
_, decision = ta.propagate("AAPL", "2026-06-29")
print(decision)   # action + reasoning from the portfolio-manager node
```

⚠️ Every `propagate` call fans out across many agents = **many paid LLM calls**. Budget for it.

---

## Proposed integration with Atlas (if/when we do it)

Wrap it behind the engine's `BaseStrategy` contract, exactly like `EnsembleStrategy`:

1. New `trading_bot/strategy/llm_agents.py` → `LLMAgentStrategy(BaseStrategy)`.
2. `calculate_signals()` calls `TradingAgentsGraph.propagate(symbol, today)` per symbol,
   maps the decision (`BUY/SELL/HOLD`) + a confidence proxy into a `TradeSignal`.
3. Feed that signal as **one weighted input** into the ensemble blend (low default weight
   until validated); the learned `WeightStore` tunes its influence over time.
4. **Atlas `RiskManager` keeps final sizing** — the LLM verdict can never raise the hard caps.
5. Guard rails: timeout + try/except → fall back to `HOLD` so an API hiccup never crashes the
   async loop (same degrade-gracefully pattern as `EnsembleStrategy`).
6. Cache by `(symbol, date)` to avoid re-paying for the same day; respect a per-run cost cap.

**Honest caveat:** non-deterministic and unproven — it must pass the same
`profitability_validator` walk-forward gate as everything else before it's trusted, and the
project's "no profit promised" stance (see `decisions_log.md`) still holds.
