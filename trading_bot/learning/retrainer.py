"""
retrainer.py — the bot's self-learning loop.

Two mechanisms, both honest and bounded:

1. Outcome feedback (online):  after trades close, `update_from_outcomes()`
   nudges each contributing strategy's blend weight up (if it helped) or down
   (if it hurt), clamped to a safe band. Over time the ensemble leans on the
   strategies that have actually worked on this market.

2. Walk-forward ML refit (batch):  `walk_forward_refit()` re-fits the Desk's
   ML ensemble (framework.run_ensemble_strategy) on a rolling window so model
   parameters track regime changes without look-ahead bias.

This is the "keeps learning" system. Note: it improves the *bot*, not the AI
model — the AI's continuity comes from the knowledge/ base and CLAUDE.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from trading_bot.learning.weight_store import WeightStore

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


class Retrainer:
    def __init__(self, weight_store: Optional[WeightStore] = None,
                 learn_rate: float = 0.05, logger=None) -> None:
        self.weight_store = weight_store or WeightStore()
        self.learn_rate = float(learn_rate)
        self.logger = logger

    # ── 1. Outcome feedback ─────────────────────────────────────────────────
    def update_from_outcomes(self, outcomes: Iterable[Dict[str, Any]]) -> Dict[str, float]:
        """
        outcomes: iterable of {"names": [strategy names that fired], "pnl": float}.
        Winning trades reward their contributing strategies; losers penalise.
        Returns the updated weight map.
        """
        for o in outcomes:
            pnl = float(o.get("pnl", 0.0))
            names = o.get("names", []) or []
            if not names or pnl == 0:
                continue
            direction = 1.0 if pnl > 0 else -1.0
            delta = self.learn_rate * direction
            for name in names:
                self.weight_store.update_weight(name, delta)
        if self.logger:
            self.logger.info("Retrainer updated weights from outcomes")
        return self.weight_store.get_weights()

    def load_outcomes_from_audit(self, audit_dir: str = "audit") -> List[Dict[str, Any]]:
        """Parse audit_*.jsonl for trade-close events carrying pnl + strategies."""
        out: List[Dict[str, Any]] = []
        for f in sorted(Path(audit_dir).glob("audit_*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "pnl" in e and e.get("event_type", "").startswith("ORDER_CLOSE"):
                    out.append({"names": e.get("strategies", []), "pnl": float(e["pnl"])})
        return out

    # ── 2. Walk-forward ML refit ────────────────────────────────────────────
    def walk_forward_refit(self, df, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Re-fit the Desk ML ensemble on a rolling window. Returns a summary dict.
        Safe no-op (with status) if the Desk framework isn't importable.
        """
        try:
            import framework  # repo-root Desk ML module
        except Exception as exc:
            return {"status": "skipped", "reason": f"framework unavailable: {exc}"}

        params = params or {
            "train_window": 252, "retrain_every": 63, "n_steps": 2,
            "cost_per_trade": 0.001, "price_model_type": "Random Forest",
        }
        try:
            result = framework.run_ensemble_strategy(df, params)
            return {"status": "ok", "result_type": type(result).__name__}
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}
