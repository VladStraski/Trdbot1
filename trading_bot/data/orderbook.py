"""Локальная реплика биржевого стакана (Order Book Module, Этап 3 ТЗ).

Топик Bybit v5 `orderbook.{depth}.{symbol}` (для linear глубина 1/50/200/500)
присылает СНАПШОТ (`type: "snapshot"`) и последующие ДЕЛЬТЫ (`type: "delta"`).
Этот модуль поддерживает актуальную копию книги и считает микро-триггеры
раздела 5 ТЗ: Order Book Imbalance (OBI), стены (крупные заявки), спред.

Форма сообщения Bybit (данные в `data`):
    {
      "topic": "orderbook.50.BTCUSDT",
      "type":  "snapshot" | "delta",
      "ts":    1700000000000,
      "data": {
        "s": "BTCUSDT",
        "b": [["30000.5", "1.2"], ...],   # биды, по убыванию цены
        "a": [["30001.0", "0.8"], ...],   # аски, по возрастанию цены
        "u": 12345,                        # updateId (монотонно растёт)
        "seq": 9876                        # cross sequence
      }
    }

Правила Bybit, которые здесь соблюдаются:
- `type == "snapshot"` — сбросить локальную книгу и заполнить заново.
- `type == "delta"`   — применить изменения к существующей книге.
- Уровень с количеством "0" в дельте — УДАЛЁННЫЙ уровень (снять цену из книги).
- Дельта со `u` не больше уже применённого — устаревшая (out-of-order): пропустить
  и отметить рассинхрон, чтобы транспорт мог инициировать переподписку.

Модуль намеренно свободен от pandas/pybit — чистый stdlib, чтобы логика
проверялась офлайн проигрыванием потока (см. `stream_replay.py`), без сети
и без тяжёлых зависимостей.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..logger import get_logger

log = get_logger("data.orderbook")


@dataclass(frozen=True)
class Level:
    """Один ценовой уровень книги."""

    price: float
    qty: float


@dataclass(frozen=True)
class Wall:
    """Крупная заявка («стена») на одной из сторон книги."""

    side: str          # "bid" | "ask"
    price: float
    qty: float
    ratio: float       # во сколько раз объём уровня больше среднего по стороне


class OrderBook:
    """Локальная реплика стакана с расчётом OBI, стен и спреда.

    Хранит стороны как отображение цена -> количество; отсортированные виды
    вычисляются по запросу. Для глубины MVP (до 500 уровней) это дёшево и
    избавляет от внешних зависимостей на сортированные структуры.
    """

    def __init__(self, symbol: Optional[str] = None) -> None:
        self.symbol = symbol
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id: Optional[int] = None
        self.last_seq: Optional[int] = None
        self.last_ts: Optional[int] = None
        self.ready: bool = False        # получен хотя бы один снапшот
        self.desync_count: int = 0      # сколько раз замечен разрыв последовательности

    # ------------------------------------------------------------------ #
    #  Приём сообщений
    # ------------------------------------------------------------------ #
    def apply(self, message: dict) -> bool:
        """Применить одно WS-сообщение стакана.

        Возвращает True, если книга изменилась (снапшот или валидная дельта),
        False — если сообщение пропущено как устаревшее/пустое.
        """
        msg_type = message.get("type")
        data = message.get("data") or {}
        if self.symbol is None:
            self.symbol = data.get("s")

        if msg_type == "snapshot":
            self._apply_snapshot(data)
            self.last_ts = message.get("ts", self.last_ts)
            return True
        if msg_type == "delta":
            changed = self._apply_delta(data)
            if changed:
                self.last_ts = message.get("ts", self.last_ts)
            return changed

        log.warning("Неизвестный type стакана: %r", msg_type)
        return False

    def _apply_snapshot(self, data: dict) -> None:
        self.bids = {}
        self.asks = {}
        for price_s, qty_s in data.get("b", []):
            self._set(self.bids, price_s, qty_s)
        for price_s, qty_s in data.get("a", []):
            self._set(self.asks, price_s, qty_s)
        self.last_update_id = _to_int(data.get("u"))
        self.last_seq = _to_int(data.get("seq"))
        self.ready = True

    def _apply_delta(self, data: dict) -> bool:
        u = _to_int(data.get("u"))
        if not self.ready:
            # Дельта раньше первого снапшота — применять некуда.
            return False
        if u is not None and self.last_update_id is not None and u <= self.last_update_id:
            # Устаревшее/дублирующее обновление — Bybit советует не применять.
            self.desync_count += 1
            log.debug("Пропущена устаревшая дельта u=%s <= last=%s",
                      u, self.last_update_id)
            return False

        for price_s, qty_s in data.get("b", []):
            self._set(self.bids, price_s, qty_s)
        for price_s, qty_s in data.get("a", []):
            self._set(self.asks, price_s, qty_s)

        if u is not None:
            self.last_update_id = u
        seq = _to_int(data.get("seq"))
        if seq is not None:
            self.last_seq = seq
        return True

    @staticmethod
    def _set(side: dict[float, float], price_s, qty_s) -> None:
        """Установить/снять уровень. Количество 0 — удаление уровня."""
        price = float(price_s)
        qty = float(qty_s)
        if qty == 0.0:
            side.pop(price, None)
        else:
            side[price] = qty

    # ------------------------------------------------------------------ #
    #  Отсортированные виды
    # ------------------------------------------------------------------ #
    def bid_levels(self, depth: Optional[int] = None) -> list[Level]:
        """Биды по убыванию цены (лучший — первый)."""
        items = sorted(self.bids.items(), key=lambda kv: kv[0], reverse=True)
        if depth is not None:
            items = items[:depth]
        return [Level(p, q) for p, q in items]

    def ask_levels(self, depth: Optional[int] = None) -> list[Level]:
        """Аски по возрастанию цены (лучший — первый)."""
        items = sorted(self.asks.items(), key=lambda kv: kv[0])
        if depth is not None:
            items = items[:depth]
        return [Level(p, q) for p, q in items]

    # ------------------------------------------------------------------ #
    #  Топ книги / спред
    # ------------------------------------------------------------------ #
    def best_bid(self) -> Optional[Level]:
        if not self.bids:
            return None
        price = max(self.bids)
        return Level(price, self.bids[price])

    def best_ask(self) -> Optional[Level]:
        if not self.asks:
            return None
        price = min(self.asks)
        return Level(price, self.asks[price])

    def mid_price(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return (bb.price + ba.price) / 2.0

    def spread(self) -> Optional[float]:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return None
        return ba.price - bb.price

    def spread_bps(self) -> Optional[float]:
        """Спред в базисных пунктах относительно mid (нормированная величина)."""
        spread = self.spread()
        mid = self.mid_price()
        if spread is None or not mid:
            return None
        return spread / mid * 10_000.0

    # ------------------------------------------------------------------ #
    #  Микро-триггеры
    # ------------------------------------------------------------------ #
    def obi(self, depth: Optional[int] = 25) -> Optional[float]:
        """Order Book Imbalance по top-`depth` уровням каждой стороны.

        OBI = (Σqty_bid - Σqty_ask) / (Σqty_bid + Σqty_ask), диапазон [-1, 1].
        > 0 — перевес заявок на покупку (давление вверх), < 0 — на продажу.
        depth=None — по всей известной книге.
        """
        bid_vol = sum(l.qty for l in self.bid_levels(depth))
        ask_vol = sum(l.qty for l in self.ask_levels(depth))
        total = bid_vol + ask_vol
        if total == 0.0:
            return None
        return (bid_vol - ask_vol) / total

    def walls(self, depth: int = 50, ratio: float = 3.0) -> list[Wall]:
        """Крупные заявки: уровни, чей объём >= `ratio`×среднего по стороне.

        Считается по top-`depth` уровням каждой стороны. Стены — кандидаты в
        уровни поддержки/сопротивления для SL/TP и подтверждения входа.
        """
        result: list[Wall] = []
        for side_name, levels in (("bid", self.bid_levels(depth)),
                                  ("ask", self.ask_levels(depth))):
            if not levels:
                continue
            avg = sum(l.qty for l in levels) / len(levels)
            if avg <= 0.0:
                continue
            for l in levels:
                r = l.qty / avg
                if r >= ratio:
                    result.append(Wall(side_name, l.price, l.qty, r))
        # Самые крупные — первыми.
        result.sort(key=lambda w: w.qty, reverse=True)
        return result

    # ------------------------------------------------------------------ #
    #  Снимок состояния
    # ------------------------------------------------------------------ #
    def snapshot(self, obi_depth: int = 25, wall_depth: int = 50,
                 wall_ratio: float = 3.0) -> dict:
        """Компактный словарь метрик для стратегии/логов."""
        bb, ba = self.best_bid(), self.best_ask()
        walls = self.walls(depth=wall_depth, ratio=wall_ratio)
        return {
            "symbol": self.symbol,
            "ts": self.last_ts,
            "ready": self.ready,
            "best_bid": bb.price if bb else None,
            "best_ask": ba.price if ba else None,
            "mid": self.mid_price(),
            "spread": self.spread(),
            "spread_bps": self.spread_bps(),
            "obi": self.obi(obi_depth),
            "n_bids": len(self.bids),
            "n_asks": len(self.asks),
            "walls": [(w.side, w.price, w.qty, round(w.ratio, 2)) for w in walls],
            "desync_count": self.desync_count,
        }


def _to_int(value) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
