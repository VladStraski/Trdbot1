"""Тесты потока сделок и CVD (Этап 3). Только stdlib — без сети/pandas/pybit."""

from trading_bot.data.trades_stream import TradesStream
from trading_bot.data.stream_replay import replay, synthetic_public_stream


def _trade_msg(trades, symbol="BTCUSDT", ts=1000):
    return {"topic": f"publicTrade.{symbol}", "type": "snapshot", "ts": ts,
            "data": trades}


def test_cvd_accumulation():
    tr = TradesStream()
    tr.apply(_trade_msg([
        {"T": 1000, "s": "BTCUSDT", "S": "Buy", "v": "1.0", "p": "100"},
        {"T": 1001, "s": "BTCUSDT", "S": "Sell", "v": "0.4", "p": "100"},
        {"T": 1002, "s": "BTCUSDT", "S": "Buy", "v": "0.1", "p": "100"},
    ]))
    assert tr.buy_volume == 1.1
    assert tr.sell_volume == 0.4
    assert round(tr.cvd, 4) == round(1.1 - 0.4, 4)
    assert tr.trade_count == 3
    assert tr.last_price == 100.0
    assert tr.last_ts == 1002


def test_imbalance_ratio():
    tr = TradesStream()
    tr.apply(_trade_msg([
        {"T": 1, "s": "BTCUSDT", "S": "Buy", "v": "3.0", "p": "100"},
        {"T": 2, "s": "BTCUSDT", "S": "Sell", "v": "1.0", "p": "100"},
    ]))
    # (3 - 1) / (3 + 1)
    assert tr.imbalance_ratio() == 0.5


def test_cvd_delta_window():
    tr = TradesStream()
    # Старая продажа за пределами окна + свежие покупки внутри окна.
    tr.apply(_trade_msg([{"T": 0, "s": "B", "S": "Sell", "v": "5.0", "p": "100"}]))
    tr.apply(_trade_msg([
        {"T": 10_000, "s": "B", "S": "Buy", "v": "1.0", "p": "100"},
        {"T": 10_500, "s": "B", "S": "Buy", "v": "0.5", "p": "100"},
    ]))
    # last_ts = 10_500; окно 2000мс охватывает только две покупки.
    assert tr.cvd_delta(window_ms=2000) == 1.5
    # Полный CVD учитывает и старую продажу: 1.5 - 5.0
    assert round(tr.cvd, 4) == round(1.5 - 5.0, 4)


def test_unknown_side_skipped():
    tr = TradesStream()
    applied = tr.apply(_trade_msg([
        {"T": 1, "s": "B", "S": "Buy", "v": "1.0", "p": "100"},
        {"T": 2, "s": "B", "S": "???", "v": "1.0", "p": "100"},
    ]))
    assert applied == 1
    assert tr.trade_count == 1
    assert tr.cvd == 1.0


def test_empty_ratio_is_none():
    assert TradesStream().imbalance_ratio() is None


def test_replay_stream_rising_cvd():
    _, tr = replay(synthetic_public_stream(n_updates=20))
    snap = tr.snapshot(window_ms=60_000)
    assert snap["cvd"] > 0                 # синтетика: перевес покупок
    assert snap["cvd_delta"] > 0           # CVD растёт в окне
    assert snap["trade_count"] == 20 * 3
    assert snap["imbalance_ratio"] > 0
