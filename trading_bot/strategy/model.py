"""Типы данных стратегии (Этап 4 ТЗ).

Ключевая идея — как в Order Book Module: логика скоринга отделена от pandas.
`FeatureSnapshot` — плоский набор СКАЛЯРНЫХ признаков (показания индикаторов на
баре принятия решения + микро-контекст стакана + funding), не DataFrame.

Благодаря этому:
- confluence-скоринг (`confluence_scorer.py` и слои) — чистый stdlib, тестируется
  офлайн без pandas и одинаково работает в live и в бэктесте;
- извлечение признаков из enriched-DataFrame (pandas) — тонкий адаптер в
  `base_strategy.py`, единственное место с зависимостью от pandas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Направление сделки.
LONG = "long"
SHORT = "short"


@dataclass(frozen=True)
class FeatureSnapshot:
    """Скалярные признаки на момент принятия решения (без look-ahead).

    Все значения относятся к ПОСЛЕДНИМ ЗАКРЫТЫМ барам своих TF (контекст 4h,
    сигнал 1h, триггер 15m) и к текущему микро-контексту. Извлекаются адаптером
    из выровненных буферов агрегатора.
    """

    price: float                      # цена последнего закрытого триггер-бара
    ts: int = 0                       # время решения (close_time триггер-бара), мс

    # --- Контекст (4h): направление тренда, режим рынка ---
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    adx: float = 0.0

    # --- Сигнал (1h): momentum ---
    # Осциллятор — RSI ИЛИ Stoch RSI %K (не оба, раздел 6 ТЗ); прошлое значение
    # нужно, чтобы поймать выход из перепроданности/перекупленности.
    osc: Optional[float] = None
    osc_prev: Optional[float] = None
    macd_hist: float = 0.0
    macd_hist_prev: float = 0.0

    # --- Триггер (15m): тайминг ---
    bull_pattern: bool = False        # бычий свечной паттерн на триггер-баре
    bear_pattern: bool = False
    at_swing_low: bool = False        # бар у подтверждённого swing low
    at_swing_high: bool = False
    volume: float = 0.0
    volume_avg: float = 0.0           # средний объём (подтверждение через OBV/объём)
    atr: float = 0.0                  # ATR триггер-TF (для расчёта стопа/размера)
    swing_low_price: Optional[float] = None   # последний подтверждённый swing low
    swing_high_price: Optional[float] = None  # последний подтверждённый swing high

    # --- Микро-триггер (реал-тайм): стакан и поток сделок ---
    obi: Optional[float] = None       # Order Book Imbalance [-1..1]
    cvd_delta: Optional[float] = None # изменение CVD за окно (знак = давление)

    # --- Рыночный контекст ---
    funding_rate: Optional[float] = None  # текущий funding (доля за интервал)


@dataclass(frozen=True)
class PointItem:
    """Один вклад в confluence-скор: метка правила и начисленные очки."""

    label: str
    points: int
    detail: str = ""


@dataclass(frozen=True)
class Signal:
    """Результат оценки стратегии на баре.

    entered=True означает, что суммарный скор прошёл порог входа И обязательный
    трендовый фильтр выполнен. На Этапе 4 сигнал только логируется, ордера — Этап 7.
    """

    direction: Optional[str]          # LONG | SHORT | None (нет условий/флэт)
    score: int
    threshold: int
    entered: bool
    price: float
    ts: int
    breakdown: list[PointItem] = field(default_factory=list)
    reason: str = ""                  # почему нет входа (для лога решений)

    def as_dict(self) -> dict:
        return {
            "direction": self.direction,
            "score": self.score,
            "threshold": self.threshold,
            "entered": self.entered,
            "price": self.price,
            "ts": self.ts,
            "reason": self.reason,
            "breakdown": [(p.label, p.points) for p in self.breakdown],
        }


@dataclass(frozen=True)
class ScoreConfig:
    """Параметры confluence-скоринга (раздел 6 ТЗ; калибруются на бэктесте)."""

    entry_threshold: int = 6
    adx_min: float = 20.0             # обязательный фильтр: тренд есть, если ADX>20

    # Пороги осциллятора (для RSI: 30/70; для Stoch RSI задать 20/80).
    osc_oversold: float = 30.0
    osc_overbought: float = 70.0

    # Очки по правилам (знак и величина — из ТЗ).
    pts_osc_exit: int = 2             # выход из перепроданности/перекупленности
    pts_macd: int = 1                # MACD-гистограмма растёт/падает в сторону сделки
    pts_pattern_swing: int = 2       # свечной паттерн на swing
    pts_volume: int = 1              # объём выше среднего
    pts_micro: int = 2               # OBI и CVD в сторону сделки
    pts_funding_ok: int = 1          # funding не экстремальный
    pts_funding_bad: int = -2        # funding экстремальный против сделки

    # Порог «экстремального» funding (доля за 8ч). >0.05% считаем перегревом.
    funding_extreme: float = 0.0005

    # Использовать ли Stoch RSI (влияет только на дефолтные пороги-подсказки).
    use_stoch_rsi: bool = False
