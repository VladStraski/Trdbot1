"""Единый клиент Bybit — общий интерфейс для demo и prod.

Требование ТЗ (разделы 3, 10): код execution-слоя идентичен для обоих окружений,
различаются только base URL и ключи, выбор — ЯВНО через конфиг (`BYBIT_ENV`).

pybit нативно поддерживает Demo Trading через `demo=True`
(base URL -> https://api-demo.bybit.com). На Этапе 1 используются только
read-методы (баланс, klines) и пополнение демо-баланса; торговые методы
(create/amend/cancel) добавляются на Этапе 7.
"""

from __future__ import annotations

from typing import Any, Optional

from pybit.unified_trading import HTTP

from ..config.settings import Settings
from ..logger import get_logger

log = get_logger("execution.bybit_client")


class BybitClientError(RuntimeError):
    """Ошибка на уровне Bybit API (retCode != 0) либо сетевой сбой."""


class BybitClient:
    """Тонкая обёртка над pybit HTTP с единым demo/prod поведением."""

    def __init__(self, settings: Settings, http: Optional[HTTP] = None) -> None:
        self.settings = settings
        # http можно подменить в тестах (fake), иначе создаём реальную сессию.
        if http is not None:
            self._http = http
        else:
            self._http = HTTP(
                testnet=False,
                demo=settings.is_demo,      # demo=True -> api-demo.bybit.com
                api_key=settings.api_key or None,
                api_secret=settings.api_secret or None,
            )
        log.info(
            "BybitClient инициализирован (env=%s, demo=%s, symbol=%s)",
            settings.env, settings.is_demo, settings.symbol,
        )

    # ------------------------------------------------------------------ #
    #  Внутреннее: единая обработка ответа Bybit v5 { retCode, retMsg, result }
    # ------------------------------------------------------------------ #
    @staticmethod
    def _unwrap(resp: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(resp, dict):
            raise BybitClientError(f"Неожиданный ответ API: {resp!r}")
        ret_code = resp.get("retCode")
        if ret_code not in (0, None):
            raise BybitClientError(
                f"Bybit API retCode={ret_code}: {resp.get('retMsg')}"
            )
        return resp.get("result", {}) or {}

    # ------------------------------------------------------------------ #
    #  Аккаунт
    # ------------------------------------------------------------------ #
    def get_wallet_balance(self, account_type: str = "UNIFIED",
                           coin: Optional[str] = None) -> dict[str, Any]:
        """Баланс кошелька. Для linear-perpetual используется UNIFIED-аккаунт."""
        kwargs: dict[str, Any] = {"accountType": account_type}
        if coin:
            kwargs["coin"] = coin
        return self._unwrap(self._http.get_wallet_balance(**kwargs))

    def get_equity(self, coin: str = "USDT", account_type: str = "UNIFIED") -> float:
        """Текущий equity в указанной монете (для sizing/risk)."""
        result = self.get_wallet_balance(account_type=account_type)
        for acct in result.get("list", []):
            # На уровне аккаунта Bybit отдаёт totalEquity; на уровне монеты — equity.
            for c in acct.get("coin", []):
                if c.get("coin") == coin:
                    equity = c.get("equity") or c.get("walletBalance") or "0"
                    return float(equity or 0)
            total = acct.get("totalEquity")
            if total not in (None, ""):
                return float(total)
        return 0.0

    def request_demo_funds(self) -> dict[str, Any]:
        """Пополнить демо-баланс (только demo). Эндпоинт /v5/account/demo-apply-money."""
        if not self.settings.is_demo:
            raise BybitClientError("request_demo_funds доступен только в demo-окружении")
        return self._unwrap(self._http.request_demo_trading_funds())

    # ------------------------------------------------------------------ #
    #  Рыночные данные
    # ------------------------------------------------------------------ #
    def get_kline(self, interval: str, limit: int = 200,
                  start: Optional[int] = None, end: Optional[int] = None,
                  symbol: Optional[str] = None,
                  category: Optional[str] = None) -> list[list[str]]:
        """Сырые klines Bybit (список списков, новые сверху).

        interval — интервал Bybit ('15','60','240',...), не человекочитаемый TF.
        """
        kwargs: dict[str, Any] = {
            "category": category or self.settings.category,
            "symbol": symbol or self.settings.symbol,
            "interval": interval,
            "limit": limit,
        }
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
        result = self._unwrap(self._http.get_kline(**kwargs))
        return result.get("list", [])

    def get_instrument_info(self, symbol: Optional[str] = None,
                            category: Optional[str] = None) -> dict[str, Any]:
        """Спецификация инструмента: tickSize, qtyStep, min/max leverage и т.д."""
        result = self._unwrap(self._http.get_instruments_info(
            category=category or self.settings.category,
            symbol=symbol or self.settings.symbol,
        ))
        items = result.get("list", [])
        return items[0] if items else {}

    def ping(self) -> bool:
        """Быстрая проверка доступности API (server time)."""
        try:
            self._http.get_server_time()
            return True
        except Exception as exc:  # noqa: BLE001 - диагностический ping
            log.warning("ping() не удался: %s", exc)
            return False
