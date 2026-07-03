"""
Centralized structured logging with rotation, trade events, and remote alerts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


class StructuredLogger:
    """JSON-capable logger with trade audit events and Telegram alerts."""

    def __init__(
        self,
        bot_name: str = "swing_trader_bot",
        log_dir: str = "logs",
        telegram_token: str = "",
        telegram_chat_id: str = "",
    ) -> None:
        self.bot_name = bot_name
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(bot_name)
        if self.logger.handlers:
            return
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        )

        file_handler = logging.handlers.RotatingFileHandler(
            str(Path(log_dir) / f"{bot_name}.log"),
            maxBytes=10_000_000,
            backupCount=30,
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

    def debug(self, message: str, **context: Any) -> None:
        self._log(logging.DEBUG, message, context)

    def info(self, message: str, **context: Any) -> None:
        self._log(logging.INFO, message, context)

    def warning(self, message: str, **context: Any) -> None:
        self._log(logging.WARNING, message, context)

    def error(self, message: str, exc_info: bool = False, **context: Any) -> None:
        self._log(logging.ERROR, message, context, exc_info=exc_info)

    def critical(self, message: str, exc_info: bool = False, **context: Any) -> None:
        self._log(logging.CRITICAL, message, context, exc_info=exc_info)
        if self.telegram_token and self.telegram_chat_id:
            asyncio.create_task(self._send_telegram_alert(message, context))

    def log_trade(self, event_type: str, **kwargs: Any) -> None:
        entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            **kwargs,
        }
        payload = json.dumps(entry, default=str)
        if event_type in ("ORDER_PLACED", "ORDER_FILLED", "POSITION_OPENED", "POSITION_CLOSED"):
            self.logger.info("TRADE: %s", payload)
        elif event_type in ("ORDER_FAILED", "ORDER_CANCELLED", "EMERGENCY_CLOSE"):
            self.logger.warning("TRADE: %s", payload)
        else:
            self.logger.info("TRADE: %s", payload)

    def log_critical_event(self, message: str, **context: Any) -> None:
        self.critical(message, **context)

    def _log(
        self,
        level: int,
        message: str,
        context: Dict[str, Any],
        exc_info: bool = False,
    ) -> None:
        if context:
            message = f"{message} | {json.dumps(context, default=str)}"
        self.logger.log(level, message, exc_info=exc_info)

    async def _send_telegram_alert(self, message: str, context: Dict[str, Any]) -> None:
        try:
            import aiohttp

            text = f"🚨 {message}\n{json.dumps(context, indent=2, default=str)[:1500]}"
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"chat_id": self.telegram_chat_id, "text": text},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        self.logger.error("Telegram alert failed: HTTP %s", resp.status)
        except Exception as exc:
            self.logger.error("Telegram alert error: %s", exc)
