# Project Decisions Log

A living record of architectural and policy decisions for this project. Append new decisions; do not rewrite history.

---

## Decisions

| Date | Decision | Rationale |
| ---- | -------- | --------- |
| 2026-06-19 | **Two systems merged:** `trading_bot/` (async execution engine, from BotV) + the Streamlit **"Desk"** (analysis brain at repo root). | **Desk = brain** (signals/analysis); **trading_bot = body** (async execution + risk). Clear separation of concerns. |
| 2026-06-19 | **All three brokers supported and switchable:** `paper` / `mt5` / `investec` via the `--broker` flag. | One codebase, swappable execution backends for paper testing, MT5, and Investec. |
| 2026-06-19 | **Learning model defined in two parts:** (a) the bot's ML models + ensemble weights **self-retrain** on walk-forward windows and trade outcomes; (b) this **knowledge base + CLAUDE.md** give every future AI session context. | The **AI model itself does NOT self-train between sessions** — durable knowledge lives in files, not in the model. |
| 2026-06-19 | **Recovery/Martingale sizing kept OFF by default.** | Recovery sizing conflicts with the hard-cap safety model (see `risk_management.md`). Safety first. |
| 2026-06-19 | **Profitability is never promised.** | Must be proven by the user's own backtests **plus a demo period** before any real money. See the honest lesson in `backtesting.md`. |
| — | *Future decisions go here* | *(placeholder — this is a living document)* |
