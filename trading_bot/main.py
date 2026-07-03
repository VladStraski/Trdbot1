"""Точка входа бота.

Реализованные этапы (раздел 9 ТЗ):
    Этап 1 — Data Layer: подключение к Bybit, klines по трём TF, multi-TF
             агрегатор, выравнивание без look-ahead.
    Этап 2 — Indicator Engine: расчёт индикаторов на исторических данных.
    Этап 3 — Order Book Module: OBI, стены, спред и CVD (реал-тайм / офлайн-реплей).

Запуск:
    python -m trading_bot.main stage1 [--offline] [--demo-funds]
    python -m trading_bot.main stage2 [--offline] [--stoch-rsi]
    python -m trading_bot.main stage3 [--offline] [--duration N] [--depth 50]

Публичные данные стакана/сделок (Этап 3) идут через общий стрим
`stream.bybit.com` (mainnet = demo). REST-этапы (1–2) в live-режиме требуют
доступа к `api-demo.bybit.com`; при закрытом доступе используйте `--offline`.
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from .config.settings import TF_MS, Settings, load_settings
from .data.klines import fetch_klines
from .data.multi_tf_aggregator import MultiTFAggregator
from .execution.bybit_client import BybitClient
from .indicators.engine import IndicatorConfig, IndicatorEngine
from .logger import get_logger, setup_logging
from .storage.models import Storage

log = get_logger("main")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _synthetic_klines(tf: str, n: int, end_ms: int) -> pd.DataFrame:
    """Синтетические закрытые свечи для офлайн-прогона (детерминированный
    ряд без внешних данных; случайность не используется намеренно)."""
    step = TF_MS[tf]
    last_open = (end_ms // step) * step - step  # последняя полностью закрытая
    rows = []
    for i in range(n):
        ot = last_open - (n - 1 - i) * step
        base = 30000 + (ot // step) % 500          # плавный детерминированный ход
        rows.append({
            "open_time": ot, "open": base, "high": base + 20,
            "low": base - 20, "close": base + 5, "volume": 100,
            "turnover": base * 100, "close_time": ot + step, "is_closed": True,
        })
    return pd.DataFrame(rows)


def _populate_aggregator(settings: Settings, klines_limit: int, offline: bool,
                         request_demo_funds: bool = False,
                         check_account: bool = False) -> MultiTFAggregator | None:
    """Создать и наполнить multi-TF агрегатор (live или offline).

    Возвращает готовый агрегатор либо None при неустранимой ошибке.
    Общий код для stage1/stage2.
    """
    tfs = [settings.tf_context, settings.tf_signal, settings.tf_trigger]
    agg = MultiTFAggregator(timeframes=tfs)
    now = _now_ms()

    if offline:
        for tf in tfs:
            agg.seed(tf, _synthetic_klines(tf, klines_limit, now))
        return agg

    client = BybitClient(settings)
    if not client.ping():
        log.error("Bybit API недоступен — проверьте сеть/прокси. Если домен "
                  "bybit заблокирован политикой окружения, используйте --offline")
        return None
    log.info("API доступен ✔")

    if check_account:
        if settings.api_key and settings.api_secret:
            if request_demo_funds and settings.is_demo:
                try:
                    client.request_demo_funds()
                    log.info("Демо-баланс пополнен ✔")
                except Exception as exc:  # noqa: BLE001
                    log.warning("Не удалось пополнить демо-баланс: %s", exc)
            try:
                equity = client.get_equity(coin="USDT")
                log.info("Equity (USDT): %.4f", equity)
                with Storage(settings.db_path) as store:
                    store.record_equity(_now_ms(), equity, "USDT", source="rest")
            except Exception as exc:  # noqa: BLE001
                log.warning("Не удалось получить equity: %s", exc)
        else:
            log.warning("API-ключи не заданы — шаги с аккаунтом пропущены "
                        "(klines и агрегатор работают на публичных данных)")

    for tf in tfs:
        df = fetch_klines(client, tf, limit=klines_limit, only_closed=True, now_ms=now)
        if df.empty:
            log.error("Пустые klines для TF=%s", tf)
            return None
        agg.seed(tf, df)
    return agg


def stage1_healthcheck(request_demo_funds: bool = False, klines_limit: int = 300,
                       offline: bool = False) -> int:
    """Этап 1: подключение, klines, multi-TF агрегатор, проверка выравнивания.

    offline=True — прогон на синтетических данных без сети (для окружений, где
    доступ к api.bybit.com закрыт сетевой политикой). Проверяет всю логику
    seed + выравнивания без look-ahead, минуя REST.
    """
    settings = load_settings()
    setup_logging(settings.log_dir, settings.log_level)
    mode = "OFFLINE (синтетика)" if offline else "LIVE (Bybit REST)"
    log.info("=== Этап 1: Data Layer health-check [%s] ===", mode)
    log.info("Конфигурация: %s", settings.redacted())

    agg = _populate_aggregator(settings, klines_limit, offline,
                               request_demo_funds=request_demo_funds,
                               check_account=True)
    if agg is None:
        return 1

    # 4) Проверка выравнивания без look-ahead: для последней закрытой свечи
    #    триггерного TF берём контекстную свечу и убеждаемся, что она закрылась
    #    НЕ ПОЗЖЕ момента решения.
    trigger_tf, context_tf = settings.tf_trigger, settings.tf_context
    last_trigger = agg.latest(trigger_tf, 1).iloc[0]
    ref_time = int(last_trigger["close_time"])  # момент принятия решения
    ctx = agg.aligned(context_tf, ref_time)
    if ctx is None:
        log.error("Не удалось выровнять %s к %s (недостаточно истории)",
                  context_tf, trigger_tf)
        return 1

    assert int(ctx["close_time"]) <= ref_time, "LOOK-AHEAD: контекст закрылся позже решения!"
    log.info(
        "Выравнивание OK ✔  триггер %s close_time=%s  ->  контекст %s close_time=%s "
        "(закрыт до момента решения)",
        trigger_tf, ref_time, context_tf, int(ctx["close_time"]),
    )

    log.info("=== Этап 1 завершён успешно ✔ ===")
    return 0


def stage2_indicators(klines_limit: int = 300, offline: bool = False,
                      use_stoch_rsi: bool = False) -> int:
    """Этап 2: расчёт индикаторов на исторических данных сигнального TF (1h)
    и снапшот последних значений (замена «проверки на графике» в CLI)."""
    settings = load_settings()
    setup_logging(settings.log_dir, settings.log_level)
    mode = "OFFLINE (синтетика)" if offline else "LIVE (Bybit REST)"
    log.info("=== Этап 2: Indicator Engine [%s] ===", mode)

    agg = _populate_aggregator(settings, klines_limit, offline)
    if agg is None:
        return 1

    cfg = IndicatorConfig(use_stoch_rsi=use_stoch_rsi)
    engine = IndicatorEngine(cfg)

    for tf in (settings.tf_context, settings.tf_signal, settings.tf_trigger):
        frame = agg.frame(tf)
        enriched = engine.compute(frame)
        last = enriched.iloc[-1]
        osc = (f"stochK={last.get('stochrsi_k'):.1f}" if use_stoch_rsi
               else f"rsi={last.get('rsi'):.1f}")
        log.info(
            "[%s] close=%.2f ema%d=%.2f ema%d=%.2f adx=%.1f %s "
            "macd_hist=%.4f atr=%.2f bb_bw=%.4f",
            tf, last["close"], cfg.ema_fast, last[f"ema_{cfg.ema_fast}"],
            cfg.ema_slow, last[f"ema_{cfg.ema_slow}"], last["adx"], osc,
            last["macd_hist"], last["atr"], last["bb_bandwidth"],
        )
        # Санити-проверки: индикаторы посчитаны, без NaN на последнем баре.
        for col in ("adx", "atr", "macd_hist", f"ema_{cfg.ema_slow}"):
            if pd.isna(last[col]):
                log.error("NaN в %s на последнем баре TF=%s "
                          "(недостаточно истории для %s?)", col, tf, col)
                return 1

    log.info("=== Этап 2 завершён успешно ✔ ===")
    return 0


def stage3_orderbook(offline: bool = False, duration: float = 15.0,
                     depth: int = 50, obi_depth: int = 25,
                     cvd_window_ms: int = 60_000,
                     updates: int = 40) -> int:
    """Этап 3: Order Book Module — OBI, стены, спред и CVD в реальном времени.

    offline=True — проигрывание детерминированного синтетического потока
    (snapshot + дельты стакана + publicTrade) через те же обработчики, что и
    live-контур; проверяет всю логику без сети/pybit.

    offline=False — подписка на публичный WS `orderbook.{depth}.{symbol}` и
    `publicTrade.{symbol}` (общий стрим mainnet/demo, раздел 3 ТЗ) на `duration`
    секунд, с периодическим снимком микро-контекста в лог.
    """
    from .data.orderbook import OrderBook
    from .data.trades_stream import TradesStream

    settings = load_settings()
    setup_logging(settings.log_dir, settings.log_level)
    mode = "OFFLINE (синтетика)" if offline else "LIVE (Bybit public WS)"
    log.info("=== Этап 3: Order Book Module [%s] ===", mode)
    log.info("symbol=%s depth=%d obi_depth=%d cvd_window=%dмс",
             settings.symbol, depth, obi_depth, cvd_window_ms)

    order_book = OrderBook(symbol=settings.symbol)
    trades = TradesStream()

    def _log_micro(tag: str) -> None:
        ob_snap = order_book.snapshot(obi_depth=obi_depth)
        tr_snap = trades.snapshot(window_ms=cvd_window_ms)
        if not ob_snap["ready"] or ob_snap["mid"] is None:
            log.info("[%s] стакан ещё не готов…", tag)
            return
        log.info(
            "[%s] mid=%.2f spread=%.2f (%.2f bps) OBI=%+.3f | "
            "CVD=%+.4f Δ=%+.4f trades=%d | стены=%d",
            tag, ob_snap["mid"], ob_snap["spread"], ob_snap["spread_bps"],
            ob_snap["obi"] if ob_snap["obi"] is not None else float("nan"),
            tr_snap["cvd"], tr_snap["cvd_delta"], tr_snap["trade_count"],
            len(ob_snap["walls"]),
        )

    if offline:
        from .data.stream_replay import replay, synthetic_public_stream

        messages = synthetic_public_stream(
            symbol=settings.symbol, depth=depth, n_updates=updates)
        # Периодический снимок по ходу проигрывания (каждые ~10 сообщений).
        state = {"i": 0}

        def _on_update(ob, tr, _msg) -> None:
            state["i"] += 1
            if state["i"] % 20 == 0:
                _log_micro(f"replay#{state['i']}")

        replay(messages, order_book, trades, on_update=_on_update)
        _log_micro("итог")

        # Санити-проверки корректности микро-триггеров.
        snap = order_book.snapshot(obi_depth=obi_depth)
        if not snap["ready"]:
            log.error("Стакан не инициализирован снапшотом")
            return 1
        if snap["best_bid"] is None or snap["best_ask"] is None:
            log.error("Нет лучших цен в стакане")
            return 1
        if snap["best_bid"] >= snap["best_ask"]:
            log.error("LOOK/CROSS: best_bid >= best_ask (%.2f >= %.2f)",
                      snap["best_bid"], snap["best_ask"])
            return 1
        log.info("=== Этап 3 (offline) завершён успешно ✔ ===")
        return 0

    # --- LIVE ---
    from .data.ws_public import PublicWSFeed

    feed = PublicWSFeed(
        symbol=settings.symbol, depth=depth, category=settings.category,
        on_orderbook=lambda ob, _m: None, on_trade=lambda tr, _m: None,
    )
    # Обработчики фида — те же экземпляры, что логируем.
    feed.order_book = order_book
    feed.trades = trades
    try:
        feed.start()
    except ImportError:
        log.error("pybit не установлен — live-режим недоступен. "
                  "Используйте --offline или установите pybit.")
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("Не удалось открыть публичный WS: %s "
                  "(домен stream.bybit.com может быть закрыт политикой) — "
                  "используйте --offline", exc)
        return 1

    try:
        deadline = time.time() + duration
        while time.time() < deadline:
            time.sleep(1.0)
            _log_micro("live")
    except KeyboardInterrupt:
        log.info("Прервано пользователем")
    finally:
        feed.stop()

    log.info("=== Этап 3 (live) завершён ✔ ===")
    return 0 if order_book.ready else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trading Bot CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("stage1", help="Health-check Этапа 1 (Data Layer)")
    p1.add_argument("--demo-funds", action="store_true",
                    help="Пополнить демо-баланс перед проверкой")
    p1.add_argument("--limit", type=int, default=300,
                    help="Сколько свечей грузить на каждый TF")
    p1.add_argument("--offline", action="store_true",
                    help="Прогон на синтетических данных без сети")

    p2 = sub.add_parser("stage2", help="Расчёт индикаторов (Indicator Engine)")
    p2.add_argument("--limit", type=int, default=300,
                    help="Сколько свечей грузить на каждый TF")
    p2.add_argument("--offline", action="store_true",
                    help="Прогон на синтетических данных без сети")
    p2.add_argument("--stoch-rsi", action="store_true",
                    help="Использовать Stoch RSI вместо RSI")

    p3 = sub.add_parser("stage3", help="Order Book Module (OBI, стены, спред, CVD)")
    p3.add_argument("--offline", action="store_true",
                    help="Проигрывание синтетического потока без сети")
    p3.add_argument("--duration", type=float, default=15.0,
                    help="Длительность live-подписки в секундах")
    p3.add_argument("--depth", type=int, default=50,
                    help="Глубина стакана (1/50/200/500)")
    p3.add_argument("--obi-depth", type=int, default=25,
                    help="Сколько уровней брать для расчёта OBI")
    p3.add_argument("--cvd-window", type=int, default=60_000,
                    help="Окно расчёта дельты CVD, мс")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "stage1":
        return stage1_healthcheck(request_demo_funds=args.demo_funds,
                                  klines_limit=args.limit,
                                  offline=args.offline)
    if args.command == "stage2":
        return stage2_indicators(klines_limit=args.limit,
                                 offline=args.offline,
                                 use_stoch_rsi=args.stoch_rsi)
    if args.command == "stage3":
        return stage3_orderbook(offline=args.offline,
                                duration=args.duration,
                                depth=args.depth,
                                obi_depth=args.obi_depth,
                                cvd_window_ms=args.cvd_window)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
