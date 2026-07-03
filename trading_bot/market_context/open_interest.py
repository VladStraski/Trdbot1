"""Open Interest (Market Context, раздел 4.4 ТЗ).

Обёртка над `/v5/market/open-interest`. Рост OI при росте цены — приток нового
капитала (подтверждение тренда); рост OI при падении — наращивание шортов и т.п.
Парсинг тестируется офлайн с фейковым клиентом.
"""

from __future__ import annotations

from typing import Optional

from ..logger import get_logger

log = get_logger("market_context.open_interest")


class OpenInterestContext:
    def __init__(self, client) -> None:
        self.client = client

    def _series(self) -> list[float]:
        """OI по времени в хронологическом порядке (старые -> новые)."""
        points = self.client.get_open_interest()
        values: list[float] = []
        # Bybit отдаёт новые сверху -> разворачиваем в хронологию.
        for p in reversed(points):
            raw = p.get("openInterest")
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                continue
        return values

    def latest(self) -> Optional[float]:
        series = self._series()
        return series[-1] if series else None

    def trend(self) -> Optional[float]:
        """Относительное изменение OI за окно: (last - first) / first.

        > 0 — OI растёт (приток позиций), < 0 — сокращается. None — мало данных.
        """
        series = self._series()
        if len(series) < 2 or series[0] == 0.0:
            return None
        return (series[-1] - series[0]) / series[0]
