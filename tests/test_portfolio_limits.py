"""Тесты портфельных лимитов и kill switch (Этап 9). Чистый stdlib."""

from trading_bot.risk.portfolio_limits import LimitConfig, PortfolioGuard
from trading_bot.risk.kill_switch import KillSwitch, KillSwitchConfig


def _guard(**over) -> PortfolioGuard:
    cfg = LimitConfig(**over)
    return PortfolioGuard(cfg, starting_equity=10_000.0)


# --- лимиты входа -----------------------------------------------------------
def test_per_trade_risk_limit():
    g = _guard(risk_per_trade=0.01)
    ok, _ = g.can_open(trade_risk_amount=100.0)      # ровно 1%
    assert ok is True
    ok2, reason = g.can_open(trade_risk_amount=150.0)  # 1.5% > 1%
    assert ok2 is False and "риск сделки" in reason


def test_portfolio_risk_limit():
    g = _guard(risk_per_trade=0.02, max_portfolio_risk=0.03)
    g.register_open(100.0)                           # 1%
    g.register_open(100.0)                           # 2%
    ok, reason = g.can_open(150.0)                   # +1.5% -> 3.5% > 3%
    assert ok is False and "суммарный риск" in reason


def test_max_open_positions():
    g = _guard(max_open_positions=2, max_portfolio_risk=1.0, risk_per_trade=1.0)
    g.register_open(10.0)
    g.register_open(10.0)
    ok, reason = g.can_open(10.0)
    assert ok is False and "лимит открытых позиций" in reason


# --- circuit breakers -------------------------------------------------------
def test_daily_loss_halts_until_new_day():
    g = _guard(daily_loss_limit=0.03)
    events = g.register_close(pnl=-350.0, trade_risk_amount=0.0)  # -3.5% дня
    assert any(e.name == "daily_loss" for e in events)
    assert g.halted is True
    ok, _ = g.can_open(10.0)
    assert ok is False
    g.new_day()                                      # новый день снимает дневной стоп
    assert g.halted is False


def test_consecutive_losses_pause():
    g = _guard(max_consecutive_losses=3, daily_loss_limit=1.0, max_drawdown=1.0)
    for _ in range(3):
        g.register_close(pnl=-10.0, trade_risk_amount=0.0)
    assert g.halted is True
    assert any("серия" in r for r in g.halt_reasons)
    g.resume()
    assert g.halted is False


def test_win_resets_consecutive_losses():
    g = _guard(max_consecutive_losses=3, daily_loss_limit=1.0, max_drawdown=1.0)
    g.register_close(pnl=-10.0, trade_risk_amount=0.0)
    g.register_close(pnl=-10.0, trade_risk_amount=0.0)
    g.register_close(pnl=+10.0, trade_risk_amount=0.0)   # победа сбрасывает счётчик
    assert g.consecutive_losses == 0


def test_max_drawdown_hard_stop():
    g = _guard(max_drawdown=0.15, daily_loss_limit=1.0)
    # Просадка 20% от пика -> hard-стоп, не снимается новым днём.
    events = g.on_equity(8_000.0)
    assert any(e.name == "max_drawdown" and e.hard for e in events)
    assert g.halted is True
    g.new_day()
    assert g.halted is True                            # hard-стоп остаётся
    g.resume()
    assert g.halted is True


# --- kill switch ------------------------------------------------------------
def test_kill_switch_manual():
    ks = KillSwitch()
    assert ks.tripped is False
    ks.trip("ручной стоп")
    assert ks.tripped is True and ks.reason == "ручной стоп"


def test_kill_switch_api_errors():
    ks = KillSwitch(KillSwitchConfig(max_api_errors=3))
    ks.record_api_error(); ks.record_api_error()
    assert ks.tripped is False
    ks.record_api_error()
    assert ks.tripped is True
    ks.reset()
    ks.record_api_error(); ks.record_api_success(); ks.record_api_error()
    assert ks.tripped is False                         # успех сбросил счётчик


def test_kill_switch_feed_gap():
    ks = KillSwitch(KillSwitchConfig(max_feed_gap_ms=1000))
    ks.record_feed(10_000)
    assert ks.check_feed_gap(10_500) is False          # разрыв 500мс < 1000
    assert ks.check_feed_gap(11_500) is True           # разрыв 1500мс > 1000
