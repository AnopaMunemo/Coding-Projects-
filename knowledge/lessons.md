# Lessons — Don't Relearn These

A running log of bugs/footguns actually hit in this project, so future sessions fix them in
seconds instead of rediscovering them. Pattern from `Software-Engineer-AI-Agent-Atlas`'s
"learning-from-mistakes". Append; don't rewrite.

| Area | Symptom | Root cause | Fix / rule |
|---|---|---|---|
| Streamlit HTML | Signal cards render as **raw HTML text** | 4-space-indented multi-line `st.markdown(f"""...""")` triggers Markdown's code-block parser; split `</div>` left orphan tags | Build each card as **one non-indented concatenated string** with fully-closed HTML. |
| Ensemble | `aggregate_signal` returns **NaN confidence** | a strategy can emit NaN/inf strength on degenerate data | `EnsembleStrategy._to_trade_signal` guards: `if not math.isfinite(confidence): confidence = 0.0` then clamp [0,1]. Keep this guard. |
| Strategy Lab | `ValueError: truth value of a DataFrame is ambiguous` | `df = a.get(x) or b.get(x)` — `or` on a DataFrame | Use explicit `df = a.get(x); if df is None: df = b.get(x)`. Never `or` DataFrames. |
| Portfolio | SA universes crash / empty portfolio / FI unallocated | `valid_st` + `THEME_UNIVERSES` + `FI_UNIVERSE` only had US names | Keep SA universe names in `valid_st`, SA tickers in `THEME_UNIVERSES`, SA bonds first in `FI_UNIVERSE`. |
| MT5 bridge | EA "file not found" | Python wrote local `mt5_signals/`; MT5 reads `Common\Files\` with `FILE_COMMON` | `signal_export._mt5_common_files()` targets the real path; EA opens with `FILE_COMMON`. |
| Sandbox | `git clone github.com` → 403 | Egress policy blocks general internet; only PyPI/npm/own-repo allowed | Read repos via WebFetch; don't retry/route around the block — report it. |
| Packages | `graphifyy` (double-y) looked like a typosquat | It's the **real** PyPI name for `safishamsi/graphify` (MIT) | Verify a suspicious name before refusing, but caution-first was correct. |
| Strategy purity | — | Desk strategies must stay pure (no I/O) so the engine can run them | New strategies (e.g. `GoldORB`) compute on the DataFrame only; risk/TP/SL live in the engine. |
