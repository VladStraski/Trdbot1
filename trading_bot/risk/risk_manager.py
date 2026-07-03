"""Risk Manager — единое риск-решение по сделке (раздел 7 ТЗ).

Собирает воедино sizing, стоп/тейк, плечо и издержки: из направления, входа, ATR
и swing-уровней выдаёт `RiskDecision` (qty, stop, take, leverage, risk_amount,
net R:R) либо отклонение с причиной. Тейк ставится так, чтобы R:R ПОСЛЕ издержек
был не ниже min_rr (издержки в расчёт R:R — требование раздела 7). Чистый stdlib.

Предоставляет адаптеры под бэктест-движок (`as_sl_tp_fn`/`as_sizing_fn`), поэтому
Этап 5 использует ровно ту же логику риска, что и live-контур.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..logger import get_logger
from ..strategy.model import LONG, SHORT
from . import costs as costs_mod
from . import leverage_manager, position_sizing, stop_take_manager

log = get_logger("risk.manager")


@dataclass(frozen=True)
class RiskParams:
    risk_pct: float = 0.01
    atr_stop_mult: float = 1.75
    min_rr: float = 1.5
    leverage_hard_cap: int = 5
    desired_leverage: float = 5.0
    liq_safety_mult: float = 3.0
    qty_step: Optional[float] = None
    min_qty: float = 0.0
    use_swing: bool = True
    hold_hours_est: float = 8.0

    @classmethod
    def from_settings(cls, settings) -> "RiskParams":
        return cls(
            risk_pct=settings.risk_pct_per_trade,
            atr_stop_mult=settings.atr_stop_mult,
            min_rr=settings.min_rr,
            leverage_hard_cap=settings.leverage_hard_cap,
            desired_leverage=float(settings.leverage_hard_cap),
        )


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    direction: str
    entry: float
    stop: float
    take: float
    qty: float
    leverage: int
    risk_amount: float
    net_rr: float
    reason: str = ""


class RiskManager:
    def __init__(self, params: RiskParams | None = None,
                 costs: costs_mod.CostConfig | None = None) -> None:
        self.params = params or RiskParams()
        self.costs = costs or costs_mod.CostConfig()

    # ------------------------------------------------------------------ #
    def _stop_take(self, direction: str, entry: float, atr: float,
                   swing_low: Optional[float],
                   swing_high: Optional[float]) -> tuple[float, float]:
        p = self.params
        swing = swing_low if direction == LONG else swing_high
        stop = stop_take_manager.initial_stop(
            direction, entry, atr, p.atr_stop_mult, swing, p.use_swing)
        risk = abs(entry - stop)
        # Тейк с поправкой на издержки: net R:R = min_rr.
        cost = costs_mod.cost_per_unit(entry, self.costs, p.hold_hours_est)
        reward = p.min_rr * (risk + cost) + cost
        take = entry + reward if direction == LONG else entry - reward
        return stop, take

    def decide(self, direction: str, entry: float, atr: float, equity: float,
               swing_low: Optional[float] = None,
               swing_high: Optional[float] = None) -> RiskDecision:
        p = self.params
        stop, take = self._stop_take(direction, entry, atr, swing_low, swing_high)
        stop_distance = abs(entry - stop)

        def reject(msg: str) -> RiskDecision:
            return RiskDecision(False, direction, entry, stop, take, 0.0, 1,
                                0.0, 0.0, msg)

        if stop_distance <= 0:
            return reject("нулевая дистанция стопа")

        qty = position_sizing.fixed_fractional_qty(
            equity, p.risk_pct, entry, stop, p.qty_step, p.min_qty)
        if qty <= 0:
            return reject("размер позиции = 0 (мал equity/шаг лота)")

        net_rr = costs_mod.net_reward_risk(
            entry, stop, take, self.costs, p.hold_hours_est)
        if net_rr < p.min_rr - 1e-9:
            return reject(f"R:R после издержек {net_rr:.2f} < {p.min_rr}")

        leverage = leverage_manager.safe_leverage(
            entry, stop_distance, p.desired_leverage, p.leverage_hard_cap,
            p.liq_safety_mult)
        risk_amount = stop_distance * qty

        return RiskDecision(
            approved=True, direction=direction, entry=entry, stop=stop, take=take,
            qty=qty, leverage=leverage, risk_amount=risk_amount, net_rr=net_rr,
            reason="ok",
        )

    # ------------------------------------------------------------------ #
    #  Адаптеры под бэктест-движок (Этап 5)
    # ------------------------------------------------------------------ #
    def as_sl_tp_fn(self):
        def fn(direction, entry, feat, _cfg):
            return self._stop_take(direction, entry, feat.atr,
                                   feat.swing_low_price, feat.swing_high_price)
        return fn

    def as_sizing_fn(self):
        def fn(equity, entry, stop, _cfg):
            p = self.params
            return position_sizing.fixed_fractional_qty(
                equity, p.risk_pct, entry, stop, p.qty_step, p.min_qty)
        return fn
