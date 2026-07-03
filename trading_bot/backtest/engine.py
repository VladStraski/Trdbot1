"""Бэктест-движок (Этап 5 ТЗ).

Использует ТУ ЖЕ `BaseStrategy`, что и live-контур (раздел 10 ТЗ) — это гарантия,
что бэктест и реальная торговля принимают решения одинаково.

Защита от look-ahead (раздел 5 ТЗ):
- признаки бара i КАУЗАЛЬНЫ (строятся адаптером только по данным ≤ i);
- вход исполняется по цене закрытия сигнального бара i;
- проверка SL/TP начинается со СЛЕДУЮЩЕГО бара (i+1) по его high/low — сделка не
  может открыться и закрыться внутри одного бара по «будущему» экстремуму.

Издержки (раздел 7 ТЗ) учитываются обязательно: комиссии maker/taker на вход и
выход + funding за время удержания — иначе результат завышен.

Движок работает на плоских `Bar` + `FeatureSnapshot` (stdlib) — тестируется офлайн
без pandas; построение серии из свечей — отдельный адаптер (`series_from_frames`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..logger import get_logger
from ..strategy.base_strategy import BaseStrategy
from ..strategy.model import LONG, SHORT, FeatureSnapshot

log = get_logger("backtest.engine")


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class CostModel:
    """Модель издержек Bybit linear (доли, не проценты)."""

    taker_fee: float = 0.00055        # 0.055% тейкер
    maker_fee: float = 0.0002         # 0.02% мейкер
    funding_per_8h: float = 0.0001    # усреднённый funding за 8ч
    slippage: float = 0.0002          # проскальзывание как доля цены

    def fee(self, notional: float, taker: bool = True) -> float:
        return abs(notional) * (self.taker_fee if taker else self.maker_fee)

    def funding_cost(self, notional: float, hold_ms: int) -> float:
        hours = hold_ms / 3_600_000.0
        return abs(notional) * self.funding_per_8h * (hours / 8.0)


@dataclass
class BacktestConfig:
    initial_equity: float = 10_000.0
    risk_pct: float = 0.01            # доля equity под риск на сделку
    atr_stop_mult: float = 1.75       # дистанция стопа в ATR
    min_rr: float = 1.5              # целевой R:R (TP = min_rr * риск)
    use_swing_stops: bool = True     # если есть swing-уровень ближе — использовать его


@dataclass
class Trade:
    direction: str
    entry_ts: int
    entry_price: float
    qty: float
    stop: float
    take: float
    exit_ts: int = 0
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0                  # чистый PnL (после издержек)
    fees: float = 0.0
    funding: float = 0.0
    r_multiple: float = 0.0
    risk_amount: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)
    initial_equity: float = 0.0
    final_equity: float = 0.0

    def summary(self) -> dict:
        wins = [t for t in self.trades if t.pnl > 0]
        losses = [t for t in self.trades if t.pnl <= 0]
        gross_win = sum(t.pnl for t in wins)
        gross_loss = -sum(t.pnl for t in losses)
        peak = self.initial_equity
        max_dd = 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                max_dd = max(max_dd, (peak - eq) / peak)
        n = len(self.trades)
        return {
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": (len(wins) / n) if n else 0.0,
            "net_pnl": self.final_equity - self.initial_equity,
            "return_pct": ((self.final_equity / self.initial_equity - 1.0)
                           if self.initial_equity else 0.0),
            "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
            "max_drawdown": max_dd,
            "final_equity": self.final_equity,
            "total_fees": sum(t.fees for t in self.trades),
            "total_funding": sum(t.funding for t in self.trades),
        }


# Тип функций стоп/размера — можно подменить Risk Manager'ом (Этап 6).
SlTpFn = Callable[[str, float, FeatureSnapshot, BacktestConfig], "tuple[float, float]"]
SizingFn = Callable[[float, float, float, BacktestConfig], float]


def default_sl_tp(direction: str, entry: float, feat: FeatureSnapshot,
                  cfg: BacktestConfig) -> tuple[float, float]:
    """Стоп по ATR (или ближайший swing), тейк по целевому R:R."""
    atr = feat.atr if feat.atr > 0 else entry * 0.005  # запасной стоп 0.5%
    if direction == LONG:
        stop = entry - cfg.atr_stop_mult * atr
        if cfg.use_swing_stops and feat.swing_low_price is not None:
            stop = min(stop, feat.swing_low_price)  # не ближе свинга
        risk = entry - stop
        take = entry + cfg.min_rr * risk
    else:
        stop = entry + cfg.atr_stop_mult * atr
        if cfg.use_swing_stops and feat.swing_high_price is not None:
            stop = max(stop, feat.swing_high_price)
        risk = stop - entry
        take = entry - cfg.min_rr * risk
    return stop, take


def default_sizing(equity: float, entry: float, stop: float,
                   cfg: BacktestConfig) -> float:
    """Fixed-fractional risk: qty = (equity*risk_pct) / |entry - stop|."""
    dist = abs(entry - stop)
    if dist <= 0:
        return 0.0
    return (equity * cfg.risk_pct) / dist


class Backtester:
    def __init__(self, strategy: BaseStrategy, config: BacktestConfig | None = None,
                 cost_model: CostModel | None = None,
                 sl_tp_fn: SlTpFn = default_sl_tp,
                 sizing_fn: SizingFn = default_sizing) -> None:
        self.strategy = strategy
        self.config = config or BacktestConfig()
        self.costs = cost_model or CostModel()
        self.sl_tp_fn = sl_tp_fn
        self.sizing_fn = sizing_fn

    def run(self, series: list[tuple[Bar, FeatureSnapshot]]) -> BacktestResult:
        cfg = self.config
        equity = cfg.initial_equity
        result = BacktestResult(initial_equity=equity)
        open_trade: Optional[Trade] = None

        for bar, feat in series:
            # 1) Управление открытой позицией — проверка стопа/тейка по бару.
            if open_trade is not None:
                exit_price, reason = self._check_exit(open_trade, bar)
                if exit_price is not None:
                    equity += self._close(open_trade, bar.ts, exit_price, reason)
                    result.trades.append(open_trade)
                    open_trade = None

            # 2) Поиск входа (только если нет открытой позиции).
            if open_trade is None:
                signal = self.strategy.scorer.evaluate(feat)
                if signal.entered and signal.direction is not None:
                    open_trade = self._open(signal.direction, bar, feat, equity)

            result.equity_curve.append((bar.ts, equity))

        # Закрыть остаток по последней цене (mark-to-market по close).
        if open_trade is not None and series:
            last_bar = series[-1][0]
            equity += self._close(open_trade, last_bar.ts, last_bar.close, "eod")
            result.trades.append(open_trade)

        result.final_equity = equity
        return result

    # ------------------------------------------------------------------ #
    def _open(self, direction: str, bar: Bar, feat: FeatureSnapshot,
              equity: float) -> Optional[Trade]:
        slip = self.costs.slippage
        # Вход по close с проскальзыванием против нас.
        entry = bar.close * (1 + slip) if direction == LONG else bar.close * (1 - slip)
        stop, take = self.sl_tp_fn(direction, entry, feat, self.config)
        qty = self.sizing_fn(equity, entry, stop, self.config)
        if qty <= 0:
            return None
        risk_amount = abs(entry - stop) * qty
        entry_fee = self.costs.fee(entry * qty, taker=True)
        return Trade(
            direction=direction, entry_ts=bar.ts, entry_price=entry, qty=qty,
            stop=stop, take=take, fees=entry_fee, risk_amount=risk_amount,
        )

    def _check_exit(self, trade: Trade, bar: Bar) -> tuple[Optional[float], str]:
        """Проверить срабатывание стопа/тейка на баре (SL приоритетнее — консервативно).

        Вход исполнен по close бара входа; на самом баре входа выходы не проверяем
        (bar.ts == entry_ts), только на последующих — защита от внутрибарного
        look-ahead.
        """
        if bar.ts <= trade.entry_ts:
            return None, ""
        if trade.direction == LONG:
            if bar.low <= trade.stop:
                return trade.stop, "stop"
            if bar.high >= trade.take:
                return trade.take, "take"
        else:
            if bar.high >= trade.stop:
                return trade.stop, "stop"
            if bar.low <= trade.take:
                return trade.take, "take"
        return None, ""

    def _close(self, trade: Trade, ts: int, price: float, reason: str) -> float:
        """Закрыть сделку, вернуть изменение equity (чистый PnL)."""
        exit_fee = self.costs.fee(price * trade.qty, taker=True)
        hold_ms = max(0, ts - trade.entry_ts)
        funding = self.costs.funding_cost(trade.entry_price * trade.qty, hold_ms)
        sign = 1.0 if trade.direction == LONG else -1.0
        gross = (price - trade.entry_price) * trade.qty * sign
        net = gross - trade.fees - exit_fee - funding

        trade.exit_ts = ts
        trade.exit_price = price
        trade.exit_reason = reason
        trade.fees += exit_fee
        trade.funding = funding
        trade.pnl = net
        trade.r_multiple = (net / trade.risk_amount) if trade.risk_amount > 0 else 0.0
        return net
