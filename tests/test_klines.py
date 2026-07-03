"""Тесты нормализации klines (сортировка, дедуп, отсечение незакрытых)."""

import pandas as pd

from trading_bot.config.settings import Settings, TF_MS
from trading_bot.data.klines import _raw_to_df, fetch_klines


def _settings() -> Settings:
    return Settings(env="demo", api_key="", api_secret="", symbol="BTCUSDT")


class _FakeClient:
    """Минимальный дублёр BybitClient для fetch_klines (без сети)."""

    def __init__(self, raw_newest_first):
        self.settings = _settings()
        self._raw = raw_newest_first
        self.calls = 0

    def get_kline(self, interval, limit, start=None, end=None,
                  symbol=None, category=None):
        self.calls += 1
        # Отдаём один батч и «конец истории» на втором вызове.
        return self._raw if self.calls == 1 else []


def _raw_candle(open_time, close=100):
    # Формат Bybit: [start, open, high, low, close, volume, turnover]
    return [str(open_time), "100", "101", "99", str(close), "10", "1000"]


def test_raw_to_df_sorts_ascending():
    step = TF_MS["15m"]
    # Bybit отдаёт от новых к старым.
    raw = [_raw_candle(3 * step), _raw_candle(2 * step),
           _raw_candle(1 * step), _raw_candle(0)]
    df = _raw_to_df(raw)
    assert list(df["open_time"]) == [0, step, 2 * step, 3 * step]
    assert df["open"].dtype.kind in ("i", "f")  # числа, не строки


def test_raw_to_df_empty():
    df = _raw_to_df([])
    assert df.empty
    assert list(df.columns)[:2] == ["open_time", "open"]


def test_fetch_klines_marks_unclosed_and_drops_it():
    step = TF_MS["15m"]
    # 4 свечи: последняя ещё формируется на момент now.
    raw = [_raw_candle(3 * step), _raw_candle(2 * step),
           _raw_candle(1 * step), _raw_candle(0)]
    client = _FakeClient(raw)
    # now — внутри 4-й свечи (open=3*step, close=4*step) => она незакрыта.
    now = 3 * step + 1
    df = fetch_klines(client, "15m", limit=10, only_closed=True, now_ms=now)
    assert len(df) == 3                       # незакрытая отброшена
    assert df["is_closed"].all()
    assert int(df["open_time"].iloc[-1]) == 2 * step
