"""Политика переподключения с экспоненциальным backoff (раздел 10 ТЗ).

Каждый WS-стрим переподключается независимо, с нарастающей паузой между
попытками. Здесь — чистая (stdlib, детерминированная) политика задержек; сам
цикл переподключения живёт в транспортных обёртках. Тестируется офлайн.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReconnectPolicy:
    base_delay: float = 1.0          # первая пауза, сек
    factor: float = 2.0             # множитель роста
    max_delay: float = 60.0         # потолок паузы
    max_attempts: int = 0           # 0 = без ограничения попыток

    def delay_for(self, attempt: int) -> float:
        """Пауза перед попыткой `attempt` (attempt >= 1)."""
        if attempt < 1:
            return 0.0
        delay = self.base_delay * (self.factor ** (attempt - 1))
        return min(delay, self.max_delay)

    def should_retry(self, attempt: int) -> bool:
        """Делать ли попытку `attempt` (с учётом лимита max_attempts)."""
        return self.max_attempts <= 0 or attempt <= self.max_attempts

    def delays(self, n: int) -> list[float]:
        """Первые n задержек (для логов/тестов)."""
        return [self.delay_for(i) for i in range(1, n + 1)]
