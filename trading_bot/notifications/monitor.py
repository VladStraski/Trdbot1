"""Уведомления и мониторинг (Этап 10 ТЗ).

`Notifier` веерно рассылает события по каналам (лог — всегда, Telegram —
опционально) с единым форматированием для сигналов, ордеров, circuit breakers и
ошибок. `Heartbeat` отслеживает «пульс» контуров (свежесть данных/циклов) —
основа авто-детекции зависания (совместно с kill switch Этапа 9).

Чистый stdlib (каналы инъектируются) — тестируется офлайн.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from ..logger import get_logger

log = get_logger("notifications.monitor")


class Channel(Protocol):
    def send(self, text: str) -> bool: ...


class LogChannel:
    """Канал по умолчанию — структурный лог (есть всегда)."""

    def send(self, text: str) -> bool:
        log.info("NOTIFY %s", text)
        return True


class Notifier:
    def __init__(self, channels: Optional[list[Channel]] = None) -> None:
        # LogChannel присутствует всегда; прочие каналы добавляются поверх.
        self.channels: list[Channel] = [LogChannel()]
        if channels:
            self.channels.extend(channels)

    def notify(self, text: str) -> None:
        for ch in self.channels:
            try:
                ch.send(text)
            except Exception as exc:  # noqa: BLE001 - один канал не должен ронять остальные
                log.warning("Канал уведомления упал: %s", exc)

    # --- типовые события (единое форматирование) ---
    def signal(self, sig) -> None:
        if getattr(sig, "entered", False):
            self.notify(f"🟢 СИГНАЛ {sig.direction} score={sig.score} @ {sig.price}")
        else:
            self.notify(f"⚪️ нет входа: {sig.reason}")

    def order(self, result) -> None:
        state = "OK" if getattr(result, "ok", False) else "FAIL"
        self.notify(f"📈 ОРДЕР {result.action} {result.side} qty={result.qty} [{state}]")

    def breaker(self, event) -> None:
        mark = "⛔️ HARD" if getattr(event, "hard", False) else "⚠️"
        self.notify(f"{mark} BREAKER {event.name}: {event.detail}")

    def error(self, message: str) -> None:
        self.notify(f"❗️ ОШИБКА: {message}")


@dataclass
class Heartbeat:
    """Пульс контура: свежесть последнего события (данных/цикла)."""

    name: str = "loop"
    last_ms: Optional[int] = None

    def beat(self, ts_ms: int) -> None:
        self.last_ms = ts_ms

    def is_stale(self, now_ms: int, max_gap_ms: int) -> bool:
        """True, если с последнего пульса прошло больше max_gap_ms (или его не было)."""
        if self.last_ms is None:
            return True
        return (now_ms - self.last_ms) > max_gap_ms


def build_notifier(settings) -> Notifier:
    """Собрать Notifier из конфига: + Telegram, если заданы токен и chat_id."""
    channels: list[Channel] = []
    token = getattr(settings, "telegram_bot_token", None)
    chat = getattr(settings, "telegram_chat_id", None)
    if token and chat:
        from .telegram import TelegramChannel
        channels.append(TelegramChannel(token, chat))
        log.info("Telegram-уведомления включены")
    return Notifier(channels)
