"""Точка входа бота.

На текущем шаге разработки реализован Этап 1 (Data Layer): проверка подключения
к Bybit, загрузка klines по трём таймфреймам, наполнение multi-TF агрегатора и
демонстрация корректного (без look-ahead) выравнивания старшего TF к младшему.

Запуск:
    python -m trading_bot.main stage1              # health-check Этапа 1
    python -m trading_bot.main stage1 --demo-funds # + пополнить демо-баланс
"""

from __future__ import annotations

import argparse
import time

import pandas as pd

from .config.settings import TF_MS, load_settings
from .data.klines import fetch_klines
from .data.multi_tf_aggregator import MultiTFAggregator
from .execution.bybit_client import BybitClient
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

    tfs = [settings.tf_context, settings.tf_signal, settings.tf_trigger]
    agg = MultiTFAggregator(timeframes=tfs)
    now = _now_ms()

    if offline:
        for tf in tfs:
            agg.seed(tf, _synthetic_klines(tf, klines_limit, now))
    else:
        client = BybitClient(settings)

        # 1) Доступность API (публичный эндпоинт, работает без ключей).
        if not client.ping():
            log.error("Bybit API недоступен — проверьте сеть/прокси. "
                      "Если домен bybit заблокирован политикой окружения, "
                      "используйте офлайн-прогон: python -m trading_bot.main stage1 --offline")
            return 1
        log.info("API доступен ✔")

        # 2) Баланс / equity (требует ключей). Без ключей — пропускаем, не падаем.
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

        # 3) klines по трём таймфреймам -> агрегатор.
        for tf in tfs:
            df = fetch_klines(client, tf, limit=klines_limit,
                              only_closed=True, now_ms=now)
            if df.empty:
                log.error("Пустые klines для TF=%s", tf)
                return 1
            agg.seed(tf, df)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "stage1":
        return stage1_healthcheck(request_demo_funds=args.demo_funds,
                                  klines_limit=args.limit,
                                  offline=args.offline)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
