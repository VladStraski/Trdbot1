"""Execution Engine (Этап 7 ТЗ).

Превращает ОДОБРЕННОЕ риск-решение в биржевой ордер через единый BybitClient
(demo/prod — по конфигу, раздел 3 ТЗ). Ключевые требования раздела 7:
- ISOLATED-маржа и рассчитанное плечо выставляются перед входом;
- SL и TP уходят на биржу вместе с входом как reduce-only (прикреплены к позиции),
  никогда не хранятся «только в памяти» бота;
- kill switch: немедленное закрытие позиции reduce-only рыночным ордером.

`dry_run=True` — «сухой» прогон: намерение логируется, но на биржу ничего не
уходит (демонстрация без ключей/сети). Реальные ордера на demo — live-долг.
Логика формирования ордеров тестируется офлайн с фейковым HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from ..logger import get_logger
from ..strategy.model import LONG, SHORT

log = get_logger("execution.engine")


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    action: str                       # "open" | "close" | "cancel"
    side: Optional[str] = None
    qty: float = 0.0
    order_type: str = ""
    stop: Optional[float] = None
    take: Optional[float] = None
    order_id: Optional[str] = None
    order_link_id: Optional[str] = None
    dry_run: bool = False
    reason: str = ""
    raw: Optional[dict] = None


class ExecutionEngine:
    def __init__(self, client, settings, dry_run: bool = False,
                 isolated: bool = True) -> None:
        self.client = client
        self.settings = settings
        self.dry_run = dry_run
        self.isolated = isolated

    # ------------------------------------------------------------------ #
    @staticmethod
    def _entry_side(direction: str) -> str:
        return "Buy" if direction == LONG else "Sell"

    @staticmethod
    def _exit_side(direction: str) -> str:
        return "Sell" if direction == LONG else "Buy"

    def _prepare_margin(self, leverage: int) -> None:
        """Выставить ISOLATED-маржу и плечо; не падать, если уже установлено."""
        try:
            if self.isolated:
                self.client.set_margin_mode_isolated(leverage)
            else:
                self.client.set_leverage(leverage)
        except Exception as exc:  # noqa: BLE001
            # Bybit возвращает ошибку, если режим/плечо уже такие — это не фатально.
            log.warning("Настройка маржи/плеча: %s (продолжаем)", exc)
            try:
                self.client.set_leverage(leverage)
            except Exception as exc2:  # noqa: BLE001
                log.warning("set_leverage тоже не удался: %s (продолжаем)", exc2)

    # ------------------------------------------------------------------ #
    def open_from_decision(self, decision, order_type: str = "Market",
                           order_link_id: Optional[str] = None) -> ExecutionResult:
        """Открыть позицию по одобренному RiskDecision с прикреплёнными SL/TP."""
        if not getattr(decision, "approved", False):
            return ExecutionResult(False, "open", reason="решение не одобрено")

        side = self._entry_side(decision.direction)
        price = None if order_type == "Market" else decision.entry

        if self.dry_run:
            log.info("[DRY-RUN] %s %s qty=%.6f SL=%.2f TP=%.2f плечо=%dx",
                     side, order_type, decision.qty, decision.stop,
                     decision.take, decision.leverage)
            return ExecutionResult(
                True, "open", side=side, qty=decision.qty, order_type=order_type,
                stop=decision.stop, take=decision.take, dry_run=True,
                order_link_id=order_link_id, reason="dry-run",
            )

        self._prepare_margin(decision.leverage)
        try:
            resp = self.client.place_order(
                side=side, order_type=order_type, qty=decision.qty, price=price,
                reduce_only=False, stop_loss=decision.stop,
                take_profit=decision.take, order_link_id=order_link_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Ошибка размещения входного ордера: %s", exc)
            return ExecutionResult(False, "open", side=side, qty=decision.qty,
                                   reason=str(exc))
        log.info("Открыт %s qty=%.6f SL=%.2f TP=%.2f (orderId=%s)",
                 side, decision.qty, decision.stop, decision.take,
                 resp.get("orderId"))
        return ExecutionResult(
            True, "open", side=side, qty=decision.qty, order_type=order_type,
            stop=decision.stop, take=decision.take,
            order_id=resp.get("orderId"), order_link_id=resp.get("orderLinkId"),
            raw=resp,
        )

    def close_position(self, direction: str, qty: float,
                       order_link_id: Optional[str] = None) -> ExecutionResult:
        """Закрыть позицию reduce-only рыночным ордером (в т.ч. kill switch)."""
        side = self._exit_side(direction)
        if self.dry_run:
            log.info("[DRY-RUN] закрытие %s qty=%.6f (reduce-only)", side, qty)
            return ExecutionResult(True, "close", side=side, qty=qty,
                                   dry_run=True, reason="dry-run")
        try:
            resp = self.client.place_order(
                side=side, order_type="Market", qty=qty, reduce_only=True,
                order_link_id=order_link_id,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("Ошибка закрытия позиции: %s", exc)
            return ExecutionResult(False, "close", side=side, qty=qty,
                                   reason=str(exc))
        log.info("Закрытие %s qty=%.6f отправлено (orderId=%s)",
                 side, qty, resp.get("orderId"))
        return ExecutionResult(True, "close", side=side, qty=qty,
                               order_id=resp.get("orderId"), raw=resp)

    def cancel_all(self) -> ExecutionResult:
        if self.dry_run:
            log.info("[DRY-RUN] отмена всех ордеров")
            return ExecutionResult(True, "cancel", dry_run=True, reason="dry-run")
        try:
            resp = self.client.cancel_all()
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(False, "cancel", reason=str(exc))
        return ExecutionResult(True, "cancel", raw=resp)
