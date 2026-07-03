"""Контекстный слой (4h) — обязательный трендовый фильтр (раздел 5–6 ТЗ).

Определяет разрешённое направление сделки по старшему TF: тренд задаётся
взаимным положением EMA 50/200, наличие тренда — ADX. Если тренда нет
(ADX <= порога) или EMA не выстроены — вход запрещён в обе стороны (флэт).
Чистый stdlib, работает на `FeatureSnapshot`.
"""

from __future__ import annotations

from typing import Optional

from .model import LONG, SHORT, FeatureSnapshot, ScoreConfig


def trend_direction(f: FeatureSnapshot, cfg: ScoreConfig) -> Optional[str]:
    """Разрешённое направление по контексту 4h или None (флэт/нет тренда).

    LONG  — EMA_fast > EMA_slow и ADX > adx_min.
    SHORT — EMA_fast < EMA_slow и ADX > adx_min.
    None  — тренда нет либо EMA равны.
    """
    if f.adx <= cfg.adx_min:
        return None
    if f.ema_fast > f.ema_slow:
        return LONG
    if f.ema_fast < f.ema_slow:
        return SHORT
    return None


def filter_reason(f: FeatureSnapshot, cfg: ScoreConfig) -> str:
    """Человекочитаемое объяснение решения фильтра (для лога решений)."""
    if f.adx <= cfg.adx_min:
        return f"нет тренда: ADX={f.adx:.1f} <= {cfg.adx_min:.0f}"
    if f.ema_fast == f.ema_slow:
        return "EMA_fast == EMA_slow (неопределённо)"
    direction = "LONG" if f.ema_fast > f.ema_slow else "SHORT"
    return (f"тренд {direction}: EMA_fast={f.ema_fast:.2f} vs "
            f"EMA_slow={f.ema_slow:.2f}, ADX={f.adx:.1f}")
