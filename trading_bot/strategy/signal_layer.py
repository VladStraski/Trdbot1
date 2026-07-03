"""Сигнальный слой (1h) — momentum-очки confluence (раздел 6 ТЗ).

Правила (для лонга; для шорта — зеркально):
- осциллятор выходит из перепроданности (пересечение вверх порога oversold): +2;
- MACD-гистограмма растёт (в сторону сделки): +1.

Чистый stdlib; работает на `FeatureSnapshot`.
"""

from __future__ import annotations

from .model import LONG, SHORT, FeatureSnapshot, PointItem, ScoreConfig


def score(f: FeatureSnapshot, cfg: ScoreConfig, direction: str) -> list[PointItem]:
    items: list[PointItem] = []

    # --- Осциллятор: выход из зоны перепроданности/перекупленности ---
    if f.osc is not None and f.osc_prev is not None:
        if direction == LONG:
            if f.osc_prev <= cfg.osc_oversold < f.osc:
                items.append(PointItem(
                    "osc_exit_oversold", cfg.pts_osc_exit,
                    f"осц {f.osc_prev:.1f}->{f.osc:.1f} вышел выше {cfg.osc_oversold:.0f}",
                ))
        elif direction == SHORT:
            if f.osc_prev >= cfg.osc_overbought > f.osc:
                items.append(PointItem(
                    "osc_exit_overbought", cfg.pts_osc_exit,
                    f"осц {f.osc_prev:.1f}->{f.osc:.1f} вышел ниже {cfg.osc_overbought:.0f}",
                ))

    # --- MACD-гистограмма растёт/падает в сторону сделки ---
    if direction == LONG and f.macd_hist > f.macd_hist_prev:
        items.append(PointItem(
            "macd_rising", cfg.pts_macd,
            f"MACD hist {f.macd_hist_prev:.4f}->{f.macd_hist:.4f}",
        ))
    elif direction == SHORT and f.macd_hist < f.macd_hist_prev:
        items.append(PointItem(
            "macd_falling", cfg.pts_macd,
            f"MACD hist {f.macd_hist_prev:.4f}->{f.macd_hist:.4f}",
        ))

    return items
