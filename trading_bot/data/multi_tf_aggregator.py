"""Мультитаймфреймовый агрегатор.

Держит синхронизированные буферы ЗАКРЫТЫХ свечей на нескольких таймфреймах и
пересчитывает производные (индикаторы) только по закрытию свечи соответствующего
TF (раздел 4.2 ТЗ).

Критично (раздел 5 ТЗ): при выравнивании таймфреймов для точки времени на младшем
TF берётся ПОСЛЕДНЯЯ ПОЛНОСТЬЮ ЗАКРЫТАЯ свеча старшего TF на этот момент — иначе
look-ahead bias. Это гарантирует `aligned()`.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from ..config.settings import TF_MS
from ..logger import get_logger

log = get_logger("data.multi_tf_aggregator")


class MultiTFAggregator:
    """Буферы закрытых свечей по TF + корректное выравнивание без look-ahead."""

    def __init__(self, timeframes: list[str], max_buffer: int = 1500) -> None:
        # Проверяем, что для каждого TF известна длительность.
        for tf in timeframes:
            if tf not in TF_MS:
                raise ValueError(f"Неизвестный TF {tf!r}; допустимые: {list(TF_MS)}")
        self.timeframes = list(timeframes)
        self.max_buffer = max_buffer
        self._buffers: dict[str, pd.DataFrame] = {tf: _empty() for tf in timeframes}

    # ------------------------------------------------------------------ #
    #  Наполнение
    # ------------------------------------------------------------------ #
    def seed(self, tf: str, df: pd.DataFrame) -> None:
        """Инициализировать буфер TF историей (только закрытые свечи)."""
        self._require_tf(tf)
        clean = _only_closed(df).sort_values("open_time").reset_index(drop=True)
        self._buffers[tf] = clean.iloc[-self.max_buffer:].reset_index(drop=True)
        log.info("seed(%s): %d закрытых свечей", tf, len(self._buffers[tf]))

    def update_closed(self, tf: str, candle: dict) -> bool:
        """Добавить одну ТОЛЬКО ЧТО ЗАКРЫВШУЮСЯ свечу.

        Возвращает True, если свеча новая (буфер изменился) — сигнал к
        пересчёту индикаторов на этом TF.
        """
        self._require_tf(tf)
        buf = self._buffers[tf]
        open_time = int(candle["open_time"])

        if not buf.empty and open_time <= int(buf["open_time"].iloc[-1]):
            return False  # дубликат или свеча из прошлого — игнорируем

        row = {c: candle.get(c) for c in buf.columns}
        row["open_time"] = open_time
        if "close_time" not in candle:
            row["close_time"] = open_time + TF_MS[tf]
        row["is_closed"] = True

        new_buf = pd.concat([buf, pd.DataFrame([row])], ignore_index=True)
        self._buffers[tf] = new_buf.iloc[-self.max_buffer:].reset_index(drop=True)
        return True

    # ------------------------------------------------------------------ #
    #  Доступ
    # ------------------------------------------------------------------ #
    def frame(self, tf: str) -> pd.DataFrame:
        """Полный буфер закрытых свечей TF (копия)."""
        self._require_tf(tf)
        return self._buffers[tf].copy()

    def latest(self, tf: str, n: int = 1) -> pd.DataFrame:
        """Последние n закрытых свечей TF."""
        self._require_tf(tf)
        return self._buffers[tf].iloc[-n:].reset_index(drop=True)

    def last_closed_time(self, tf: str) -> Optional[int]:
        """open_time последней закрытой свечи TF (или None, если буфер пуст)."""
        self._require_tf(tf)
        buf = self._buffers[tf]
        return int(buf["open_time"].iloc[-1]) if not buf.empty else None

    def aligned(self, higher_tf: str, ref_time_ms: int) -> Optional[pd.Series]:
        """Последняя ПОЛНОСТЬЮ ЗАКРЫТАЯ свеча `higher_tf` на момент `ref_time_ms`.

        Защита от look-ahead: возвращается свеча, чья граница закрытия
        (close_time) <= ref_time_ms. Свеча, которая на момент ref_time_ms ещё
        формируется, НЕ возвращается.

        ref_time_ms — обычно close_time свечи младшего TF (момент, когда мы
        принимаем решение). Возвращает pandas.Series или None.
        """
        self._require_tf(higher_tf)
        buf = self._buffers[higher_tf]
        if buf.empty:
            return None
        eligible = buf[buf["close_time"] <= ref_time_ms]
        if eligible.empty:
            return None
        return eligible.iloc[-1]

    def aligned_frame(self, higher_tf: str, ref_time_ms: int) -> pd.DataFrame:
        """Все закрытые свечи `higher_tf` вплоть до ref_time_ms (для индикаторов).

        То же правило отсечения, что и в aligned(), но возвращает весь префикс —
        нужен, чтобы посчитать индикатор (EMA/ADX/…) на корректной истории без
        заглядывания в будущее.
        """
        self._require_tf(higher_tf)
        buf = self._buffers[higher_tf]
        return buf[buf["close_time"] <= ref_time_ms].reset_index(drop=True)

    # ------------------------------------------------------------------ #
    #  Служебное
    # ------------------------------------------------------------------ #
    def _require_tf(self, tf: str) -> None:
        if tf not in self._buffers:
            raise KeyError(f"TF {tf!r} не зарегистрирован; известны {self.timeframes}")


def _empty() -> pd.DataFrame:
    cols = ["open_time", "open", "high", "low", "close",
            "volume", "turnover", "close_time", "is_closed"]
    return pd.DataFrame(columns=cols)


def _only_closed(df: pd.DataFrame) -> pd.DataFrame:
    if "is_closed" in df.columns:
        return df[df["is_closed"]].copy()
    return df.copy()
