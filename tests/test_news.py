import pandas as pd

from btcsim.news import (
    KeywordSentimentProvider,
    NeutralProvider,
    score_text,
)


def test_score_text_bullish_positive():
    assert score_text("Bitcoin surges to record high on ETF approval") > 0


def test_score_text_bearish_negative():
    assert score_text("Crypto crash: exchange hacked in massive selloff") < 0


def test_score_text_neutral_zero():
    assert score_text("Bitcoin is a digital currency used worldwide") == 0.0


def test_neutral_provider_all_zero():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    s = NeutralProvider().daily_sentiment(idx)
    assert (s == 0.0).all()
    assert len(s) == 10


def test_keyword_provider_aligns_to_index():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    headlines = [
        (idx[1], "massive rally and record inflows, bullish adoption"),
        (idx[3], "crash and selloff, bearish fear"),
    ]
    s = KeywordSentimentProvider(headlines).daily_sentiment(idx)
    assert s.iloc[0] == 0.0
    assert s.iloc[1] > 0
    assert s.iloc[3] < 0
