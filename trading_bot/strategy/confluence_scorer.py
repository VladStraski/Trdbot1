"""Confluence-скоринг (Этап 4, раздел 6 ТЗ).

Собирает очки трёх слоёв (контекст/сигнал/триггер) плюс микро-триггер (стакан +
CVD) и рыночный контекст (funding), применяет обязательный трендовый фильтр 4h и
порог входа. Возвращает `Signal`. Не жёсткое «всё сразу», а сумма очков >= порога.

Чистый stdlib — тестируется офлайн, одинаков в live и бэктесте.
"""

from __future__ import annotations

from . import context_filter, signal_layer, trigger_layer
from .model import (LONG, SHORT, FeatureSnapshot, PointItem, ScoreConfig,
                    Signal)


class ConfluenceScorer:
    """Оценивает `FeatureSnapshot` и выдаёт торговый `Signal`."""

    def __init__(self, config: ScoreConfig | None = None) -> None:
        self.config = config or ScoreConfig()

    # ------------------------------------------------------------------ #
    def _micro_points(self, f: FeatureSnapshot, direction: str) -> list[PointItem]:
        """Очки микро-триггера: стакан (OBI) и поток сделок (CVD) в сторону сделки."""
        if f.obi is None or f.cvd_delta is None:
            return []
        cfg = self.config
        if direction == LONG and f.obi > 0 and f.cvd_delta > 0:
            return [PointItem("micro_bull", cfg.pts_micro,
                              f"OBI={f.obi:+.3f}>0 и CVDΔ={f.cvd_delta:+.4f}>0")]
        if direction == SHORT and f.obi < 0 and f.cvd_delta < 0:
            return [PointItem("micro_bear", cfg.pts_micro,
                              f"OBI={f.obi:+.3f}<0 и CVDΔ={f.cvd_delta:+.4f}<0")]
        return []

    def _funding_points(self, f: FeatureSnapshot, direction: str) -> list[PointItem]:
        """Funding: не экстремальный против сделки — +1, экстремальный — штраф."""
        if f.funding_rate is None:
            return []
        cfg = self.config
        fr = f.funding_rate
        # Для лонга опасен сильно ПОЛОЖИТЕЛЬНЫЙ funding (перегрев лонгов);
        # для шорта — сильно ОТРИЦАТЕЛЬНЫЙ.
        if direction == LONG:
            extreme = fr > cfg.funding_extreme
        else:
            extreme = fr < -cfg.funding_extreme
        if extreme:
            return [PointItem("funding_extreme", cfg.pts_funding_bad,
                              f"funding={fr:+.5f} экстремальный против сделки")]
        return [PointItem("funding_ok", cfg.pts_funding_ok,
                          f"funding={fr:+.5f} в норме")]

    # ------------------------------------------------------------------ #
    def evaluate(self, f: FeatureSnapshot) -> Signal:
        """Полная оценка: фильтр -> очки -> порог -> Signal."""
        cfg = self.config
        direction = context_filter.trend_direction(f, cfg)

        if direction is None:
            # Обязательный фильтр не пройден — входа нет, но решение логируем.
            return Signal(
                direction=None, score=0, threshold=cfg.entry_threshold,
                entered=False, price=f.price, ts=f.ts, breakdown=[],
                reason=context_filter.filter_reason(f, cfg),
            )

        breakdown: list[PointItem] = []
        breakdown += signal_layer.score(f, cfg, direction)
        breakdown += trigger_layer.score(f, cfg, direction)
        breakdown += self._micro_points(f, direction)
        breakdown += self._funding_points(f, direction)

        total = sum(p.points for p in breakdown)
        entered = total >= cfg.entry_threshold
        reason = (f"скор {total} {'>=' if entered else '<'} порога "
                  f"{cfg.entry_threshold} ({context_filter.filter_reason(f, cfg)})")

        return Signal(
            direction=direction, score=total, threshold=cfg.entry_threshold,
            entered=entered, price=f.price, ts=f.ts, breakdown=breakdown,
            reason=reason,
        )
