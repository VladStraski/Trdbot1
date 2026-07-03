"""Поток публичных сделок и Cumulative Volume Delta (Order Book Module, Этап 3).

Топик Bybit v5 `publicTrade.{symbol}` присылает исполненные сделки. У каждой
сделки поле `S` — сторона АГРЕССОРА (тейкера): "Buy" — маркет-покупка,
"Sell" — маркет-продажа. CVD (Cumulative Volume Delta) накапливает разницу
объёмов агрессивных покупок и продаж и служит микро-триггером подтверждения
давления (раздел 5–6 ТЗ).

Форма сообщения Bybit (данные — список сделок в `data`):
    {
      "topic": "publicTrade.BTCUSDT",
      "type":  "snapshot",
      "ts":    1700000000000,
      "data": [
        {"T": 1700000000000, "s": "BTCUSDT", "S": "Buy",
         "v": "0.010", "p": "30000.5", "L": "PlusTick", "i": "...", "BT": false},
        ...
      ]
    }

Как и `orderbook.py`, модуль на чистом stdlib — тестируется офлайн
проигрыванием потока, без сети и pandas/pybit.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

from ..logger import get_logger

log = get_logger("data.trades_stream")


class TradesStream:
    """Накопитель CVD и объёмов по потоку публичных сделок.

    `cvd` — совокупная дельта с момента старта. `cvd_delta(window_ms)` даёт
    изменение за скользящее окно (растёт/падает — сигнал для confluence).
    """

    def __init__(self, window_maxlen: int = 20_000) -> None:
        self.cvd: float = 0.0
        self.buy_volume: float = 0.0
        self.sell_volume: float = 0.0
        self.trade_count: int = 0
        self.last_price: Optional[float] = None
        self.last_ts: Optional[int] = None
        # Кольцевой буфер (ts, signed_volume) для оконных метрик.
        self._recent: deque[tuple[int, float]] = deque(maxlen=window_maxlen)

    # ------------------------------------------------------------------ #
    #  Приём сообщений
    # ------------------------------------------------------------------ #
    def apply(self, message: dict) -> int:
        """Применить одно WS-сообщение publicTrade. Возвращает число сделок."""
        trades = message.get("data") or []
        applied = 0
        for trade in trades:
            if self._apply_trade(trade):
                applied += 1
        return applied

    def _apply_trade(self, trade: dict) -> bool:
        side = trade.get("S")
        try:
            volume = float(trade.get("v"))
            price = float(trade.get("p"))
        except (TypeError, ValueError):
            log.warning("Пропущена сделка с некорректными p/v: %r", trade)
            return False
        ts = _to_int(trade.get("T")) or self.last_ts or 0

        if side == "Buy":
            signed = volume
            self.buy_volume += volume
        elif side == "Sell":
            signed = -volume
            self.sell_volume += volume
        else:
            log.warning("Неизвестная сторона сделки S=%r", side)
            return False

        self.cvd += signed
        self.trade_count += 1
        self.last_price = price
        self.last_ts = ts
        self._recent.append((ts, signed))
        return True

    # ------------------------------------------------------------------ #
    #  Оконные метрики
    # ------------------------------------------------------------------ #
    def cvd_delta(self, window_ms: int) -> float:
        """Изменение CVD за последние `window_ms` миллисекунд.

        > 0 — за окно преобладали агрессивные покупки (CVD растёт),
        < 0 — агрессивные продажи. Опирается на время последней сделки, а не на
        системные часы, чтобы одинаково работать в live и в офлайн-проигрывании.
        """
        if self.last_ts is None:
            return 0.0
        cutoff = self.last_ts - window_ms
        return sum(sv for ts, sv in self._recent if ts >= cutoff)

    def imbalance_ratio(self) -> Optional[float]:
        """(buy - sell) / (buy + sell) по всему потоку, диапазон [-1, 1]."""
        total = self.buy_volume + self.sell_volume
        if total == 0.0:
            return None
        return (self.buy_volume - self.sell_volume) / total

    # ------------------------------------------------------------------ #
    #  Снимок состояния
    # ------------------------------------------------------------------ #
    def snapshot(self, window_ms: int = 60_000) -> dict:
        """Компактный словарь метрик для стратегии/логов."""
        return {
            "cvd": self.cvd,
            "cvd_delta": self.cvd_delta(window_ms),
            "cvd_window_ms": window_ms,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
            "imbalance_ratio": self.imbalance_ratio(),
            "trade_count": self.trade_count,
            "last_price": self.last_price,
            "last_ts": self.last_ts,
        }


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
