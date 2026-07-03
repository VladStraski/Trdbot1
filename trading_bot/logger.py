"""Структурированное логирование.

Каждое решение стратегии и каждое действие бота логируется (раздел 10 ТЗ) —
консоль (человекочитаемо) + файл `.jsonl` (машиночитаемо, для последующего
разбора). Никаких секретов в логах.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CONFIGURED = False


class _JsonlFormatter(logging.Formatter):
    """Пишет каждую запись как одну JSON-строку."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Произвольные поля, переданные через extra={"data": {...}}
        data = getattr(record, "data", None)
        if data is not None:
            payload["data"] = data
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(log_dir: str = "trading_bot/logs", level: str = "INFO") -> None:
    """Настроить корневой логгер: консоль + trading_bot/logs/bot.jsonl."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    root = logging.getLogger("trading_bot")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    )
    root.addHandler(console)

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(Path(log_dir) / "bot.jsonl", encoding="utf-8")
    file_handler.setFormatter(_JsonlFormatter())
    root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Получить именованный логгер внутри пространства trading_bot.*"""
    return logging.getLogger(f"trading_bot.{name}")


def log_event(logger: logging.Logger, level: int, msg: str, **data: Any) -> None:
    """Залогировать событие со структурированными полями (попадут в .jsonl)."""
    logger.log(level, msg, extra={"data": data or None})
