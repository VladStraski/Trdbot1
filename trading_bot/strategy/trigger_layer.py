"""Триггерный слой (15m) — тайминг входа (раздел 6 ТЗ).

Правила (для лонга; для шорта — зеркально):
- бычий свечной паттерн на подтверждённом swing low: +2;
- объём выше среднего (подтверждение через объём/OBV): +1.

Чистый stdlib; работает на `FeatureSnapshot`.
"""

from __future__ import annotations

from .model import LONG, SHORT, FeatureSnapshot, PointItem, ScoreConfig


def score(f: FeatureSnapshot, cfg: ScoreConfig, direction: str) -> list[PointItem]:
    items: list[PointItem] = []

    # --- Свечной паттерн на swing ---
    if direction == LONG and f.bull_pattern and f.at_swing_low:
        items.append(PointItem(
            "bull_pattern_swing_low", cfg.pts_pattern_swing,
            "бычий паттерн на swing low",
        ))
    elif direction == SHORT and f.bear_pattern and f.at_swing_high:
        items.append(PointItem(
            "bear_pattern_swing_high", cfg.pts_pattern_swing,
            "медвежий паттерн на swing high",
        ))

    # --- Подтверждение объёмом (в обе стороны) ---
    if f.volume_avg > 0.0 and f.volume > f.volume_avg:
        items.append(PointItem(
            "volume_above_avg", cfg.pts_volume,
            f"объём {f.volume:.1f} > среднего {f.volume_avg:.1f}",
        ))

    return items
