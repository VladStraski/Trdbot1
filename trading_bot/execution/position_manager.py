"""Position Manager + синхронизация (Этап 8 ТЗ).

Держит внутреннее представление позиций бота и синхронизирует его с реальным
состоянием на бирже двумя путями (раздел 9–10 ТЗ):
- приватный WS (`position`-топик) — быстрые обновления в реальном времени;
- периодическая сверка через REST (`get_positions`) — источник истины; любое
  расхождение с внутренним состоянием = алерт.

Парсинг и сверка — чистый stdlib: тестируются офлайн проигрыванием событий и
снапшотов, без сети. Транспорт приватного WS вынесен в `private_ws.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..logger import get_logger

log = get_logger("execution.position_manager")


@dataclass(frozen=True)
class PositionState:
    symbol: str
    side: str                 # "Buy" | "Sell" (для непустой позиции)
    size: float
    entry_price: float = 0.0
    leverage: float = 0.0
    liq_price: float = 0.0
    unrealised_pnl: float = 0.0
    updated_ts: int = 0


@dataclass(frozen=True)
class Discrepancy:
    symbol: str
    field: str
    internal: object
    exchange: object


def parse_position(raw: dict) -> Optional[PositionState]:
    """Разобрать сырую позицию Bybit (WS/REST). None — плоская позиция (size 0)."""
    symbol = raw.get("symbol") or ""
    side = raw.get("side") or ""
    size = _f(raw.get("size"))
    if size == 0.0 or side == "":
        return None
    return PositionState(
        symbol=symbol, side=side, size=size,
        entry_price=_f(raw.get("entryPrice") or raw.get("avgPrice")),
        leverage=_f(raw.get("leverage")),
        liq_price=_f(raw.get("liqPrice")),
        unrealised_pnl=_f(raw.get("unrealisedPnl")),
        updated_ts=int(_f(raw.get("updatedTime"))),
    )


class PositionManager:
    def __init__(self, size_tol: float = 1e-8) -> None:
        self.size_tol = size_tol
        self.positions: dict[str, PositionState] = {}

    # ------------------------------------------------------------------ #
    def apply_events(self, raw_list: list[dict]) -> None:
        """Обновить внутреннее состояние из списка сырых позиций (WS или REST)."""
        for raw in raw_list:
            symbol = raw.get("symbol") or ""
            state = parse_position(raw)
            if state is None:
                self.positions.pop(symbol, None)     # позиция закрыта
            else:
                self.positions[symbol] = state

    def get(self, symbol: str) -> Optional[PositionState]:
        return self.positions.get(symbol)

    def is_flat(self, symbol: str) -> bool:
        return symbol not in self.positions

    # ------------------------------------------------------------------ #
    def reconcile(self, rest_list: list[dict]) -> list[Discrepancy]:
        """Сверить внутреннее состояние с REST-снапшотом (источник истины).

        Возвращает список расхождений (для алерта) и ПРИВОДИТ внутреннее
        состояние к биржевому. Пустой список = синхронизировано.
        """
        exchange: dict[str, PositionState] = {}
        for raw in rest_list:
            state = parse_position(raw)
            if state is not None:
                exchange[state.symbol] = state

        diffs: list[Discrepancy] = []
        symbols = set(self.positions) | set(exchange)
        for sym in symbols:
            internal = self.positions.get(sym)
            exch = exchange.get(sym)
            if internal is None and exch is not None:
                diffs.append(Discrepancy(sym, "presence", "flat", "position"))
            elif internal is not None and exch is None:
                diffs.append(Discrepancy(sym, "presence", "position", "flat"))
            elif internal is not None and exch is not None:
                if internal.side != exch.side:
                    diffs.append(Discrepancy(sym, "side", internal.side, exch.side))
                if abs(internal.size - exch.size) > self.size_tol:
                    diffs.append(Discrepancy(sym, "size", internal.size, exch.size))

        # Приводим внутреннее состояние к бирже (источник истины).
        self.positions = dict(exchange)
        if diffs:
            for d in diffs:
                log.warning("РАСХОЖДЕНИЕ %s.%s: бот=%s биржа=%s",
                            d.symbol, d.field, d.internal, d.exchange)
        return diffs


def _f(value) -> float:
    try:
        return float(value) if value not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0
