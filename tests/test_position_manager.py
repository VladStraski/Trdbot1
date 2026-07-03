"""Тесты Position Manager и backoff (Этап 8). Чистый stdlib — без сети."""

from trading_bot.execution.position_manager import (PositionManager,
                                                    parse_position)
from trading_bot.reconnect import ReconnectPolicy


def _pos(symbol="BTCUSDT", side="Buy", size="0.5", entry="30000",
         liq="27000", lev="5", ts="100"):
    return {"symbol": symbol, "side": side, "size": size, "entryPrice": entry,
            "liqPrice": liq, "leverage": lev, "updatedTime": ts,
            "unrealisedPnl": "0"}


def test_parse_flat_position_is_none():
    assert parse_position({"symbol": "BTCUSDT", "side": "", "size": "0"}) is None


def test_apply_open_and_close():
    pm = PositionManager()
    pm.apply_events([_pos(size="0.5")])
    st = pm.get("BTCUSDT")
    assert st is not None and st.side == "Buy" and st.size == 0.5
    # Событие с size 0 -> позиция закрыта.
    pm.apply_events([_pos(size="0", side="")])
    assert pm.is_flat("BTCUSDT")


def test_apply_size_update():
    pm = PositionManager()
    pm.apply_events([_pos(size="0.5")])
    pm.apply_events([_pos(size="0.8")])
    assert pm.get("BTCUSDT").size == 0.8


def test_reconcile_detects_size_mismatch():
    pm = PositionManager()
    pm.apply_events([_pos(size="0.5")])
    # Биржа показывает другой размер -> расхождение, внутреннее приводится к бирже.
    diffs = pm.reconcile([_pos(size="0.7")])
    fields = [(d.field, d.internal, d.exchange) for d in diffs]
    assert ("size", 0.5, 0.7) in fields
    assert pm.get("BTCUSDT").size == 0.7          # источник истины — биржа


def test_reconcile_detects_presence_mismatch():
    pm = PositionManager()
    pm.apply_events([_pos(size="0.5")])
    # Биржа плоская -> расхождение presence, внутреннее становится плоским.
    diffs = pm.reconcile([])
    assert any(d.field == "presence" for d in diffs)
    assert pm.is_flat("BTCUSDT")


def test_reconcile_detects_side_mismatch():
    pm = PositionManager()
    pm.apply_events([_pos(side="Buy")])
    diffs = pm.reconcile([_pos(side="Sell")])
    assert any(d.field == "side" for d in diffs)


def test_reconcile_clean_when_matching():
    pm = PositionManager()
    pm.apply_events([_pos(size="0.5")])
    assert pm.reconcile([_pos(size="0.5")]) == []


def test_reconnect_backoff_sequence():
    p = ReconnectPolicy(base_delay=1.0, factor=2.0, max_delay=30.0)
    assert p.delays(6) == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]   # растёт и упирается в потолок
    assert p.should_retry(100) is True                        # без лимита


def test_reconnect_max_attempts():
    p = ReconnectPolicy(max_attempts=3)
    assert p.should_retry(3) is True
    assert p.should_retry(4) is False
