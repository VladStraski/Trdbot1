"""Indicator Engine — расчёт индикаторов, осцилляторов и свечных паттернов.

Собственная реализация на pandas/numpy: `pandas-ta` в актуальных версиях
требует Python 3.11+ несовместим (нужен 3.12+), поэтому внешняя TA-библиотека
не используется. Формулы — стандартные (Уайлдер для RSI/ATR/ADX).

Все индикаторы КАУЗАЛЬНЫ: значение в строке i зависит только от строк ≤ i.
Функции swing_high/swing_low требуют подтверждения `right` барами справа и
возвращают только ПОДТВЕРЖДЁННЫЕ экстремумы — без look-ahead в торговых
решениях (подтверждённый свинг на баре i становится известен на баре i+right).

Все функции работают с нормализованным OHLCV-DataFrame из data/klines.py
(колонки: open, high, low, close, volume, ...).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
#  Базовые сглаживания
# --------------------------------------------------------------------------- #

def ema(series: pd.Series, length: int) -> pd.Series:
    """Экспоненциальная скользящая средняя."""
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    """Простая скользящая средняя."""
    return series.rolling(length).mean()


def rma(series: pd.Series, length: int) -> pd.Series:
    """Сглаживание Уайлдера (RMA / SMMA): ewm с alpha = 1/length."""
    return series.ewm(alpha=1.0 / length, adjust=False).mean()


# --------------------------------------------------------------------------- #
#  Волатильность
# --------------------------------------------------------------------------- #

def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range = max(H-L, |H-prevClose|, |L-prevClose|)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range (сглаживание Уайлдера)."""
    return rma(true_range(df), length)


def bollinger(close: pd.Series, length: int = 20, mult: float = 2.0) -> pd.DataFrame:
    """Полосы Боллинджера: середина (SMA), верх/низ, ширина (bandwidth)."""
    mid = sma(close, length)
    # Стандартное отклонение популяции (ddof=0) — как в большинстве TA-пакетов.
    std = close.rolling(length).std(ddof=0)
    upper = mid + mult * std
    lower = mid - mult * std
    bandwidth = (upper - lower) / mid
    return pd.DataFrame({
        "bb_mid": mid, "bb_upper": upper, "bb_lower": lower, "bb_bandwidth": bandwidth,
    })


# --------------------------------------------------------------------------- #
#  Осцилляторы
# --------------------------------------------------------------------------- #

def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """RSI по Уайлдеру, диапазон 0..100."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss
    result = 100.0 - (100.0 / (1.0 + rs))
    # Когда потерь нет (avg_loss == 0) — RSI = 100; когда нет роста — RSI = 0.
    result = result.where(avg_loss != 0, 100.0)
    result = result.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return result


def stoch_rsi(close: pd.Series, length: int = 14, k: int = 3, d: int = 3) -> pd.DataFrame:
    """Stochastic RSI: %K и %D в диапазоне 0..100."""
    r = rsi(close, length)
    lowest = r.rolling(length).min()
    highest = r.rolling(length).max()
    denom = (highest - lowest)
    stoch = (r - lowest) / denom
    stoch = stoch.where(denom != 0, 0.0) * 100.0
    k_line = stoch.rolling(k).mean()
    d_line = k_line.rolling(d).mean()
    return pd.DataFrame({"stochrsi_k": k_line, "stochrsi_d": d_line})


def macd(close: pd.Series, fast: int = 12, slow: int = 26,
         signal: int = 9) -> pd.DataFrame:
    """MACD: линия, сигнальная, гистограмма."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return pd.DataFrame({
        "macd": macd_line, "macd_signal": signal_line, "macd_hist": hist,
    })


