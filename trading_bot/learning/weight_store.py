"""
weight_store.py — atomic, versioned persistence of learned strategy weights.

The ensemble strategy and the retrainer read/write per-strategy blend weights
here. Writes are atomic (temp file → os.replace) so a crash never leaves a
half-written file, and the schema is versioned so future fields can migrate
cleanly. This mirrors the atomic-write pattern already used in
signal_export.py and gold_monte_carlo_v5.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict


SCHEMA_VERSION = 1


class WeightStore:
    """Load/save normalised strategy weights with atomic, versioned JSON."""

    def __init__(self, path: str = "state/ensemble_weights.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict = self._load()

    # ── load / save ────────────────────────────────────────────────────────
    def _load(self) -> Dict:
        if not self.path.exists():
            return {"schema_version": SCHEMA_VERSION, "weights": {}, "updated_utc": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"schema_version": SCHEMA_VERSION, "weights": {}, "updated_utc": None}
        if data.get("schema_version", 0) < SCHEMA_VERSION:
            data = self._migrate(data)
        return data

    def _migrate(self, old: Dict) -> Dict:
        new = {"schema_version": SCHEMA_VERSION, "weights": {}, "updated_utc": None}
        new["weights"] = old.get("weights", {})
        return new

    def save(self) -> None:
        self._data["updated_utc"] = datetime.now(timezone.utc).isoformat()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)   # atomic

    # ── accessors ──────────────────────────────────────────────────────────
    def get_weights(self) -> Dict[str, float]:
        return dict(self._data.get("weights", {}))

    def get_weight(self, name: str, default: float = 1.0) -> float:
        return float(self._data.get("weights", {}).get(name, default))

    def set_weights(self, weights: Dict[str, float]) -> None:
        # Normalise so the mean weight is 1.0 (keeps confidence scaling stable)
        if weights:
            mean = sum(weights.values()) / len(weights)
            if mean > 0:
                weights = {k: v / mean for k, v in weights.items()}
        self._data["weights"] = weights
        self.save()

    def update_weight(self, name: str, delta: float,
                      lo: float = 0.1, hi: float = 3.0) -> None:
        """Nudge one strategy's weight by delta, clamped to [lo, hi]."""
        w = self._data.setdefault("weights", {})
        w[name] = float(min(hi, max(lo, w.get(name, 1.0) + delta)))
        self.save()
