"""Тесты бэктест-движка (Этап 5). Синтетические Bar+FeatureSnapshot, stdlib."""

from trading_bot.backtest.engine import (Backtester, BacktestConfig, Bar,
                                         CostModel, default_sl_tp)
from trading_bot.strategy.base_strategy import BaseStrategy
from trading_bot.strategy.model import LONG, FeatureSnapshot


def _enter_long(atr=1.0) -> FeatureSnapshot:
    """Признаки, гарантированно проходящие порог входа в лонг."""
    return FeatureSnapshot(
        price=100.0, ts=0, ema_fast=110.0, ema_slow=100.0, adx=30.0,
        osc=35.0, osc_prev=28.0, macd_hist=0.5, macd_hist_prev=0.2,
        bull_pattern=True, at_swing_low=True, volume=150.0, volume_avg=100.0,
        atr=atr, obi=0.3, cvd_delta=1.2, funding_rate=0.0001,
    )


def _flat() -> FeatureSnapshot:
    """Нет тренда — входа не будет."""
    return FeatureSnapshot(price=100.0, ts=0, ema_fast=100.0, ema_slow=100.0,
                           adx=10.0)


def _bt() -> Backtester:
    return Backtester(BaseStrategy(), BacktestConfig(initial_equity=10_000.0))


def test_winning_long_hits_take():
    series = [
        (Bar(ts=0, open=100, high=100.5, low=99.8, close=100.0), _enter_long()),
        # Следующий бар пробивает тейк вверх -> выход в плюс.
        (Bar(ts=1, open=100, high=105.0, low=100.0, close=104.0), _flat()),
        (Bar(ts=2, open=104, high=104, low=104, close=104.0), _flat()),
    ]
    res = _bt().run(series)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.direction == LONG
    assert t.exit_reason == "take"
    assert t.pnl > 0
    assert res.final_equity > res.initial_equity
    assert t.fees > 0                      # издержки учтены


def test_losing_long_hits_stop():
    series = [
        (Bar(ts=0, open=100, high=100.5, low=99.8, close=100.0), _enter_long()),
        (Bar(ts=1, open=100, high=100.1, low=95.0, close=96.0), _flat()),
    ]
    res = _bt().run(series)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "stop"
    assert t.pnl < 0
    assert res.final_equity < res.initial_equity
    assert -1.5 < t.r_multiple < 0        # около -1R (плюс издержки)


def test_no_lookahead_exit_on_entry_bar():
    """Экстремум на баре входа не должен закрыть сделку в тот же бар."""
    series = [
        # На баре входа high=200 (выше любого тейка) — но выход запрещён.
        (Bar(ts=0, open=100, high=200.0, low=100.0, close=100.0), _enter_long()),
        (Bar(ts=1, open=100, high=100.1, low=100.0, close=100.0), _flat()),
    ]
    res = _bt().run(series)
    # Сделка открыта на баре 0, но не закрыта на нём; на баре 1 ни стоп, ни тейк
    # не тронуты -> закрытие по EOD (mark-to-market), reason='eod'.
    assert len(res.trades) == 1
    assert res.trades[0].exit_reason == "eod"


def test_only_one_position_at_a_time():
    """Пока позиция открыта, новый вход не берётся."""
    series = [
        (Bar(ts=0, open=100, high=100.5, low=99.8, close=100.0), _enter_long()),
        (Bar(ts=1, open=100, high=100.6, low=99.9, close=100.2), _enter_long()),
        (Bar(ts=2, open=100, high=105.0, low=100.0, close=104.0), _enter_long()),
    ]
    res = _bt().run(series)
    # Первый вход на баре 0; тейк срабатывает на баре 2; затем на баре 2 после
    # выхода снова открывается позиция и закрывается по EOD.
    assert len(res.trades) == 2
    assert res.trades[0].exit_reason == "take"


def test_summary_metrics():
    series = [
        (Bar(ts=0, open=100, high=100.5, low=99.8, close=100.0), _enter_long()),
        (Bar(ts=1, open=100, high=105.0, low=100.0, close=104.0), _flat()),
        (Bar(ts=2, open=104, high=104, low=104, close=104.0), _enter_long()),
        (Bar(ts=3, open=104, high=104.1, low=99.0, close=99.5), _flat()),
    ]
    res = _bt().run(series)
    s = res.summary()
    assert s["trades"] == 2
    assert s["wins"] == 1 and s["losses"] == 1
    assert s["win_rate"] == 0.5
    assert 0.0 <= s["max_drawdown"] <= 1.0
    assert s["total_fees"] > 0


def test_default_sl_tp_uses_swing_when_closer():
    f = _enter_long(atr=1.0)
    f = FeatureSnapshot(**{**f.__dict__, "swing_low_price": 99.0})
    stop, take = default_sl_tp(LONG, 100.0, f, BacktestConfig())
    # ATR-стоп был бы 100-1.75=98.25; swing 99.0 ближе -> берём min? Нет: стоп
    # не должен быть БЛИЖЕ свинга, т.е. не выше него. min(98.25, 99.0)=98.25.
    assert stop == 98.25