def adx(df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
    """ADX + directional indicators (+DI / -DI). ADX>20 ~ наличие тренда."""
    high, low = df["high"], df["low"]
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=df.index)
    minus_dm = pd.Series(minus_dm, index=df.index)

    tr = true_range(df)
    atr_ = rma(tr, length)
    plus_di = 100.0 * rma(plus_dm, length) / atr_
    minus_di = 100.0 * rma(minus_dm, length) / atr_

    di_sum = plus_di + minus_di
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    dx = dx.where(di_sum != 0, 0.0)
    adx_ = rma(dx, length)
    return pd.DataFrame({"adx": adx_, "plus_di": plus_di, "minus_di": minus_di})


# --------------------------------------------------------------------------- #
#  Объём / цена
# --------------------------------------------------------------------------- #

def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(df["close"].diff().fillna(0.0))
    return (direction * df["volume"]).cumsum()


def vwap(df: pd.DataFrame, anchor: pd.Series | None = None) -> pd.Series:
    """VWAP. По умолчанию — накопительный от начала буфера.

    anchor — опциональная булева серия-«якорь»: True в точках сброса
    (например, начало торговой сессии/дня). Между якорями VWAP считается
    независимо.
    """
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    pv = typical * df["volume"]
    if anchor is None:
        return pv.cumsum() / df["volume"].cumsum()
    # Группируем по накопительной сумме якорей — каждая группа считается отдельно.
    group = anchor.fillna(False).astype(int).cumsum()
    cum_pv = pv.groupby(group).cumsum()
    cum_vol = df["volume"].groupby(group).cumsum()
    return cum_pv / cum_vol


# --------------------------------------------------------------------------- #
#  Свечные паттерны (булевы серии)
# --------------------------------------------------------------------------- #

def _body(df: pd.DataFrame) -> pd.Series:
    return (df["close"] - df["open"]).abs()


def _range(df: pd.DataFrame) -> pd.Series:
    return (df["high"] - df["low"]).replace(0, np.nan)


def doji(df: pd.DataFrame, body_max_ratio: float = 0.1) -> pd.Series:
    """Doji: тело <= body_max_ratio от полного диапазона свечи."""
    return (_body(df) / _range(df)).fillna(1.0) <= body_max_ratio


def hammer(df: pd.DataFrame, body_max_ratio: float = 0.35,
           wick_min_ratio: float = 2.0) -> pd.Series:
    """Hammer / бычий pin bar: маленькое тело сверху, длинная нижняя тень.

    lower_wick >= wick_min_ratio * body И верхняя тень мала.
    """
    body = _body(df)
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    rng = _range(df)
    small_body = (body / rng).fillna(1.0) <= body_max_ratio
    long_lower = lower_wick >= wick_min_ratio * body.replace(0, np.nan)
    short_upper = upper_wick <= body
    return (small_body & long_lower.fillna(False) & short_upper).fillna(False)


def shooting_star(df: pd.DataFrame, body_max_ratio: float = 0.35,
                  wick_min_ratio: float = 2.0) -> pd.Series:
    """Shooting star / медвежий pin bar: длинная верхняя тень, тело снизу."""
    body = _body(df)
    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]
    rng = _range(df)
    small_body = (body / rng).fillna(1.0) <= body_max_ratio
    long_upper = upper_wick >= wick_min_ratio * body.replace(0, np.nan)
    short_lower = lower_wick <= body
    return (small_body & long_upper.fillna(False) & short_lower).fillna(False)


def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Бычье поглощение: предыдущая свеча медвежья, текущая бычья и её тело
    полностью перекрывает тело предыдущей."""
    o, c = df["open"], df["close"]
    prev_o, prev_c = o.shift(1), c.shift(1)
    prev_bear = prev_c < prev_o
    curr_bull = c > o
    engulf = (c >= prev_o) & (o <= prev_c)
    return (prev_bear & curr_bull & engulf).fillna(False)


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Медвежье поглощение (зеркально)."""
    o, c = df["open"], df["close"]
    prev_o, prev_c = o.shift(1), c.shift(1)
    prev_bull = prev_c > prev_o
    curr_bear = c < o
    engulf = (o >= prev_c) & (c <= prev_o)
    return (prev_bull & curr_bear & engulf).fillna(False)


