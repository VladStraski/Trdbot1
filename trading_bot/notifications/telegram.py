"""Telegram-уведомления (Этап 10 ТЗ, опционально).

Транспорт инъектируется (`sender`), поэтому форматирование и логика уведомлений
тестируются офлайн без сети. По умолчанию отправка идёт через Telegram Bot API
(`requests`, ленивый импорт). Токен/chat_id — только из конфига (`.env`).
"""

from __future__ import annotations

from typing import Callable, Optional

from ..logger import get_logger

log = get_logger("notifications.telegram")

# sender(url, payload) -> bool
Sender = Callable[[str, dict], bool]


def _default_sender(url: str, payload: dict) -> bool:
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as exc:  # noqa: BLE001 - сеть/зависимость best-effort
        log.warning("Не удалось отправить в Telegram: %s", exc)
        return False


class TelegramChannel:
    """Канал уведомлений в Telegram. Метод send(text) совместим с Notifier."""

    def __init__(self, bot_token: str, chat_id: str,
                 sender: Optional[Sender] = None) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._sender = sender or _default_sender

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        return self._sender(url, {"chat_id": self.chat_id, "text": text})
