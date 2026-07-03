"""Оркестратор стратегии (Этап 4 ТЗ).

Связывает confluence-скоринг с данными. Две части:
- `evaluate(features)` — чистая оценка (делегирует `ConfluenceScorer`), stdlib;
- `extract_features(...)` — АДАПТЕР: собирает `FeatureSnapshot` из выровненных
  буферов `MultiTFAggregator` и индикаторов `IndicatorEngine`. Единственное место
  с зависимостью от pandas — импортируется лениво, чтобы модуль грузился и в
  окружении без pandas (тогда доступна только чистая часть скоринга).

Защита от look-ahead: признаки берутся из `agg.aligned_frame(tf, ref_time)` —
только бары, ЗАКРЫТЫЕ к моменту решения ref_time (раздел 5 ТЗ).
"""

from __future__ import annotations

from typing import Optional

from ..logger import get_logger
from .confluence_scorer import ConfluenceScorer
from .model import FeatureSnapshot, ScoreConfig, Signal

log = get_logger("strategy.base")


class BaseStrategy:
    """Изолированная от биржи стратегия: данные -> сигнал (раздел 4.6 ТЗ)."""

    def __init__(self, config: ScoreConfig | None = None) -> None:
        self.config = config or ScoreConfig()
        self.scorer = ConfluenceScorer(self.config)

    # ------------------------------------------------------------------ #
    #  Чистая часть — тестируется офлайн без pandas
    # ------------------------------------------------------------------ #
    def evaluate(self, features: FeatureSnapshot) -> Signal:
        """Оценить готовый набор признаков и вернуть сигнал (+ лог решения)."""
        signal = self.scorer.evaluate(features)
        log.info("Решение: dir=%s score=%d entered=%s | %s",
                 signal.direction, signal.score, signal.entered, signal.reason)
        return signal

    # ------------------------------------------------------------------ #
    #  Адаптер к данным — pandas импортируется лениво
    # ------------------------------------------------------------------ #
    def extract_features(
        self,
        aggregator,
        engine,
        ref_time_ms: int,
        tf_context: str,
        tf_signal: str,
        tf_trigger: str,
        obi: Optional[float] = None,
        cvd_delta: Optional[float] = None,
        funding_rate: Optional[float] = None,
        volume_window: int = 20,
    ) -> Optional[FeatureSnapshot]:
        """Собрать `FeatureSnapshot` из агрегатора на момент ref_time_ms.

        Возвращает None, если истории недостаточно для расчёта индикаторов.
        obi/cvd_delta/funding_rate подставляются из Order Book Module и
        Market Context (могут быть None — тогда соответствующие очки не начисляются).
        """
        import pandas as pd  # ленивый импорт: адаптер нужен только в live/бэктесте

        cfg = self.config

        ctx = engine.compute(aggregator.aligned_frame(tf_context, ref_time_ms))
        sig = engine.compute(aggregator.aligned_frame(tf_signal, ref_time_ms))
        trg = engine.compute(aggregator.aligned_frame(tf_trigger, ref_time_ms))
        if ctx.empty or sig.empty or trg.empty:
            return None

        ctx_last = ctx.iloc[-1]
        sig_last = sig.iloc[-1]
        sig_prev = sig.iloc[-2] if len(sig) >= 2 else sig_last
        trg_last = trg.iloc[-1]

        # Осциллятор: RSI или Stoch RSI %K (в зависимости от конфигурации движка).
        if "stochrsi_k" in sig.columns:
            osc, osc_prev = sig_last.get("stochrsi_k"), sig_prev.get("stochrsi_k")
        else:
            osc, osc_prev = sig_last.get("rsi"), sig_prev.get("rsi")

        # Подтверждённый swing у триггер-бара (без look-ahead: последний бар,
        # который уже мог быть подтверждён `right` барами справа).
        from ..indicators.engine import (last_confirmed_swing, swing_high,
                                          swing_low)
        sw_low = swing_low(trg)
        sw_high = swing_high(trg)
        at_swing_low = bool(sw_low.iloc[-3:].any()) if len(sw_low) >= 3 else False
        at_swing_high = bool(sw_high.iloc[-3:].any()) if len(sw_high) >= 3 else False
        last_idx = len(trg) - 1
        swing_low_price = last_confirmed_swing(trg, last_idx, "low")
        swing_high_price = last_confirmed_swing(trg, last_idx, "high")

        vol_avg = float(trg["volume"].tail(volume_window).mean())

        def _f(row, key, default=0.0) -> float:
            val = row.get(key)
            return float(val) if val is not None and not pd.isna(val) else default

        return FeatureSnapshot(
            price=_f(trg_last, "close"),
            ts=int(trg_last.get("close_time") or 0),
            ema_fast=_f(ctx_last, f"ema_{engine.config.ema_fast}"),
            ema_slow=_f(ctx_last, f"ema_{engine.config.ema_slow}"),
            adx=_f(ctx_last, "adx"),
            osc=None if osc is None or pd.isna(osc) else float(osc),
            osc_prev=None if osc_prev is None or pd.isna(osc_prev) else float(osc_prev),
            macd_hist=_f(sig_last, "macd_hist"),
            macd_hist_prev=_f(sig_prev, "macd_hist"),
            bull_pattern=bool(trg_last.get("bull_engulfing") or trg_last.get("hammer")),
            bear_pattern=bool(trg_last.get("bear_engulfing") or trg_last.get("shooting_star")),
            at_swing_low=at_swing_low,
            at_swing_high=at_swing_high,
            volume=_f(trg_last, "volume"),
            volume_avg=vol_avg,
            atr=_f(trg_last, "atr"),
            swing_low_price=swing_low_price,
            swing_high_price=swing_high_price,
            obi=obi,
            cvd_delta=cvd_delta,
            funding_rate=funding_rate,
        )
