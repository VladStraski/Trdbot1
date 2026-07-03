"""Тесты уведомлений и мониторинга (Этап 10). Инъекция каналов — без сети."""

from trading_bot.notifications.monitor import Heartbeat, Notifier
from trading_bot.notifications.telegram import TelegramChannel


class _CaptureChannel:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)
        return True


class _Sig:
    def __init__(self, entered, direction="long", score=8, price=30000.0, reason="ok"):
        self.entered = entered
        self.direction = direction
        self.score = score
        self.price = price
        self.reason = reason


class _Breaker:
    def __init__(self, name, detail, hard):
        self.name = name
        self.detail = detail
        self.hard = hard


def test_notifier_fans_out_to_channels():
    cap = _CaptureChannel()
    n = Notifier([cap])
    n.notify("привет")
    assert cap.sent == ["привет"]           # доп. канал получил
    # LogChannel присутствует всегда (не роняет).
    assert len(n.channels) == 2


def test_signal_formatting():
    cap = _CaptureChannel()
    Notifier([cap]).signal(_Sig(entered=True, direction="long", score=8))
    assert "СИГНАЛ" in cap.sent[0] and "long" in cap.sent[0]
    cap2 = _CaptureChannel()
    Notifier([cap2]).signal(_Sig(entered=False, reason="нет тренда"))
    assert "нет входа" in cap2.sent[0]


def test_breaker_formatting_marks_hard():
    cap = _CaptureChannel()
    Notifier([cap]).breaker(_Breaker("max_drawdown", "просадка 20%", hard=True))
    assert "HARD" in cap.sent[0] and "max_drawdown" in cap.sent[0]


def test_failing_channel_does_not_break_others():
    class Boom:
        def send(self, text):
            raise RuntimeError("boom")
    cap = _CaptureChannel()
    n = Notifier([Boom(), cap])
    n.notify("ok")                          # не должно упасть
    assert cap.sent == ["ok"]


def test_telegram_uses_injected_sender():
    captured = {}
    def fake_sender(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return True
    ch = TelegramChannel("TOKEN", "CHAT", sender=fake_sender)
    assert ch.enabled is True
    assert ch.send("hi") is True
    assert "botTOKEN/sendMessage" in captured["url"]
    assert captured["payload"] == {"chat_id": "CHAT", "text": "hi"}


def test_telegram_disabled_without_credentials():
    ch = TelegramChannel("", "", sender=lambda u, p: True)
    assert ch.enabled is False
    assert ch.send("hi") is False


def test_heartbeat_staleness():
    hb = Heartbeat("data")
    assert hb.is_stale(now_ms=1000, max_gap_ms=500) is True   # пульса не было
    hb.beat(1000)
    assert hb.is_stale(now_ms=1400, max_gap_ms=500) is False  # 400 < 500
    assert hb.is_stale(now_ms=1600, max_gap_ms=500) is True   # 600 > 500
