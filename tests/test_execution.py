"""Тесты Execution Engine (Этап 7). Фейковый HTTP вместо pybit — без сети/ключей."""

from trading_bot.config.settings import Settings
from trading_bot.execution.bybit_client import BybitClient, _fmt
from trading_bot.execution.execution_engine import ExecutionEngine
from trading_bot.risk.risk_manager import RiskManager, RiskParams
from trading_bot.strategy.model import LONG, SHORT


class FakeHTTP:
    """Записывает вызовы и возвращает ответы в форме Bybit v5."""

    def __init__(self):
        self.calls = []

    def _ok(self, result=None):
        return {"retCode": 0, "retMsg": "OK", "result": result or {}}

    def switch_margin_mode(self, **kw):
        self.calls.append(("switch_margin_mode", kw))
        return self._ok()

    def set_leverage(self, **kw):
        self.calls.append(("set_leverage", kw))
        return self._ok()

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return self._ok({"orderId": "OID1", "orderLinkId": kw.get("orderLinkId", "")})

    def cancel_all_orders(self, **kw):
        self.calls.append(("cancel_all_orders", kw))
        return self._ok({"list": []})


def _settings():
    return Settings(env="demo", api_key="", api_secret="", symbol="BTCUSDT")


def _client_and_http():
    http = FakeHTTP()
    return BybitClient(_settings(), http=http), http


def _approved_long():
    return RiskManager(RiskParams()).decide(LONG, entry=30_000.0, atr=150.0,
                                            equity=10_000.0)


def test_fmt_no_exponent():
    assert _fmt(0.0001) == "0.0001"
    assert _fmt(50.0) == "50"
    assert _fmt(0.0) == "0"


def test_open_sends_entry_with_attached_sl_tp():
    client, http = _client_and_http()
    eng = ExecutionEngine(client, _settings())
    decision = _approved_long()
    res = eng.open_from_decision(decision)

    assert res.ok and res.action == "open" and res.side == "Buy"
    assert res.order_id == "OID1"
    names = [c[0] for c in http.calls]
    # Маржа/плечо выставлены до входа, затем ордер.
    assert "switch_margin_mode" in names
    assert names[-1] == "place_order"
    po = dict(http.calls[-1][1])
    assert po["side"] == "Buy"
    assert po["orderType"] == "Market"
    assert "reduceOnly" not in po                 # вход не reduce-only
    assert "stopLoss" in po and "takeProfit" in po  # SL/TP на бирже, не в памяти
    assert po["qty"] == _fmt(decision.qty)


def test_close_is_reduce_only_opposite_side():
    client, http = _client_and_http()
    eng = ExecutionEngine(client, _settings())
    res = eng.close_position(LONG, qty=0.2)
    assert res.ok and res.side == "Sell"
    po = dict(http.calls[-1][1])
    assert po["reduceOnly"] is True
    assert po["side"] == "Sell"
    assert po["orderType"] == "Market"


def test_short_close_buys():
    client, http = _client_and_http()
    ExecutionEngine(client, _settings()).close_position(SHORT, qty=0.1)
    assert dict(http.calls[-1][1])["side"] == "Buy"


def test_dry_run_sends_nothing():
    client, http = _client_and_http()
    eng = ExecutionEngine(client, _settings(), dry_run=True)
    res = eng.open_from_decision(_approved_long())
    assert res.ok and res.dry_run is True
    assert http.calls == []                        # на биржу ничего не ушло


def test_rejected_decision_not_sent():
    client, http = _client_and_http()
    eng = ExecutionEngine(client, _settings())
    rejected = RiskManager().decide(LONG, 30_000.0, 150.0, equity=0.0)
    res = eng.open_from_decision(rejected)
    assert res.ok is False
    assert http.calls == []
