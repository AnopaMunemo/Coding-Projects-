"""Signal-to-order latency tracking."""
from __future__ import annotations

import time
from collections import deque
from decimal import Decimal
from typing import Deque, Dict, Optional

import numpy as np

from trading_bot.config import EXECUTION
from trading_bot.logging.logger import StructuredLogger


class LatencyMonitor:
    def __init__(self, logger: StructuredLogger, window_size: int = 1000) -> None:
        self.logger = logger
        self.latencies: Deque[float] = deque(maxlen=window_size)
        self._start_time: Optional[float] = None

    def start(self) -> None:
        self._start_time = time.perf_counter()

    def end(self, symbol: str = "") -> None:
        if self._start_time is None:
            return
        elapsed = time.perf_counter() - self._start_time
        self.latencies.append(elapsed)
        self._start_time = None

        if len(self.latencies) >= 10:
            arr = np.array(self.latencies)
            p50 = float(np.percentile(arr, 50)) * 1000
            p99 = float(np.percentile(arr, 99)) * 1000
            avg = float(np.mean(arr)) * 1000
            self.logger.info(
                "Latency stats",
                symbol=symbol,
                p50_ms=round(p50, 1),
                p99_ms=round(p99, 1),
                avg_ms=round(avg, 1),
            )
            if p99 > EXECUTION.P99_LATENCY_TARGET_MS:
                self.logger.warning(
                    "P99 latency exceeded threshold",
                    p99_ms=round(p99, 1),
                    threshold_ms=EXECUTION.P99_LATENCY_TARGET_MS,
                )

    def get_stats(self) -> Dict[str, float]:
        if not self.latencies:
            return {"p50": 0.0, "p99": 0.0, "avg": 0.0}
        arr = np.array(self.latencies)
        return {
            "p50": float(np.percentile(arr, 50)),
            "p99": float(np.percentile(arr, 99)),
            "avg": float(np.mean(arr)),
        }
