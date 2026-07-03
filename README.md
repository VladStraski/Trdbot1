# Trdbot1 — торговый бот для Bybit (linear perpetuals)

Торговый бот на основе технического анализа с подтверждением через ордербук и
рыночный контекст. Старт — paper trading на **Bybit Demo Trading**, переход на
реальную торговлю только после успешного бэктеста и прогона на демо.

Полное ТЗ и порядок этапов — в `TRADING_BOT_SPEC.md` (раздел 9).

## Статус разработки

| Этап | Модуль | Статус |
|---|---|---|
| 1 | **Data Layer** (Bybit-клиент, klines, multi-TF агрегатор, storage) | ✅ реализован |
| 2 | **Indicator Engine** (ADX, EMA, RSI/StochRSI, MACD, ATR, BB, OBV, VWAP, свечи, свинги) | ✅ реализован |
| 3 | **Order Book Module** (OBI, стены, спред, CVD; WS + офлайн-реплей) | ✅ реализован |
| 4 | **Strategy Module** (confluence-скоринг, сигналы в лог; Market Context) | ✅ реализован |
| 5 | **Backtester** (та же Strategy; издержки, look-ahead-защита, метрики) | ✅ реализован |
| 6 | **Risk Manager** (sizing, плечо от liq-safety, стоп/тейк, издержки в R:R) | ✅ реализован |
| 7 | Execution Engine | ⏳ |
| 8 | Position Manager + синхронизация | ⏳ |
| 9 | Portfolio limits / kill switch | ⏳ |
| 10 | Notifications / мониторинг | ⏳ |

## Установка

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp trading_bot/config/.env.example trading_bot/config/.env
# заполнить .env: BYBIT_ENV=demo и ключи BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET
```

> **Python 3.11 vs 3.12.** `pandas-ta` в актуальных версиях требует Python 3.12+.
> Поэтому индикаторы (Этап 2) реализованы собственным модулем
> `indicators/engine.py` на pandas/numpy (формулы Уайлдера) — внешняя
> TA-библиотека не требуется.

## Запуск — Этап 1 (Data Layer)

Health-check: подключение к Bybit, загрузка klines по 3 таймфреймам,
наполнение multi-TF агрегатора и проверка выравнивания **без look-ahead**.

```bash
# Живой прогон (нужен доступ к api.bybit.com / api-demo.bybit.com):
python -m trading_bot.main stage1 --limit 300
python -m trading_bot.main stage1 --demo-funds     # + пополнить демо-баланс

# Офлайн-прогон на синтетических данных (без сети) — проверяет всю логику
# агрегатора и выравнивания:
python -m trading_bot.main stage1 --offline
```

## Запуск — Этап 2 (Indicator Engine)

Расчёт индикаторов по трём таймфреймам и снапшот последних значений
(EMA 50/200, ADX, RSI/StochRSI, MACD, ATR, Bollinger).

```bash
python -m trading_bot.main stage2 --limit 300
python -m trading_bot.main stage2 --offline          # без сети
python -m trading_bot.main stage2 --offline --stoch-rsi   # Stoch RSI вместо RSI
```

Индикаторы каузальны (значение бара i зависит только от баров ≤ i);
`swing_high/low` возвращают только подтверждённые экстремумы — без look-ahead.

## Запуск — Этап 3 (Order Book Module)

Реал-тайм микро-триггеры (раздел 5 ТЗ): **OBI** (Order Book Imbalance), **стены**
(крупные заявки), **спред** по стакану `orderbook.{depth}.{symbol}` и **CVD**
(Cumulative Volume Delta) по потоку сделок `publicTrade.{symbol}`.

```bash
# Живой прогон: публичный WS stream.bybit.com (общий для mainnet и demo):
python -m trading_bot.main stage3 --duration 20 --depth 50

