"""Издержки для расчёта R:R (раздел 7 ТЗ).

Комиссии maker/taker (round-trip: вход + выход) и funding за время удержания
обязательно входят в оценку R:R — иначе сделка выглядит выгоднее, чем есть.
Все величины — доли (не проценты). Чистый stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostConfig:
    taker_fee: float = 0.00055        # 0.055%
    maker_fee: float = 0.0002         # 0.02%
    funding_per_8h: float = 0.0001    # усреднённый funding за 8ч


def round_trip_fee_fraction(cfg: CostConfig, taker: bool = True) -> float:
    """Доля комиссии за вход+выход (round-trip)."""
    fee = cfg.taker_fee if taker else cfg.maker_fee
    return 2.0 * fee


def funding_fraction(cfg: CostConfig, hold_hours: float) -> float:
    """Доля funding за время удержания."""
    return cfg.funding_per_8h * (max(0.0, hold_hours) / 8.0)


def cost_per_unit(entry: float, cfg: CostConfig, hold_hours: float,
                  taker: bool = True) -> float:
    """Издержки в ЦЕНОВЫХ единицах на 1 контракт (notional = entry × 1)."""
    frac = round_trip_fee_fraction(cfg, taker) + funding_fraction(cfg, hold_hours)
    return entry * frac


def net_reward_risk(entry: float, stop: float, take: float, cfg: CostConfig,
                    hold_hours: float = 8.0, taker: bool = True) -> float:
    """R:R ПОСЛЕ издержек: (reward - cost) / (risk + cost).

    reward/risk берутся по цене; издержки вычитаются из награды и добавляются к
    риску (консервативно). Возвращает 0.0 при некорректных уровнях.
    """
    risk = abs(entry - stop)
    reward = abs(take - entry)
    if risk <= 0:
        return 0.0
    cost = cost_per_unit(entry, cfg, hold_hours, taker)
    net_reward = reward - cost
    net_risk = risk + cost
    if net_risk <= 0:
        return 0.0
    return net_reward / net_risk
