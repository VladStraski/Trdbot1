"""Транспорт приватного WebSocket Bybit (Этап 8 ТЗ).

Приватные стримы (позиции/ордера/исполнения) для синхронизации состояния.
Для demo используется `wss://stream-demo.bybit.com` (pybit `demo=True`).
Как и публичный транспорт, отделён от обработки: наполняет `PositionManager`,
а разбор/сверка — чистый stdlib. `pybit` импортируется лениво.

Reconnect: pybit переустанавливает приватное соединение и переотправляет
подписки; поверх этого периодическая REST-сверка (`PositionManager.reconcile`)
служит независимым бэкстопом на случай пропущенных событий (раздел 10 ТЗ).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..logger import get_logger
from .position_manager import PositionManager

log = get_logger("execution.private_ws")


class PrivateWSFeed:
    def __init__(self, settings, position_manager: Optional[PositionManager] = None,
                 on_position: Optional[Callable[[PositionManager, dict], None]] = None,
                 on_order: Optional[Callable[[dict], None]] = None) -> None:
        self.settings = settings
        self.positions = position_manager or PositionManager()
        self.on_position = on_position
        self.on_order = on_order
        self._ws: Any = None

    def start(self) -> None:
        """Открыть приватный WS и подписаться на позиции и ордера."""
        from pybit.unified_trading import WebSocket  # ленивый импорт

        self._ws = WebSocket(
            testnet=False,
            demo=self.settings.is_demo,
            channel_type="private",
            api_key=self.settings.api_key or None,
            api_secret=self.settings.api_secret or None,
        )
        self._ws.position_stream(self._handle_position)
        self._ws.order_stream(self._handle_order)
        log.info("Приватный WS запущен (demo=%s)", self.settings.is_demo)

    def stop(self) -> None:
        if self._ws is not None:
            try:
                self._ws.exit()
            except Exception as exc:  # noqa: BLE001
                log.warning("Ошибка при закрытии приватного WS: %s", exc)
            finally:
                self._ws = None

    # ------------------------------------------------------------------ #
    def _handle_position(self, message: dict) -> None:
        try:
            self.positions.apply_events(message.get("data") or [])
            if self.on_position is not None:
                self.on_position(self.positions, message)
        except Exception:  # noqa: BLE001
            log.exception("Сбой обработки события позиции")

    def _handle_order(self, message: dict) -> None:
        try:
            if self.on_order is not None:
                self.on_order(message)
        except Exception:  # noqa: BLE001
            log.exception("Сбой обработки события ордера")
