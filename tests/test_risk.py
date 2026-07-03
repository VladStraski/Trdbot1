"""Тесты Risk Manager (Этап 6). Чистая арифметика — stdlib, без сети."""

from trading_bot.risk.position_sizing import fixed_fractional_qty, risk_amount
from trading_bot.risk.leverage_manager import safe_leverage, liq_safety_ok
from trading_bot.risk.costs import CostConfig, net_reward_risk
from trading_bot.risk.stop_take_manager import (PositionRiskState, initial_stop,
                                                price_at_r, reached_r, take_from_rr)
from trading_bot.risk.risk_manager import RiskManager, RiskParams
from trading_bot.strategy.model import LONG, SHORT


# --- position sizing --------------------------------------------------------
def test_fixed_fractional_basic():
    # риск 1% от 10000 = 100; дистанция стопа 2 -> qty 50
    assert fixed_fractional_qty(10_000, 0.01, 100.0, 98.0) == 50.0


def test_fixed_fractional_qty_step_rounds_down():
    q = fixed_fractional_qty(10_000, 0.01, 100.0, 99.0, qty_step=0.5)  # raw 100
    assert q == 100.0
    q2 = fixed_fractional_qty(10_000, 0.01, 100.0, 98.7, qty_step=0.5)  # raw ~76.9
    assert q2 == 76.5  # округление вниз до шага 0.5


def test_fixed_fractional_zero_distance():
    assert fixed_fractional_qty(10_000, 0.01, 100.0, 100.0) == 0.0


# --- leverage ---------------------------------------------------------------
def test_safe_leverage_capped_by_hardcap():
    # max_by_liq = 100/(3*2)=16.7, desired 10, cap 5 -> 5
    assert safe_leverage(100.0, 2.0, desired_leverage=10, hard_cap=5) == 5


def test_safe_leverage_limited_by_liq():
    # max_by_liq = 100/(3*10)=3.33 -> 3
    assert safe_leverage(100.0, 10.0, desired_leverage=10, hard_cap=5) == 3


def test_liq_safety_ok():
    assert liq_safety_ok(100.0, 2.0, liq_price=90.0) is True      # 10 >= 6
    assert liq_safety_ok(100.0, 2.0, liq_price=95.0) is False     # 5 < 6


# --- stop/take --------------------------------------------------------------
def test_initial_stop_uses_further_swing():
    # ATR-стоп long = 100-1.75=98.25; swing_low 97 дальше -> берём 97
    assert initial_stop(LONG, 100.0, atr=1.0, atr_mult=1.75, swing=97.0) == 97.0
    # swing 99 ближе -> оставляем ATR-стоп 98.25
    assert initial_stop(LONG, 100.0, atr=1.0, atr_mult=1.75, swing=99.0) == 98.25


def test_reached_r_and_price_at_r():
    assert reached_r(LONG, 100.0, 98.0, 104.0) == 2.0
    assert price_at_r(LONG, 100.0, 98.0, 1.0) == 102.0
    assert take_from_rr(SHORT, 100.0, 102.0, 1.5) == 97.0


def test_position_state_breakeven_and_trail():
    st = PositionRiskState(LONG, entry=100.0, stop=98.0, take=104.0)
    st.update(price=101.0, atr=1.0)          # r=0.5 -> без изменений
    assert st.stop == 98.0 and st.breakeven_done is False
    st.update(price=102.0, atr=1.0, atr_mult=1.0)  # r=1.0 -> BE + trail
    assert st.breakeven_done is True
    assert st.stop >= 100.0                   # стоп не ниже входа
    prev = st.stop
    st.update(price=101.5, atr=1.0)          # стоп не ослабляется
    assert st.stop == prev


# --- costs ------------------------------------------------------------------
def test_net_reward_risk_below_gross():
    cfg = CostConfig()
    # gross RR = 2.0; после издержек должно быть меньше
    net = net_reward_risk(100.0, 98.0, 104.0, cfg, hold_hours=8.0)
    assert 0 < net < 2.0


# --- risk manager -----------------------------------------------------------
def test_decide_approves_and_meets_net_rr():
    rm = RiskManager(RiskParams(min_rr=1.5))
    d = rm.decide(LONG, entry=100.0, atr=1.0, equity=10_000.0)
    assert d.approved is True
    assert d.qty > 0 and d.leverage >= 1
    assert d.net_rr >= 1.5 - 1e-6            # тейк выставлен с учётом издержек
    assert d.risk_amount > 0


def test_decide_rejects_zero_equity():
    rm = RiskManager()
    d = rm.decide(LONG, entry=100.0, atr=1.0, equity=0.0)
    assert d.approved is False
    assert "позиции" in d.reason or "equity" in d.reason


def test_backtester_uses_risk_manager_adapters():
    # Интеграция: движок Этапа 5 работает через адаптеры Risk Manager.
    from trading_bot.backtest.engine import Backtester, BacktestConfig, Bar
    from trading_bot.strategy.base_strategy import BaseStrategy
    from trading_bot.strategy.model import FeatureSnapshot

    rm = RiskManager(RiskParams(min_rr=1.5))
    bt = Backtester(BaseStrategy(), BacktestConfig(),
                    sl_tp_fn=rm.as_sl_tp_fn(), sizing_fn=rm.as_sizing_fn())
    feat = FeatureSnapshot(
        price=100.0, ts=0, ema_fast=110.0, ema_slow=100.0, adx=30.0,
        osc=35.0, osc_prev=28.0, macd_hist=0.5, macd_hist_prev=0.2,
        bull_pattern=True, at_swing_low=True, volume=150.0, volume_avg=100.0,
        atr=1.0, obi=0.3, cvd_delta=1.2, funding_rate=0.0001)
    flat = FeatureSnapshot(price=100.0, ts=0, ema_fast=100.0, ema_slow=100.0, adx=10.0)
    series = [
        (Bar(0, 100, 100.5, 99.8, 100.0), feat),
        (Bar(1, 100, 110.0, 100.0, 109.0), flat),   # уводим вверх — тейк сработает
    ]
    res = bt.run(series)
    assert len(res.trades) == 1
    assert res.trades[0].qty > 0
