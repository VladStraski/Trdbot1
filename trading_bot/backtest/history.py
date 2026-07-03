"""Адаптер истории для бэктеста (Этап 5 ТЗ).

Строит серию `(Bar, FeatureSnapshot)` из засеянного `MultiTFAggregator`, проходя
по закрытым барам триггер-TF и извлекая КАУЗАЛЬНЫЕ признаки на момент закрытия
каждого бара (та же `extract_features`, что и в live). Единственное место с
pandas — импортируется лениво.

Микро-контекст (OBI/CVD) и funding в историческом бэктесте по умолчанию
недоступны (реал-тайм-данные не хранятся), поэтому подставляются None —
соответствующие очки не начисляются. Это осознанно консервативно.
"""

from __future__ import annotations

from typing import Optional

from ..logger import get_logger
from ..strategy.base_strategy import BaseStrategy
from .engine import Bar

log = get_logger("backtest.history")


def series_from_aggregator(
    aggregator,
    engine,
    strategy: BaseStrategy,
    tf_context: str,
    tf_signal: str,
    tf_trigger: str,
    warmup: int = 210,
) -> list[tuple[Bar, "object"]]:
    """Пройти по закрытым триггер-барам и собрать серию для бэктеста.

    warmup — сколько первых баров пропустить (пока не наберётся история для
    EMA200/ADX и т.п.), чтобы не входить на «сырых» индикаторах.
    """
    trg = aggregator.frame(tf_trigger)
    series: list[tuple[Bar, object]] = []
    n = len(trg)
    for i in range(warmup, n):
        row = trg.iloc[i]
        ref_time = int(row["close_time"])
        feat = strategy.extract_features(
            aggregator, engine, ref_time, tf_context, tf_signal, tf_trigger)
        if feat is None:
            continue
        bar = Bar(
            ts=int(row["close_time"]),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
        )
        series.append((bar, feat))
    log.info("Серия для бэктеста: %d баров (из %d, warmup=%d)",
             len(series), n, warmup)
    return series
