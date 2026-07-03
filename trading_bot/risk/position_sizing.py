"""Position sizing — fixed fractional risk (раздел 7 ТЗ).

Размер позиции считается от РИСКА на сделку, а не от фиксированной суммы/плеча:
    risk_amount = equity * risk_pct
    qty         = risk_amount / |entry - stop|
Так убыток при срабатывании стопа ≈ risk_pct от equity независимо от цены и
дистанции стопа. Чистый stdlib.
"""

from __future__ import annotations

import math
from typing import Optional


def fixed_fractional_qty(equity: float, risk_pct: float, entry: float,
                         stop: float, qty_step: Optional[float] = None,
                         min_qty: float = 0.0) -> float:
    """Количество контрактов под заданный риск.

    qty_step — шаг лота инструмента (округление ВНИЗ, чтобы не превысить риск).
    Возвращает 0.0, если вход некорректен (нулевая дистанция стопа и т.п.).
    """
    if equity <= 0 or risk_pct <= 0:
        return 0.0
    dist = abs(entry - stop)
    if dist <= 0:
        return 0.0
    qty = (equity * risk_pct) / dist
    if qty_step and qty_step > 0:
        qty = math.floor(qty / qty_step) * qty_step
    if qty < min_qty:
        return 0.0
    return qty


def risk_amount(equity: float, risk_pct: float) -> float:
    """Денежный риск на сделку."""
    return max(0.0, equity) * max(0.0, risk_pct)
