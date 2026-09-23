import pandas as pd

from btcsim.news import FearGreedProvider
from btcsim.simulator import Simulator
from btcsim.strategies import SentimentStrategy


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload

    def get(self, *args, **kwargs):
        return _FakeResp(self._payload)


def _payload_for(index, values):
    data = []
    for date, val in zip(index, values):
        ts = int(pd.Timestamp(date).timestamp())
        data.append({"value": str(val), "timestamp": str(ts)})
    return {"data": data, "metadata": {"error": None}}


def test_feargreed_maps_to_minus_one_to_one(tmp_path):
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    payload = _payload_for(idx, [0, 25, 50, 75, 100])
    provider = FearGreedProvider(session=_FakeSession(payload), cache_dir=tmp_path)
    s = provider.daily_sentiment(idx)
    assert s.iloc[0] == -1.0   # extreme fear
    assert s.iloc[2] == 0.0    # neutral
    assert s.iloc[4] == 1.0    # extreme greed
    assert (s >= -1).all() and (s <= 1).all()


def test_feargreed_falls_back_to_neutral_on_error(tmp_path):
    class _BoomSession:
        def get(self, *a, **k):
            raise RuntimeError("network down")

    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    provider = FearGreedProvider(session=_BoomSession(), cache_dir=tmp_path)
    s = provider.daily_sentiment(idx)
    assert (s == 0.0).all()


def test_contrarian_buys_on_fear(volatile_series, tmp_path):
    idx = volatile_series.frame.index
    # Extreme fear early, extreme greed later.
    values = [10] * 20 + [90] * (len(idx) - 20)
    payload = _payload_for(idx, values)
    provider = FearGreedProvider(session=_FakeSession(payload), cache_dir=tmp_path)
    sim = Simulator(
        volatile_series, initial_cash=10_000.0, fee_rate=0.0,
        sentiment_provider=provider,
    )
    result = sim.run(SentimentStrategy(threshold=0.5, smooth=1, contrarian=True))
    sides = list(result.trades["side"])
    assert sides[0] == "BUY"     # bought during fear
    assert "SELL" in sides       # sold during greed
