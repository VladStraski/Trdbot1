"""Тесты Indicator Engine: свойства индикаторов, формулы, паттерны, свинги."""

import numpy as np
import pandas as pd

from trading_bot.indicators import engine as ind
from trading_bot.indicators.engine import IndicatorConfig, IndicatorEngine


def _df(o, h, l, c, v=None):
    n = len(c)
    return pd.DataFrame({
        "open": o, "high": h, "low": l, "close": c,
        "volume": v if v is not None else [1.0] * n,
    })


# --- сглаживания ---
def test_ema_constant():
    s = pd.Series([5.0] * 10)
    assert np.allclose(ind.ema(s, 3), 5.0)


def test_sma_basic():
    s = pd.Series([1.0, 2, 3, 4])
    assert ind.sma(s, 2).iloc[-1] == 3.5


# --- RSI ---
def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1.0, 30.0))
    down = pd.Series(np.arange(30.0, 1.0, -1.0))
    assert (ind.rsi(up, 14).dropna() <= 100).all()
    assert (ind.rsi(up, 14).dropna() >= 0).all()
    # чисто растущий ряд -> RSI = 100; чисто падающий -> 0
    assert ind.rsi(up, 2).iloc[-1] == 100.0
    assert ind.rsi(down, 2).iloc[-1] == 0.0


# --- ATR / TR ---
def test_atr_positive():
    df = _df([10, 11, 12], [11, 12, 13], [9, 10, 11], [10.5, 11.5, 12.5])
    a = ind.atr(df, 2)
    assert (a.dropna() > 0).all()


# --- MACD ---
def test_macd_hist_identity():
    close = pd.Series(np.linspace(100, 120, 60))
    m = ind.macd(close)
    assert np.allclose(m["macd_hist"], m["macd"] - m["macd_signal"])


# --- ADX ---
def test_adx_uptrend_di():
    n = 60
    close = pd.Series(np.linspace(100, 160, n))
    df = _df(close - 0.5, close + 1.0, close - 1.0, close)
    res = ind.adx(df, 14)
    # В устойчивом аптренде +DI должен превышать -DI на хвосте.
    assert res["plus_di"].iloc[-1] > res["minus_di"].iloc[-1]
    assert (res["adx"].dropna().between(0, 100)).all()


# --- Bollinger ---
def test_bollinger_ordering():
    close = pd.Series(np.random.default_rng(0).normal(100, 2, 50))
    bb = ind.bollinger(close, 20, 2.0)
    tail = bb.dropna()
    assert (tail["bb_upper"] >= tail["bb_mid"]).all()
    assert (tail["bb_mid"] >= tail["bb_lower"]).all()


# --- OBV ---
def test_obv_direction():
    df = _df([1, 1, 1, 1], [1, 1, 1, 1], [1, 1, 1, 1],
             [10, 11, 10, 12], v=[5, 5, 5, 5])
    o = ind.obv(df)
    # шаги: +vol, -vol, +vol => 0, +5, 0, +5
    assert list(o) == [0.0, 5.0, 0.0, 5.0]


# --- VWAP ---
def test_vwap_single_bar_equals_typical():
    df = _df([10], [12], [8], [10], v=[100])
    typical = (12 + 8 + 10) / 3.0
    assert ind.vwap(df).iloc[0] == typical


# --- свечные паттерны ---
def test_bullish_engulfing_detected():
    df = _df(o=[10, 7], h=[10.5, 11.5], l=[7.5, 6.5], c=[8, 11])
    res = ind.bullish_engulfing(df)
    assert list(res) == [False, True]


def test_hammer_detected():
    df = _df(o=[10.0], h=[10.3], l=[9.0], c=[10.2])
    assert bool(ind.hammer(df).iloc[0]) is True


def test_doji_detected():
    df = _df(o=[10.0], h=[10.5], l=[9.5], c=[10.01])
    assert bool(ind.doji(df).iloc[0]) is True


# --- свинги / отсутствие look-ahead ---
def test_swing_high_low():
    highs = [1, 2, 3, 2, 1]
    lows = [3, 2, 1, 2, 3]
    df = _df(o=highs, h=highs, l=lows, c=highs)
    sh = ind.swing_high(df, left=1, right=1)
    sl = ind.swing_low(df, left=1, right=1)
    assert list(sh) == [False, False, True, False, False]
    assert list(sl) == [False, False, True, False, False]


def test_last_confirmed_swing_no_lookahead():
    highs = [1, 2, 3, 2, 1]
    lows = highs
    df = _df(o=highs, h=highs, l=lows, c=highs)
    # свинг на j=2 подтверждается right=1 баром -> известен с index>=3
    assert ind.last_confirmed_swing(df, at_index=3, kind="high",
                                    left=1, right=1) == 3.0
    # до подтверждения (index=2) свинг ещё не виден -> None
    assert ind.last_confirmed_swing(df, at_index=2, kind="high",
                                    left=1, right=1) is None


# --- движок целиком ---
def test_engine_compute_adds_columns():
    n = 250
    rng = np.random.default_rng(1)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)))
    df = _df(close, close + 1, close - 1, close, v=rng.uniform(1, 10, n))
    out = IndicatorEngine(IndicatorConfig()).compute(df)
    for col in ["ema_50", "ema_200", "adx", "rsi", "macd", "atr",
                "bb_upper", "obv", "vwap", "bull_engulfing"]:
        assert col in out.columns
    # исходные строки не потеряны
    assert len(out) == n
