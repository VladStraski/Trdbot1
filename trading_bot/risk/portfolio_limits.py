"""Портфельные лимиты и circuit breakers (Этап 9, раздел 7 ТЗ).

Отдельный слой, проверяемый НЕЗАВИСИМО от стратегии. Ограничения (дефолты):
- риск на сделку .............. 1% equity
- суммарный риск по позициям .. 3% equity
- макс. открытых позиций ...... 3
- дневной лимит убытка ........ -3% -> стоп торговли до конца дня
- серия убытков подряд ........ 5 -> пауза + уведомление
- просадка от пика equity ..... -15% -> полная остановка, ручной рестарт

Чистый stdlib. Состояние держит `PortfolioGuard`; решения — `can_open` (перед
входом) и `register_close` (после сделки, возвращает сработавшие breakers).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..logger import get_logger

log = get_logger("risk.portfolio_limits")


@dataclass(frozen=True)
class LimitConfig:
    risk_per_trade: float = 0.01
    max_portfolio_risk: float = 0.03
    max_open_positions: int = 3
    daily_loss_limit: float = 0.03
    max_consecutive_losses: int = 5
    max_drawdown: float = 0.15

    @classmethod
    def from_settings(cls, settings) -> "LimitConfig":
        return cls(
            risk_per_trade=settings.risk_pct_per_trade,
            max_portfolio_risk=settings.max_portfolio_risk,
            max_open_positions=settings.max_open_positions,
            daily_loss_limit=settings.daily_loss_limit,
            max_consecutive_losses=settings.max_consecutive_losses,
            max_drawdown=settings.max_drawdown,
        )


@dataclass(frozen=True)
class BreakerEvent:
    name: str
    detail: str
    hard: bool           # hard=True — полная остановка (ручной рестарт)


class PortfolioGuard:
    """Слой портфельных лимитов и автостопов."""

    def __init__(self, config: LimitConfig, starting_equity: float) -> None:
        self.cfg = config
        self.equity = starting_equity
        self.peak_equity = starting_equity
        self.day_start_equity = starting_equity
        self.open_count = 0
        self.open_risk = 0.0
        self.consecutive_losses = 0
        self.halted = False
        self.halt_reasons: list[str] = []

    # ------------------------------------------------------------------ #
    def can_open(self, trade_risk_amount: float) -> tuple[bool, str]:
        """Разрешён ли новый вход с данным денежным риском."""
        if self.halted:
            return False, f"торговля остановлена: {', '.join(self.halt_reasons)}"
        if self.open_count >= self.cfg.max_open_positions:
            return False, f"достигнут лимит открытых позиций ({self.cfg.max_open_positions})"
        if self.equity <= 0:
            return False, "нулевой/отрицательный equity"
        per_trade = trade_risk_amount / self.equity
        if per_trade > self.cfg.risk_per_trade + 1e-9:
            return False, (f"риск сделки {per_trade:.3%} > лимита "
                           f"{self.cfg.risk_per_trade:.2%}")
        projected = (self.open_risk + trade_risk_amount) / self.equity
        if projected > self.cfg.max_portfolio_risk + 1e-9:
            return False, (f"суммарный риск {projected:.3%} > лимита "
                           f"{self.cfg.max_portfolio_risk:.2%}")
        return True, "ok"

    def register_open(self, trade_risk_amount: float) -> None:
        self.open_count += 1
        self.open_risk += trade_risk_amount

    def register_close(self, pnl: float, trade_risk_amount: float) -> list[BreakerEvent]:
        """Учесть закрытие сделки и проверить breakers. Возвращает сработавшие."""
        self.open_count = max(0, self.open_count - 1)
        self.open_risk = max(0.0, self.open_risk - trade_risk_amount)
        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.consecutive_losses = self.consecutive_losses + 1 if pnl <= 0 else 0
        return self._check_breakers()

    def on_equity(self, equity: float) -> list[BreakerEvent]:
        """Обновить equity (mark-to-market) и проверить просадку."""
        self.equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        return self._check_breakers()

    def new_day(self) -> None:
        """Сброс дневных лимитов (снимает дневной стоп, не снимает hard-стопы)."""
        self.day_start_equity = self.equity
        # Снять только «мягкие» причины (дневной стоп/серия), hard оставить.
        self.halt_reasons = [r for r in self.halt_reasons if r.startswith("HARD")]
        self.halted = bool(self.halt_reasons)
        self.consecutive_losses = 0

    def resume(self) -> None:
        """Ручное снятие мягкой паузы (серия убытков)."""
        self.halt_reasons = [r for r in self.halt_reasons if r.startswith("HARD")]
        self.halted = bool(self.halt_reasons)

    # ------------------------------------------------------------------ #
    def _halt(self, reason: str) -> None:
        if reason not in self.halt_reasons:
            self.halt_reasons.append(reason)
        self.halted = True

    def _check_breakers(self) -> list[BreakerEvent]:
        events: list[BreakerEvent] = []

        # Дневной лимит убытка.
        day_pnl = self.equity - self.day_start_equity
        if self.day_start_equity > 0 and \
                day_pnl <= -self.cfg.daily_loss_limit * self.day_start_equity:
            ev = BreakerEvent("daily_loss",
                              f"дневной убыток {day_pnl:.2f} превысил лимит "
                              f"{self.cfg.daily_loss_limit:.0%}", hard=False)
            events.append(ev)
            self._halt("дневной стоп")

        # Серия убыточных сделок.
        if self.consecutive_losses >= self.cfg.max_consecutive_losses:
            ev = BreakerEvent("consecutive_losses",
                              f"{self.consecutive_losses} убытков подряд", hard=False)
            events.append(ev)
            self._halt("пауза: серия убытков")

        # Просадка от пика equity — полная остановка.
        if self.peak_equity > 0:
            dd = (self.peak_equity - self.equity) / self.peak_equity
            if dd >= self.cfg.max_drawdown:
                ev = BreakerEvent("max_drawdown",
                                  f"просадка {dd:.1%} >= {self.cfg.max_drawdown:.0%}",
                                  hard=True)
                events.append(ev)
                self._halt("HARD: просадка от пика")

        for ev in events:
            log.warning("CIRCUIT BREAKER [%s]: %s%s", ev.name, ev.detail,
                        " (HARD-СТОП)" if ev.hard else "")
        return events
