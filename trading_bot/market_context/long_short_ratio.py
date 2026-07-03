"""Long/Short account ratio (Market Context, раздел 4.4 ТЗ).

Обёртка над `/v5/market/account-ratio`. Экстремальный перекос толпы в одну
сторону — контр-сигнал (переполненная сделка). Парсинг тестируется офлайн.
"""

from __future__ import annotations

from typing import Optional

from ..logger import get_logger

log = get_logger("market_context.long_short_ratio")


class LongShortRatioContext:
    def __init__(self, client) -> None:
        self.client = client

    def latest(self) -> Optional[float]:
        """Отношение buyRatio/sellRatio по последней точке (>1 — перевес лонгов)."""
        points = self.client.get_long_short_ratio()
        if not points:
            return None
        latest = points[0]  # новые сверху
        try:
            buy = float(latest.get("buyRatio"))
            sell = float(latest.get("sellRatio"))
        except (TypeError, ValueError):
            return None
        if sell == 0.0:
            return None
        return buy / sell
