"""Leverage manager — плечо от безопасности ликвидации (раздел 7 ТЗ).

Стоп-лосс и ликвидация РАЗДЕЛЯЮТСЯ всегда: дистанция до ликвидации должна быть
>= liq_safety_mult × дистанции до стопа (дефолт 3×). Для изолированной маржи
дистанция до ликвидации ≈ entry / leverage, отсюда предельное безопасное плечо:
    leverage <= entry / (liq_safety_mult × stop_distance)
Итоговое плечо — минимум из желаемого, безопасного и жёсткого потолка (hard_cap).
Чистый stdlib.
"""

from __future__ import annotations

import math


def safe_leverage(entry: float, stop_distance: float, desired_leverage: float,
                  hard_cap: int = 5, liq_safety_mult: float = 3.0) -> int:
    """Максимальное безопасное целое плечо (>=1)."""
    if stop_distance <= 0 or entry <= 0:
        return 1
    max_by_liq = entry / (liq_safety_mult * stop_distance)
    lev = min(desired_leverage, max_by_liq, float(hard_cap))
    return max(1, int(math.floor(lev)))


def liq_safety_ok(entry: float, stop_distance: float, liq_price: float,
                  liq_safety_mult: float = 3.0) -> bool:
    """Сверка фактической liqPrice из ответа биржи с нормой (раздел 7 ТЗ).

    После открытия позиции фактическая ликвидационная цена должна отстоять от
    входа не меньше, чем на liq_safety_mult × дистанции стопа.
    """
    if stop_distance <= 0:
        return False
    liq_distance = abs(entry - liq_price)
    return liq_distance >= liq_safety_mult * stop_distance