# --------------------------------------------------------------------------- #
#  Swing highs / lows (локальные экстремумы для SL/TP)
# --------------------------------------------------------------------------- #

def swing_high(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.Series:
    """Подтверждённый swing high: high[i] строго максимален в окне [i-left, i+right].

    Возвращает булеву серию; True на баре i означает, что i — swing high,
    подтверждённый `right` барами справа (то есть становится известен на i+right).
    Для торговли использовать сдвиг: swing на i применим начиная с бара i+right.
    """
    return _swing(df["high"], left, right, is_high=True)


def swing_low(df: pd.DataFrame, left: int = 2, right: int = 2) -> pd.Series:
    """Подтверждённый swing low (зеркально swing_high)."""
    return _swing(df["low"], left, right, is_high=False)


def _swing(series: pd.Series, left: int, right: int, is_high: bool) -> pd.Series:
    n = len(series)
    vals = series.to_numpy()
    out = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window = vals[i - left: i + right + 1]
        center = vals[i]
        if is_high:
            if center == window.max() and (window[:left].max() < center) \
                    and (window[left + 1:].max() < center):
                out[i] = True
        else:
            if center == window.min() and (window[:left].min() > center) \
                    and (window[left + 1:].min() > center):
                out[i] = True
    return pd.Series(out, index=series.index)


def last_confirmed_swing(df: pd.DataFrame, at_index: int, kind: str,
                         left: int = 2, right: int = 2) -> float | None:
    """Значение последнего swing (`kind`='high'|'low'), ПОДТВЕРЖДЁННОГО к бару
    at_index (т.е. свинг на баре j с j+right <= at_index). Для расчёта SL/TP.
    """
    if kind == "high":
        flags = swing_high(df, left, right)
        price = df["high"]
    elif kind == "low":
        flags = swing_low(df, left, right)
        price = df["low"]
    else:
        raise ValueError("kind должен быть 'high' или 'low'")

    for j in range(at_index - right, -1, -1):
        if bool(flags.iloc[j]):
            return float(price.iloc[j])
    return None


# --------------------------------------------------------------------------- #
#  Движок: собрать всё в один enriched-DataFrame
# --------------------------------------------------------------------------- #

@dataclass
class IndicatorConfig:
    ema_fast: int = 50
    ema_slow: int = 200
    adx_length: int = 14
    rsi_length: int = 14
    use_stoch_rsi: bool = False          # RSI или Stoch RSI (не оба — ТЗ)
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_length: int = 14
    bb_length: int = 20
    bb_mult: float = 2.0


class IndicatorEngine:
    """Обёртка: считает набор индикаторов и добавляет их колонками к DataFrame."""

    def __init__(self, config: IndicatorConfig | None = None) -> None:
        self.config = config or IndicatorConfig()

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """Вернуть КОПИЮ df с добавленными индикаторными колонками."""
        c = self.config
        out = df.copy()
        close = out["close"]

        out[f"ema_{c.ema_fast}"] = ema(close, c.ema_fast)
        out[f"ema_{c.ema_slow}"] = ema(close, c.ema_slow)

        out = out.join(adx(out, c.adx_length))

        if c.use_stoch_rsi:
            out = out.join(stoch_rsi(close, c.rsi_length))
        else:
            out["rsi"] = rsi(close, c.rsi_length)

        out = out.join(macd(close, c.macd_fast, c.macd_slow, c.macd_signal))
        out["atr"] = atr(out, c.atr_length)
        out = out.join(bollinger(close, c.bb_length, c.bb_mult))
        out["obv"] = obv(out)
        out["vwap"] = vwap(out)

        # Свечные паттерны.
        out["doji"] = doji(out)
        out["hammer"] = hammer(out)
        out["shooting_star"] = shooting_star(out)
        out["bull_engulfing"] = bullish_engulfing(out)
        out["bear_engulfing"] = bearish_engulfing(out)
        return out