# Офлайн: проигрывание детерминированного синтетического потока (без сети),
# через те же обработчики, что и live-контур:
python -m trading_bot.main stage3 --offline
```

Транспорт (`data/ws_public.py`, pybit) отделён от обработчиков
(`data/orderbook.py`, `data/trades_stream.py` — чистый stdlib), поэтому вся
логика проверяется офлайн проигрыванием потока (`data/stream_replay.py`), без
сети и без pandas/pybit.

## Запуск — Этап 4 (Strategy Module)

Confluence-скоринг (раздел 6 ТЗ): обязательный трендовый фильтр 4h (EMA50/200,
ADX>20) + очки по слоям 1h/15m, микро-контексту (OBI, CVD) и funding. Порог входа
по сумме очков (дефолт 6). Сигналы **только в лог** — реальные ордера с Этапа 7.

```bash
# Демонстрация скоринга на синтетических сценариях (без сети/pandas/pybit):
python -m trading_bot.main stage4 --offline
# Живая единичная оценка на последних закрытых барах:
python -m trading_bot.main stage4 --limit 400
```

Скоринг отделён от pandas: `strategy/` работает на `FeatureSnapshot` (скаляры) и
одинаков в live и бэктесте; извлечение признаков из свечей (pandas) — тонкий
адаптер `base_strategy.extract_features`. Market Context (`market_context/`:
funding, open interest, long/short ratio) — REST-обёртки с инъектируемым клиентом.

## Запуск — Этап 5 (Backtester)

Прогон **той же** `BaseStrategy` по истории с обязательным учётом издержек
(комиссии maker/taker + funding, раздел 7 ТЗ) и защитой от look-ahead: вход по
close сигнального бара, проверка SL/TP — только со следующего бара.

```bash
python -m trading_bot.main stage5 --offline     # синтетика (без сети/pandas)
python -m trading_bot.main stage5 --limit 800   # по живым klines
```

Движок работает на плоских `Bar`+`FeatureSnapshot` (stdlib); стоп/размер вынесены
в инъектируемые функции (`default_sl_tp`/`default_sizing`) — на Этапе 6 их
заменяет Risk Manager без переписывания движка.

## Запуск — Этап 6 (Risk Manager)

Единое риск-решение по сделке (раздел 7 ТЗ): fixed-fractional sizing, стоп по
ATR/swing, плечо от безопасности ликвидации (дистанция до ликвидации ≥ 3×
дистанции стопа, потолок 5x), тейк с поправкой на издержки (R:R после комиссий и
funding ≥ min_rr). Те же адаптеры (`as_sl_tp_fn`/`as_sizing_fn`) подключаются в
бэктест Этапа 5 — логика риска одна для live и истории.

```bash
python -m trading_bot.main stage6 --offline   # риск-решения + бэктест через RM
python -m trading_bot.main stage6 --limit 800 # решение по живому equity/сигналу
```

> **Опциональные зависимости.** `config/settings.py` и CLI написаны так, что
> `stage3`/`stage4 --offline` работают без установленных `pandas`/`pybit`/
> `python-dotenv` — тяжёлые пакеты импортируются лениво там, где реально нужны.

### ⚠️ Ограничение сетевой политики окружения

В managed-окружении Claude Code исходящий трафик к Bybit фильтруется egress-
прокси. Наблюдалось: REST-хосты `api.bybit.com` / `api-demo.bybit.com` и
demo-WS `stream-demo.bybit.com` — **403 на CONNECT**; публичный WS
`stream.bybit.com` — доступен. Поэтому:
- **живой** прогон Этапов 1–2 (нужен REST) выполняется там, где домен Bybit
  разрешён политикой; в managed-окружении — `--offline`;
- **Этап 3** в live-режиме использует только `stream.bybit.com` (публичные
  данные общие для mainnet/demo), а для проверки логики есть `--offline`.

> При закрытом доступе к PyPI зависимости (`pandas`, `pybit`, `pytest`) не
> устанавливаются — тогда доступен только stdlib-контур Этапа 3 и его тесты.

## Тесты

```bash
python -m pytest tests/ -q
```

Офлайн-тесты покрывают нормализацию klines, защиту от look-ahead в
multi-TF агрегаторе, индикаторы, а также Order Book Module (снапшот/дельта
стакана, OBI, стены, спред, CVD) — не требуют сети и ключей.

Тесты Этапа 3 (`tests/test_orderbook.py`, `tests/test_trades_stream.py`)
опираются только на stdlib и проходят даже без установленных `pandas`/`pybit`.

## Структура

```
trading_bot/
├── config/       settings.py, .env.example (demo/prod ключи раздельно)
├── data/         klines.py, multi_tf_aggregator.py, orderbook.py,
│                 trades_stream.py, ws_public.py, stream_replay.py
├── execution/    bybit_client.py (единый demo/prod интерфейс)
├── storage/      models.py (SQLite: сделки, сигналы, equity)
├── indicators/ market_context/ strategy/ risk/ backtest/ notifications/   # следующие этапы
├── logger.py     структурированные логи (консоль + .jsonl)
└── main.py       CLI
tests/            офлайн-тесты
```

## Безопасность

- API-ключи только в `.env` (в `.gitignore`), demo и prod — раздельно.
- Окружение выбирается **явно** через `BYBIT_ENV`, без автоопределения.
- Логируется каждое решение стратегии, не только сделки.
