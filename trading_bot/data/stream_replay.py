"""Офлайн-проигрывание потока стакана и сделок (Этап 3 ТЗ).

Транспорт (WS) отделён от обработчиков (`OrderBook`, `TradesStream`), поэтому всю
логику Order Book Module можно проверять без сети — «проиграв» заранее записанный
или синтетический поток сообщений в форме Bybit v5.

`synthetic_public_stream()` строит ДЕТЕРМИНИРОВАННЫЙ поток (без случайности —
как синтетические klines Этапа 1): один снапшот стакана, затем чередующиеся
дельты стакана и пакеты publicTrade. Поток задаёт лёгкий рост цены и перевес
агрессивных покупок, чтобы OBI и CVD демонстративно росли.

`replay()` прогоняет поток сообщений через обработчики по топику.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from .orderbook import OrderBook
from .trades_stream import TradesStream

# Топики (префиксы) Bybit v5 публичных данных.
TOPIC_ORDERBOOK = "orderbook"
TOPIC_PUBLIC_TRADE = "publicTrade"


def synthetic_public_stream(
    symbol: str = "BTCUSDT",
    depth: int = 50,
    n_updates: int = 40,
    start_ts: int = 1_700_000_000_000,
    ts_step_ms: int = 250,
    mid_price: float = 30_000.0,
    tick: float = 0.5,
) -> list[dict]:
    """Синтетический хронологический поток сообщений Bybit (snapshot + дельты + сделки).

    Полностью детерминирован: одинаковый вход -> одинаковый выход, случайность не
    используется (для воспроизводимых офлайн-тестов).
    """
    messages: list[dict] = []
    ts = start_ts

    # --- Снапшот стакана ---------------------------------------------------
    # Биды ниже mid, аски выше; количества плавно убывают от лучшей цены.
    # На биды добавляем «стену» (крупный уровень) для проверки walls().
    bids: list[list[str]] = []
    asks: list[list[str]] = []
    for i in range(depth):
        bid_price = mid_price - tick * (i + 1)
        ask_price = mid_price + tick * (i + 1)
        bid_qty = 1.0 + (depth - i) * 0.1        # ближе к рынку — объёмнее
        ask_qty = 0.8 + (depth - i) * 0.1
        bids.append([f"{bid_price:.1f}", f"{bid_qty:.3f}"])
        asks.append([f"{ask_price:.1f}", f"{ask_qty:.3f}"])
    # Стена на 5-м биде: объём кратно больше среднего.
    bids[4][1] = "50.000"

    update_id = 1000
    messages.append({
        "topic": f"{TOPIC_ORDERBOOK}.{depth}.{symbol}",
        "type": "snapshot",
        "ts": ts,
        "data": {
            "s": symbol,
            "b": bids,
            "a": asks,
            "u": update_id,
            "seq": update_id,
        },
    })

    # --- Дельты стакана + сделки ------------------------------------------
    best_bid = mid_price - tick
    best_ask = mid_price + tick
    for step in range(n_updates):
        ts += ts_step_ms
        update_id += 1

        # Дельта: наращиваем объём на лучшем биде (усиление спроса) и слегка
        # снимаем объём на лучшем аске — сдвиг давления вверх.
        new_best_bid_qty = 2.0 + step * 0.05
        b_changes = [[f"{best_bid:.1f}", f"{new_best_bid_qty:.3f}"]]
        a_changes = [[f"{best_ask:.1f}", f"{max(0.1, 1.0 - step * 0.02):.3f}"]]

        messages.append({
            "topic": f"{TOPIC_ORDERBOOK}.{depth}.{symbol}",
            "type": "delta",
            "ts": ts,
            "data": {
                "s": symbol,
                "b": b_changes,
                "a": a_changes,
                "u": update_id,
                "seq": update_id,
            },
        })

        # Пакет сделок: перевес агрессивных покупок (2 buy : 1 sell) -> CVD растёт.
        trades = [
            {"T": ts, "s": symbol, "S": "Buy", "v": "0.030", "p": f"{best_ask:.1f}"},
            {"T": ts, "s": symbol, "S": "Buy", "v": "0.020", "p": f"{best_ask:.1f}"},
            {"T": ts, "s": symbol, "S": "Sell", "v": "0.025", "p": f"{best_bid:.1f}"},
        ]
        messages.append({
            "topic": f"{TOPIC_PUBLIC_TRADE}.{symbol}",
            "type": "snapshot",
            "ts": ts,
            "data": trades,
        })

    return messages


def replay(
    messages: Iterable[dict],
    order_book: Optional[OrderBook] = None,
    trades: Optional[TradesStream] = None,
    on_update: Optional[Callable[[OrderBook, TradesStream, dict], None]] = None,
) -> tuple[OrderBook, TradesStream]:
    """Проиграть поток сообщений в обработчики, маршрутизируя по топику.

    on_update(order_book, trades, message) — необязательный колбэк после каждого
    применённого сообщения (для пошагового логирования/тестов).
    """
    order_book = order_book or OrderBook()
    trades = trades or TradesStream()

    for msg in messages:
        topic = msg.get("topic", "")
        if topic.startswith(TOPIC_ORDERBOOK + "."):
            order_book.apply(msg)
        elif topic.startswith(TOPIC_PUBLIC_TRADE + "."):
            trades.apply(msg)
        # прочие топики игнорируются
        if on_update is not None:
            on_update(order_book, trades, msg)

    return order_book, trades
