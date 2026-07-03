"""Kill switch (Этап 9, раздел 7 ТЗ).

Ручной (немедленное закрытие всего) и автоматический аварийный останов.
Авто-триггеры: серия API-ошибок подряд, разрыв ценового фида дольше N секунд,
потеря соединения. Здесь — детектор состояния (чистый stdlib); фактическое
закрытие позиций выполняет ExecutionEngine.close_position.

Время передаётся аргументом (мс), а не берётся из системных часов, — чтобы
детектор одинаково работал в live и в офлайн-проигрывании.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KillSwitchConfig:
    max_api_errors: int = 5           # подряд API-ошибок -> стоп
    max_feed_gap_ms: int = 30_000     # разрыв фида цен -> стоп


class KillSwitch:
    def __init__(self, config: KillSwitchConfig | None = None) -> None:
        self.cfg = config or KillSwitchConfig()
        self.tripped = False
        self.reason = ""
        self._api_errors = 0
        self._last_feed_ms: int | None = None

    # --- ручной ---
    def trip(self, reason: str = "manual") -> None:
        self.tripped = True
        self.reason = reason

    def reset(self) -> None:
        self.tripped = False
        self.reason = ""
        self._api_errors = 0

    # --- авто: API-ошибки ---
    def record_api_error(self) -> bool:
        self._api_errors += 1
        if self._api_errors >= self.cfg.max_api_errors:
            self.trip(f"серия API-ошибок: {self._api_errors}")
        return self.tripped

    def record_api_success(self) -> None:
        self._api_errors = 0

    # --- авто: разрыв фида ---
    def record_feed(self, ts_ms: int) -> None:
        self._last_feed_ms = ts_ms

    def check_feed_gap(self, now_ms: int) -> bool:
        """Проверить разрыв ценового фида на момент now_ms."""
        if self._last_feed_ms is None:
            return self.tripped
        if now_ms - self._last_feed_ms > self.cfg.max_feed_gap_ms:
            self.trip(f"разрыв фида цен {now_ms - self._last_feed_ms} мс")
        return self.tripped

    # --- авто: потеря соединения ---
    def on_disconnect(self) -> None:
        self.trip("потеря соединения")
