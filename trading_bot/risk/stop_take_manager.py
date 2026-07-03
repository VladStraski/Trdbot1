"""Управление стопом и тейком (раздел 7 ТЗ).

- Начальный стоп: k×ATR или ближайший swing-уровень (что дальше — чтобы стоп не
  был неоправданно близко).
- Частичное закрытие на 1R, безубыток после 1R, трейлинг по ATR/структуре.

Функции чистые (stdlib); состояние позиции держит `PositionRiskState`.
LONG/SHORT — из strategy.model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..strategy.model import LONG, SHORT


def initial_stop(direction: str, entry: float, atr: float, atr_mult: float,
                 swing: Optional[float] = None, use_swing: bool = True) -> float:
    """Начальный стоп: дистанция k×ATR, при наличии — не ближе swing-уровня."""
    dist = atr_mult * (atr if atr > 0 else entry * 0.005)
    if direction == LONG:
        stop = entry - dist
        if use_swing and swing is not None:
            stop = min(stop, swing)          # swing_low ниже -> отодвигаем стоп
        return stop
    stop = entry + dist
    if use_swing and swing is not None:
        stop = max(stop, swing)              # swing_high выше
    return stop


def take_from_rr(direction: str, entry: float, stop: float, rr: float) -> float:
    """Тейк по целевому R:R (по цене, без издержек)."""
    risk = abs(entry - stop)
    return entry + rr * risk if direction == LONG else entry - rr * risk


def price_at_r(direction: str, entry: float, stop: float, r: float) -> float:
    """Цена, соответствующая r-кратному риску (например 1R для частичного тейка)."""
    risk = abs(entry - stop)
    return entry + r * risk if direction == LONG else entry - r * risk


def reached_r(direction: str, entry: float, stop: float, price: float) -> float:
    """Сколько R прошла цена в пользу сделки (может быть отрицательным)."""
    risk = abs(entry - stop)
    if risk <= 0:
        return 0.0
    move = (price - entry) if direction == LONG else (entry - price)
    return move / risk


def trailing_stop_atr(direction: str, price: float, atr: float,
                      atr_mult: float) -> float:
    """Трейлинг-стоп по ATR от текущей цены."""
    dist = atr_mult * (atr if atr > 0 else price * 0.005)
    return price - dist if direction == LONG else price + dist


@dataclass
class PositionRiskState:
    """Состояние риск-менеджмента открытой позиции."""

    direction: str
    entry: float
    stop: float
    take: float
    breakeven_done: bool = False
    partial_done: bool = False

    def update(self, price: float, atr: float, atr_mult: float = 1.0,
               trail_after_r: float = 1.0) -> "PositionRiskState":
        """Подтянуть стоп: безубыток после 1R, затем трейлинг по ATR.

        Стоп двигается ТОЛЬКО в сторону прибыли (никогда не ослабляется).
        """
        r = reached_r(self.direction, self.entry, self.stop, price)
        if r >= 1.0 and not self.breakeven_done:
            self._tighten(self.entry)          # безубыток
            self.breakeven_done = True
        if r >= trail_after_r:
            self._tighten(trailing_stop_atr(self.direction, price, atr, atr_mult))
        return self

    def _tighten(self, new_stop: float) -> None:
        if self.direction == LONG:
            self.stop = max(self.stop, new_stop)
        else:
            self.stop = min(self.stop, new_stop)
