"""Funding rate (Market Context, раздел 4.4 ТЗ).

Тонкая обёртка над Bybit REST (`/v5/market/tickers`): funding начисляется каждые
8ч и влияет и на confluence-скоринг (перегрев лонгов/шортов), и на издержки R:R
(раздел 7). Клиент передаётся зависимостью (duck-typed `get_tickers()`), поэтому
парсинг тестируется офлайн с фейковым клиентом, без сети.
"""

from __future__ import annotations

from typing import Optional

from ..logger import get_logger

log = get_logger("market_context.funding")


class FundingContext:
    def __init__(self, client) -> None:
        self.client = client

    def current_rate(self) -> Optional[float]:
        """Текущий funding rate (доля за интервал) или None, если недоступен."""
        ticker = self.client.get_tickers()
        raw = ticker.get("fundingRate")
        if raw in (None, ""):
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            log.warning("Некорректный fundingRate: %r", raw)
            return None

    def snapshot(self) -> dict:
        ticker = self.client.get_tickers()
        return {
            "funding_rate": self._as_float(ticker.get("fundingRate")),
            "next_funding_time": ticker.get("nextFundingTime"),
            "last_price": self._as_float(ticker.get("lastPrice")),
        }

    @staticmethod
    def _as_float(raw) -> Optional[float]:
        try:
            return float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            return None
