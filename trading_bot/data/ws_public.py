"""Транспорт публичных WebSocket-стримов Bybit (Этап 3 ТЗ).

Отделяет ДОСТАВКУ сообщений (pybit WebSocket) от их ОБРАБОТКИ (`OrderBook`,
`TradesStream`). Обработчики — чистый stdlib и тестируются офлайн; этот модуль —
единственное место, где нужна сеть и pybit. `pybit` импортируется лениво (внутри
`start()`), чтобы модуль можно было импортировать без установленной зависимости.

Публичные данные (стакан, сделки) у Bybit ОБЩИЕ для mainnet и demo — стрим
`wss://stream.bybit.com/v5/public/{category}` (раздел 3 ТЗ). Отдельного demo-
стрима для публичных данных нет; приватные стримы (позиции/ордера) появятся на
Этапе 8 отдельным модулем.

Reconnect: pybit переустанавливает соединение и переотправляет подписки
самостоятельно. Поверх этого при рассинхроне стакана (`OrderBook.desync_count`
растёт) полагаемся на снапшот, который Bybit присылает после переподключения
(`type == "snapshot"` сбрасывает локальную книгу). Независимый супервизор с
backoff для приватных стримов будет добавлен на Этапе 8 (раздел 10 ТЗ).
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..logger import get_logger
from .orderbook import OrderBook
from .trades_stream import TradesStream

log = get_logger("data.ws_public")

# Допустимые глубины стакана Bybit v5 для linear.
VALID_DEPTHS = (1, 50, 200, 500)


class PublicWSFeed:
    """Подписка на `orderbook.{depth}.{symbol}` и `publicTrade.{symbol}`.

    Держит собственные `OrderBook` и `TradesStream`, наполняемые из колбэков
    pybit. Опциональные `on_orderbook` / `on_trade` дают внешнему коду хук на
    каждое обновление (например, пересчёт confluence-скоринга).
    """

    def __init__(
        self,
        symbol: str,
        depth: int = 50,
        category: str = "linear",
        testnet: bool = False,
        on_orderbook: Optional[Callable[[OrderBook, dict], None]] = None,
        on_trade: Optional[Callable[[TradesStream, dict], None]] = None,
    ) -> None:
        if depth not in VALID_DEPTHS:
            raise ValueError(
                f"depth={depth} недопустим для linear; допустимые: {VALID_DEPTHS}"
            )
        self.symbol = symbol
        self.depth = depth
        self.category = category
        self.testnet = testnet
        self.on_orderbook = on_orderbook
        self.on_trade = on_trade

        self.order_book = OrderBook(symbol=symbol)
        self.trades = TradesStream()
        self._ws: Any = None

    # ------------------------------------------------------------------ #
    #  Жизненный цикл
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        """Открыть публичный WS и подписаться на стакан и сделки.

        pybit запускает WS в собственном потоке и вызывает колбэки по мере
        поступления сообщений. Импорт pybit ленивый — модуль импортируется и
        тестируется без установленной библиотеки.
        """
        from pybit.unified_trading import WebSocket  # ленивый импорт

        self._ws = WebSocket(testnet=self.testnet, channel_type=self.category)
        self._ws.orderbook_stream(self.depth, self.symbol, self._handle_orderbook)
        self._ws.trade_stream(self.symbol, self._handle_trade)
        log.info(
            "Публичный WS запущен: orderbook.%d.%s + publicTrade.%s (category=%s)",
            self.depth, self.symbol, self.symbol, self.category,
        )

    def stop(self) -> None:
        """Закрыть WS-соединение, если оно открыто."""
        if self._ws is not None:
            try:
                self._ws.exit()
            except Exception as exc:  # noqa: BLE001 - best-effort закрытие
                log.warning("Ошибка при закрытии WS: %s", exc)
            finally:
                self._ws = None
                log.info("Публичный WS остановлен")

    # ------------------------------------------------------------------ #
    #  Колбэки pybit
    # ------------------------------------------------------------------ #
    def _handle_orderbook(self, message: dict) -> None:
        try:
            self.order_book.apply(message)
            if self.on_orderbook is not None:
                self.on_orderbook(self.order_book, message)
        except Exception:  # noqa: BLE001 - защищаем поток WS от падения
            log.exception("Сбой обработки сообщения стакана")

    def _handle_trade(self, message: dict) -> None:
        try:
            self.trades.apply(message)
            if self.on_trade is not None:
                self.on_trade(self.trades, message)
        except Exception:  # noqa: BLE001 - защищаем поток WS от падения
            log.exception("Сбой обработки сообщения publicTrade")
