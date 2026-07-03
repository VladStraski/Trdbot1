# Trdbot1 — торговый бот для Bybit (linear perpetuals)

Торговый бот на основе технического анализа с подтверждением через ордербук и
рыночный контекст. Старт — paper trading на **Bybit Demo Trading**, переход на
реальную торговлю только после успешного бэктеста и прогона на демо.

Полное ТЗ и порядок этапов — в `TRADING_BOT_SPEC.md` (раздел 9).

## Статус разработки

| Этап | Модуль | Статус |
|---|---|---|
| 1 | **Data Layer** (Bybit-клиент, klines, multi-TF агрегатор, storage) | ✅ реализован |
| 2 | Indicator Engine | ⏳ |
| 3 | Order Book Module | ⏳ |
| 4 | Strategy Module (confluence) | ⏳ |
| 5 | Backtester | ⏳ |
| 6 | Risk Manager | ⏳ |
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
> На 3.11 индикаторы (Этап 2) будут реализованы собственным модулем на
> pandas/numpy — внешняя TA-библиотека не обязательна.

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

### ⚠️ Ограничение сетевой политики окружения

В managed-окружении Claude Code исходящий трафик к `bybit.com` (и demo, и
mainnet) блокируется прокси (403 на CONNECT). Поэтому **живой** прогон
Этапа 1 и последующая проверка на demo-данных выполняются локально у
пользователя или в окружении, где домен Bybit разрешён сетевой политикой.
В managed-окружении для проверки логики используется `--offline`.

## Тесты

```bash
python -m pytest tests/ -q
```

Офлайн-тесты покрывают нормализацию klines и защиту от look-ahead в
multi-TF агрегаторе — не требуют сети и ключей.

## Структура

```
trading_bot/
├── config/       settings.py, .env.example (demo/prod ключи раздельно)
├── data/         klines.py, multi_tf_aggregator.py
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
