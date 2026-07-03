"""Тесты Order Book Module (Этап 3): снапшот/дельта, спред, OBI, стены.

Опираются только на stdlib-модули `orderbook`/`stream_replay` — без сети,
pandas и pybit."""

from trading_bot.data.orderbook import OrderBook
from trading_bot.data.stream_replay import replay, synthetic_public_stream


def _snapshot_msg(bids, asks, u=1, ts=1000, symbol="BTCUSDT", depth=50):
    return {
        "topic": f"orderbook.{depth}.{symbol}",
        "type": "snapshot",
        "ts": ts,
        "data": {"s": symbol, "b": bids, "a": asks, "u": u, "seq": u},
    }


def _delta_msg(bids, asks, u, ts=1001, symbol="BTCUSDT", depth=50):
    return {
        "topic": f"orderbook.{depth}.{symbol}",
        "type": "delta",
        "ts": ts,
        "data": {"s": symbol, "b": bids, "a": asks, "u": u, "seq": u},
    }


def test_snapshot_best_and_spread():
    ob = OrderBook()
    ob.apply(_snapshot_msg(
        bids=[["100.0", "2.0"], ["99.5", "1.0"]],
        asks=[["100.5", "1.5"], ["101.0", "1.0"]],
    ))
    assert ob.ready is True
    assert ob.best_bid().price == 100.0
    assert ob.best_ask().price == 100.5
    assert ob.mid_price() == 100.25
    assert ob.spread() == 0.5
    # spread_bps = 0.5 / 100.25 * 10000
    assert round(ob.spread_bps(), 2) == round(0.5 / 100.25 * 10_000, 2)


def test_obi_sign():
    ob = OrderBook()
    # Бидов по объёму больше -> OBI положителен.
    ob.apply(_snapshot_msg(
        bids=[["100.0", "5.0"], ["99.5", "5.0"]],
        asks=[["100.5", "1.0"], ["101.0", "1.0"]],
    ))
    obi = ob.obi(depth=2)
    assert obi is not None and obi > 0
    # (10 - 2) / (10 + 2)
    assert round(obi, 4) == round((10 - 2) / (10 + 2), 4)


def test_delta_updates_and_level_removal():
    ob = OrderBook()
    ob.apply(_snapshot_msg(
        bids=[["100.0", "2.0"], ["99.5", "1.0"]],
        asks=[["100.5", "1.5"]],
        u=1,
    ))
    # Дельта: изменить объём лучшего бида и УДАЛИТЬ уровень 99.5 (qty 0).
    changed = ob.apply(_delta_msg(
        bids=[["100.0", "3.0"], ["99.5", "0"]],
        asks=[],
        u=2,
    ))
    assert changed is True
    assert ob.bids[100.0] == 3.0
    assert 99.5 not in ob.bids


def test_stale_delta_rejected():
    ob = OrderBook()
    ob.apply(_snapshot_msg(bids=[["100.0", "1.0"]], asks=[["101.0", "1.0"]], u=10))
    # Дельта со старым/равным updateId должна быть отвергнута.
    changed = ob.apply(_delta_msg(bids=[["100.0", "9.0"]], asks=[], u=10))
    assert changed is False
    assert ob.desync_count == 1
    assert ob.bids[100.0] == 1.0  # не изменилось


def test_delta_before_snapshot_ignored():
    ob = OrderBook()
    changed = ob.apply(_delta_msg(bids=[["100.0", "1.0"]], asks=[], u=1))
    assert changed is False
    assert ob.ready is False


def test_snapshot_resets_book():
    ob = OrderBook()
    ob.apply(_snapshot_msg(bids=[["100.0", "1.0"]], asks=[["101.0", "1.0"]], u=1))
    # Новый снапшот с другими уровнями — книга заменяется целиком.
    ob.apply(_snapshot_msg(bids=[["200.0", "2.0"]], asks=[["201.0", "2.0"]], u=99))
    assert ob.best_bid().price == 200.0
    assert 100.0 not in ob.bids


def test_wall_detection():
    ob = OrderBook()
    # Один уровень с объёмом много больше остальных -> стена.
    bids = [["100.0", "1.0"], ["99.5", "1.0"], ["99.0", "20.0"], ["98.5", "1.0"]]
    ob.apply(_snapshot_msg(bids=bids, asks=[["100.5", "1.0"]], u=1))
    walls = ob.walls(depth=4, ratio=3.0)
    assert len(walls) == 1
    assert walls[0].side == "bid"
    assert walls[0].price == 99.0
    assert walls[0].qty == 20.0


def test_replay_stream_positive_obi():
    ob, _ = replay(synthetic_public_stream(n_updates=20))
    snap = ob.snapshot()
    assert snap["ready"] is True
    assert snap["best_bid"] < snap["best_ask"]      # книга не пересечена
    assert snap["obi"] > 0                            # синтетика: перевес бидов
    assert any(w[0] == "bid" for w in snap["walls"])  # заложенная стена найдена
    assert snap["desync_count"] == 0
