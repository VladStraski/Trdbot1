"""Тесты Market Context (funding/OI/LSR). Парсинг на фейковом клиенте — без сети."""

from trading_bot.market_context.funding import FundingContext
from trading_bot.market_context.open_interest import OpenInterestContext
from trading_bot.market_context.long_short_ratio import LongShortRatioContext


class _FakeClient:
    def __init__(self, ticker=None, oi=None, lsr=None):
        self._ticker = ticker or {}
        self._oi = oi or []
        self._lsr = lsr or []

    def get_tickers(self):
        return self._ticker

    def get_open_interest(self):
        return self._oi

    def get_long_short_ratio(self):
        return self._lsr


def test_funding_current_rate():
    fc = FundingContext(_FakeClient(ticker={"fundingRate": "0.00012",
                                            "lastPrice": "30000"}))
    assert fc.current_rate() == 0.00012
    snap = fc.snapshot()
    assert snap["funding_rate"] == 0.00012
    assert snap["last_price"] == 30000.0


def test_funding_missing_is_none():
    assert FundingContext(_FakeClient(ticker={})).current_rate() is None
    assert FundingContext(_FakeClient(ticker={"fundingRate": ""})).current_rate() is None


def test_open_interest_latest_and_trend():
    # Bybit отдаёт новые сверху: 120 (новее) ... 100 (старее).
    oi = [{"openInterest": "120"}, {"openInterest": "110"}, {"openInterest": "100"}]
    oc = OpenInterestContext(_FakeClient(oi=oi))
    assert oc.latest() == 120.0                 # самый новый
    assert round(oc.trend(), 4) == round((120 - 100) / 100, 4)  # рост от старого к новому


def test_open_interest_empty():
    oc = OpenInterestContext(_FakeClient(oi=[]))
    assert oc.latest() is None
    assert oc.trend() is None


def test_long_short_ratio():
    lsr = [{"buyRatio": "0.6", "sellRatio": "0.4"}]
    lc = LongShortRatioContext(_FakeClient(lsr=lsr))
    assert lc.latest() == 0.6 / 0.4


def test_long_short_ratio_empty():
    assert LongShortRatioContext(_FakeClient(lsr=[])).latest() is None
