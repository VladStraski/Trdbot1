"""Загрузка исторических свечей (klines) через REST.

Bybit v5 отдаёт klines списком списков, ОТ НОВЫХ К СТАРЫМ:
    [startTime, open, high, low, close, volume, turnover]
где startTime — время ОТКРЫТИЯ свечи в мс.

Здесь всё приводится к нормализованному DataFrame:
  - строки отсортированы ПО ВОЗРАСТАНИЮ времени;
  - индекс — целочисленный порядковый; колонка open_time (мс) сохранена;
  - добавлены close_time и is_closed (закрыта ли свеча на момент запроса).

Отдаётся только полностью закрытые свечи (при пагинации незакрытая
последняя свеча отбрасывается) — это фундамент защиты от look-ahead.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..config.settings import TF_MS
from ..execution.bybit_client import BybitClient
from ..logger import get_logger

log = get_logger("data.klines")

# Bybit ограничивает один запрос 1000 свечами.
_MAX_LIMIT = 1000

OHLCV_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "turnover"]


def _raw_to_df(raw: list[list[str]]) -> pd.DataFrame:
    """Сырой ответ Bybit -> нормализованный DataFrame (сортировка по возрастанию)."""
    if not raw:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    df = pd.DataFrame(raw, columns=OHLCV_COLUMNS)
    # Всё приходит строками — приводим к числам.
    df["open_time"] = df["open_time"].astype("int64")
    for col in ("open", "high", "low", "close", "volume", "turnover"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("open_time").reset_index(drop=True)
    df = df.drop_duplicates(subset="open_time", keep="last").reset_index(drop=True)
    return df


def _add_time_columns(df: pd.DataFrame, tf: str, now_ms: Optional[int]) -> pd.DataFrame:
    """Добавить close_time и is_closed."""
    if df.empty:
        df["close_time"] = pd.Series(dtype="int64")
        df["is_closed"] = pd.Series(dtype="bool")
        return df

    tf_ms = TF_MS.get(tf)
    if tf_ms is None:
        raise ValueError(f"Нет длительности для TF {tf!r} в TF_MS")

    df["close_time"] = df["open_time"] + tf_ms  # эксклюзивная граница закрытия
    if now_ms is not None:
        # Свеча закрыта, если её граница закрытия уже наступила.
        df["is_closed"] = df["close_time"] <= now_ms
    else:
        # Без опорного времени считаем закрытыми все, кроме последней
        # (Bybit отдаёт текущую формирующуюся свечу первой в сыром списке).
        df["is_closed"] = True
        df.loc[df.index[-1], "is_closed"] = False
    return df


def fetch_klines(
    client: BybitClient,
    tf: str,
    limit: int = 200,
    *,
    only_closed: bool = True,
    now_ms: Optional[int] = None,
) -> pd.DataFrame:
    """Загрузить `limit` последних свечей таймфрейма `tf`.

    tf — человекочитаемый ('15m','1h','4h'); конвертируется в интервал Bybit.
    only_closed=True (по умолчанию) отбрасывает незакрытую свечу — для стратегии
    и агрегатора всегда работаем только с закрытыми свечами.
    """
    interval = _resolve_interval(client, tf)
    settings = client.settings

    remaining = limit
    frames: list[pd.DataFrame] = []
    end: Optional[int] = None  # для пагинации вглубь истории

    while remaining > 0:
        batch = min(remaining, _MAX_LIMIT)
        raw = client.get_kline(interval=interval, limit=batch, end=end)
        if not raw:
            break
        df = _raw_to_df(raw)
        frames.append(df)
        remaining -= len(df)
        if len(raw) < batch:
            break  # история закончилась
        # Следующий запрос — строго старше самой ранней полученной свечи.
        earliest_open = int(df["open_time"].iloc[0])
        end = earliest_open - 1

    if not frames:
        log.warning("fetch_klines(%s): пусто", tf)
        return _add_time_columns(pd.DataFrame(columns=OHLCV_COLUMNS), tf, now_ms)

    full = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset="open_time", keep="last")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    full = _add_time_columns(full, tf, now_ms)

    if only_closed:
        full = full[full["is_closed"]].reset_index(drop=True)

    # Возвращаем ровно последние `limit` закрытых свечей.
    if len(full) > limit:
        full = full.iloc[-limit:].reset_index(drop=True)

    log.info(
        "fetch_klines(%s): %d свечей, диапазон [%s .. %s]",
        tf, len(full),
        int(full["open_time"].iloc[0]) if not full.empty else None,
        int(full["open_time"].iloc[-1]) if not full.empty else None,
    )
    return full


def _resolve_interval(client: BybitClient, tf: str) -> str:
    """Человекочитаемый TF -> интервал Bybit через Settings.interval()."""
    return client.settings.interval(tf)
