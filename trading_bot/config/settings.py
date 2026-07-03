"""Конфигурация бота.

Все параметры и ключи читаются из окружения (`.env`), никогда не хранятся в коде.
Ключи demo и prod — раздельные переменные; окружение выбирается ЯВНО через
`BYBIT_ENV`, без автоопределения (требование раздела 10 ТЗ).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Корень пакета: .../trading_bot
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
# Корень репозитория: .../Trdbot1
PROJECT_ROOT = PACKAGE_ROOT.parent


def _load_dotenv() -> None:
    """Подхватить config/.env, если установлен python-dotenv.

    python-dotenv — опциональная зависимость: переменные окружения можно задать
    и извне. Если пакет не установлен, просто пропускаем загрузку файла (важно
    для окружений без доступа к PyPI — работает stdlib-контур без .env-файла).
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Не перетираем уже выставленное окружение (override=False).
    load_dotenv(PACKAGE_ROOT / "config" / ".env", override=False)


_load_dotenv()


# --- Маппинг человекочитаемых TF в интервалы Bybit v5 kline ---
# https://bybit-exchange.github.io/docs/v5/market/kline
BYBIT_INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W", "1M": "M",
}

# Длительность TF в миллисекундах (для выравнивания/агрегации; D/W/M не входят
# в internal-таймфреймы стратегии, поэтому здесь только интрадей).
TF_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # --- окружение / ключи ---
    env: str                      # "demo" | "prod"
    api_key: str
    api_secret: str

    # --- инструмент ---
    symbol: str = "BTCUSDT"
    category: str = "linear"
    margin_mode: str = "ISOLATED"

    # --- таймфреймы ---
    tf_context: str = "4h"
    tf_signal: str = "1h"
    tf_trigger: str = "15m"

    # --- risk ---
    risk_pct_per_trade: float = 0.01
    max_portfolio_risk: float = 0.03
    max_open_positions: int = 3
    daily_loss_limit: float = 0.03
    max_consecutive_losses: int = 5
    max_drawdown: float = 0.15
    leverage_hard_cap: int = 5
    atr_stop_mult: float = 1.75
    min_rr: float = 1.5

    # --- strategy ---
    entry_score_threshold: int = 6

    # --- storage / logs ---
    db_path: str = "trading_bot/storage/trades.sqlite"
    log_level: str = "INFO"
    log_dir: str = "trading_bot/logs"

    # --- telegram (опц.) ---
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    @property
    def is_demo(self) -> bool:
        return self.env == "demo"

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    def interval(self, tf: str) -> str:
        """Человекочитаемый TF -> интервал Bybit ('4h' -> '240')."""
        try:
            return BYBIT_INTERVAL_MAP[tf]
        except KeyError as exc:
            raise ValueError(
                f"Неизвестный таймфрейм {tf!r}. Допустимые: {list(BYBIT_INTERVAL_MAP)}"
            ) from exc

    def redacted(self) -> dict:
        """Безопасное для логов представление (без секретов)."""
        return {
            "env": self.env,
            "api_key": (self.api_key[:4] + "…") if self.api_key else "<empty>",
            "symbol": self.symbol,
            "category": self.category,
            "margin_mode": self.margin_mode,
            "tfs": [self.tf_context, self.tf_signal, self.tf_trigger],
            "risk_pct_per_trade": self.risk_pct_per_trade,
            "leverage_hard_cap": self.leverage_hard_cap,
        }


def load_settings() -> Settings:
    """Собрать Settings из окружения с явным выбором demo/prod."""
    env = _get("BYBIT_ENV", "demo").lower()
    if env not in ("demo", "prod"):
        raise ValueError(f"BYBIT_ENV должно быть 'demo' или 'prod', получено {env!r}")

    if env == "demo":
        api_key = _get("BYBIT_DEMO_API_KEY")
        api_secret = _get("BYBIT_DEMO_API_SECRET")
    else:
        api_key = _get("BYBIT_PROD_API_KEY")
        api_secret = _get("BYBIT_PROD_API_SECRET")

    return Settings(
        env=env,
        api_key=api_key,
        api_secret=api_secret,
        symbol=_get("SYMBOL", "BTCUSDT"),
        category=_get("CATEGORY", "linear"),
        margin_mode=_get("MARGIN_MODE", "ISOLATED"),
        tf_context=_get("TF_CONTEXT", "4h"),
        tf_signal=_get("TF_SIGNAL", "1h"),
        tf_trigger=_get("TF_TRIGGER", "15m"),
        risk_pct_per_trade=_get_float("RISK_PCT_PER_TRADE", 0.01),
        max_portfolio_risk=_get_float("MAX_PORTFOLIO_RISK", 0.03),
        max_open_positions=_get_int("MAX_OPEN_POSITIONS", 3),
        daily_loss_limit=_get_float("DAILY_LOSS_LIMIT", 0.03),
        max_consecutive_losses=_get_int("MAX_CONSECUTIVE_LOSSES", 5),
        max_drawdown=_get_float("MAX_DRAWDOWN", 0.15),
        leverage_hard_cap=_get_int("LEVERAGE_HARD_CAP", 5),
        atr_stop_mult=_get_float("ATR_STOP_MULT", 1.75),
        min_rr=_get_float("MIN_RR", 1.5),
        entry_score_threshold=_get_int("ENTRY_SCORE_THRESHOLD", 6),
        db_path=_get("DB_PATH", "trading_bot/storage/trades.sqlite"),
        log_level=_get("LOG_LEVEL", "INFO"),
        log_dir=_get("LOG_DIR", "trading_bot/logs"),
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=_get("TELEGRAM_CHAT_ID") or None,
    )
