"""Тесты confluence-скоринга (Этап 4). Чистый stdlib — без pandas/pybit/сети."""

from trading_bot.strategy.confluence_scorer import ConfluenceScorer
from trading_bot.strategy.model import LONG, SHORT, FeatureSnapshot, ScoreConfig


def _strong_long(**over) -> FeatureSnapshot:
    base = dict(
        price=30000.0, ts=1, ema_fast=31000.0, ema_slow=30000.0, adx=30.0,
        osc=35.0, osc_prev=28.0, macd_hist=0.5, macd_hist_prev=0.2,
        bull_pattern=True, at_swing_low=True, volume=150.0, volume_avg=100.0,
        obi=0.3, cvd_delta=1.2, funding_rate=0.0001,
    )
    base.update(over)
    return FeatureSnapshot(**base)


def test_mandatory_filter_blocks_when_no_trend():
    f = _strong_long(adx=15.0)  # ADX ниже порога -> тренда нет
    sig = ConfluenceScorer().evaluate(f)
    assert sig.direction is None
    assert sig.entered is False
    assert sig.score == 0


def test_strong_long_enters():
    sig = ConfluenceScorer().evaluate(_strong_long())
    assert sig.direction == LONG
    assert sig.entered is True
    # 2(osc) +1(macd) +2(pattern) +1(vol) +2(micro) +1(funding) = 9
    assert sig.score == 9


def test_strong_short_enters():
    f = FeatureSnapshot(
        price=30000.0, ts=2, ema_fast=29000.0, ema_slow=30000.0, adx=28.0,
        osc=65.0, osc_prev=72.0, macd_hist=-0.5, macd_hist_prev=-0.2,
        bear_pattern=True, at_swing_high=True, volume=150.0, volume_avg=100.0,
        obi=-0.3, cvd_delta=-1.2, funding_rate=-0.0001,
    )
    sig = ConfluenceScorer().evaluate(f)
    assert sig.direction == SHORT
    assert sig.entered is True
    assert sig.score == 9


def test_weak_long_below_threshold():
    # Тренд есть, но нет ни осц-выхода, ни паттерна, ни объёма, ни микро.
    f = _strong_long(osc=50.0, osc_prev=49.0, macd_hist=0.1, macd_hist_prev=0.2,
                     bull_pattern=False, at_swing_low=False, volume=90.0,
                     obi=None, cvd_delta=None)
    sig = ConfluenceScorer().evaluate(f)
    assert sig.direction == LONG
    assert sig.entered is False
    assert sig.score < 6


def test_extreme_funding_penalizes_long():
    # Всё то же, но funding экстремально положительный -> штраф -2 вместо +1.
    strong = ConfluenceScorer().evaluate(_strong_long())
    penalized = ConfluenceScorer().evaluate(_strong_long(funding_rate=0.01))
    assert penalized.score == strong.score - 3  # +1 -> -2 = разница 3
    labels = [p.label for p in penalized.breakdown]
    assert "funding_extreme" in labels


def test_micro_absent_no_micro_points():
    f = _strong_long(obi=None, cvd_delta=None)
    sig = ConfluenceScorer().evaluate(f)
    labels = [p.label for p in sig.breakdown]
    assert "micro_bull" not in labels
    assert sig.score == 7  # 9 - 2 (нет микро)


def test_threshold_configurable():
    cfg = ScoreConfig(entry_threshold=10)  # выше достижимого в сценарии
    sig = ConfluenceScorer(cfg).evaluate(_strong_long())
    assert sig.entered is False
    assert sig.threshold == 10


def test_osc_must_cross_not_just_be_low():
    # osc уже низкий И остаётся низким (нет пересечения вверх) -> нет очков.
    f = _strong_long(osc=25.0, osc_prev=24.0)
    sig = ConfluenceScorer().evaluate(f)
    labels = [p.label for p in sig.breakdown]
    assert "osc_exit_oversold" not in labels
