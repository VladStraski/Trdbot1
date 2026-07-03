"""Тесты multi-TF агрегатора — в первую очередь защита от look-ahead."""

import pandas as pd
import pytest

from trading_bot.config.settings import TF_MS
from trading_bot.data.multi_tf_aggregator import MultiTFAggregator


def _make_df(tf: str, n: int, start: int = 0) -> pd.DataFrame:
    step = TF_MS[tf]
    rows = []
    for i in range(n):
        ot = start + i * step
        rows.append({
            "open_time": ot, "open": 100 + i, "high": 101 + i, "low": 99 + i,
            "close": 100.5 + i, "volume": 10, "turnover": 1000,
            "close_time": ot + step, "is_closed": True,
        })
    return pd.DataFrame(rows)


def test_seed_and_latest():
    agg = MultiTFAggregator(["15m", "4h"])
    agg.seed("15m", _make_df("15m", 5))
    assert len(agg.frame("15m")) == 5
    assert agg.latest("15m", 1).iloc[0]["open_time"] == 4 * TF_MS["15m"]


def test_aligned_no_lookahead():
    """Контекстная свеча должна закрыться НЕ ПОЗЖЕ момента решения."""
    agg = MultiTFAggregator(["15m", "4h"])
    agg.seed("4h", _make_df("4h", 10))          # 4h свечи: 0..9
    agg.seed("15m", _make_df("15m", 100))

    # Момент решения = близко к началу: только первая 4h свеча (close=4h) закрыта.
    ref = TF_MS["4h"] + 1          # чуть позже закрытия первой 4h свечи
    ctx = agg.aligned("4h", ref)
    assert ctx is not None
    assert int(ctx["close_time"]) <= ref
    assert int(ctx["open_time"]) == 0          # именно первая (уже закрытая) свеча


def test_aligned_returns_none_when_nothing_closed():
    agg = MultiTFAggregator(["4h"])
    agg.seed("4h", _make_df("4h", 3, start=1_000_000))
    # ref раньше закрытия любой свечи
    assert agg.aligned("4h", 0) is None


def test_aligned_picks_last_closed():
    agg = MultiTFAggregator(["4h"])
    agg.seed("4h", _make_df("4h", 10))
    # ref = точно на границе закрытия 5-й свечи (индекс 4): close_time = 5*step
    ref = 5 * TF_MS["4h"]
    ctx = agg.aligned("4h", ref)
    # Свеча с close_time == ref включается (<=), это свеча index 4.
    assert int(ctx["open_time"]) == 4 * TF_MS["4h"]
    # Следующая свеча (index 5) закрывается в 6*step > ref — не должна выбираться.
    assert int(ctx["close_time"]) == ref


def test_update_closed_dedup_and_append():
    agg = MultiTFAggregator(["15m"])
    agg.seed("15m", _make_df("15m", 3))
    last_ot = agg.last_closed_time("15m")

    # Дубликат прошлого времени — не добавляется.
    assert agg.update_closed("15m", {"open_time": last_ot, "close": 1}) is False
    assert len(agg.frame("15m")) == 3

    # Новая свеча — добавляется.
    new_ot = last_ot + TF_MS["15m"]
    assert agg.update_closed("15m", {
        "open_time": new_ot, "open": 1, "high": 2, "low": 0,
        "close": 1.5, "volume": 5, "turnover": 50,
    }) is True
    assert len(agg.frame("15m")) == 4
    assert agg.last_closed_time("15m") == new_ot


def test_unknown_tf_rejected():
    with pytest.raises(ValueError):
        MultiTFAggregator(["7h"])
