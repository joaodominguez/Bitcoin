import pandas as pd
import pytest

from btcsim import data as d


def test_parse_asset_defaults_to_crypto():
    assert d.parse_asset("bitcoin") == ("crypto", "bitcoin")
    assert d.parse_asset("crypto:ethereum") == ("crypto", "ethereum")
    assert d.parse_asset("stock:AAPL") == ("stock", "AAPL")
    assert d.parse_asset("  stock: MSFT ") == ("stock", "MSFT")


def test_asset_label():
    assert d.asset_label("bitcoin") == "BTC"
    assert d.asset_label("crypto:ethereum") == "ETH"
    assert d.asset_label("stock:aapl") == "AAPL"


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
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return _FakeResp(self._payload)


def _yahoo_payload(dates, prices, currency="USD"):
    ts = [int(pd.Timestamp(x).timestamp()) for x in dates]
    return {
        "chart": {
            "result": [
                {
                    "meta": {"currency": currency, "symbol": "AAPL"},
                    "timestamp": ts,
                    "indicators": {
                        "quote": [{"close": prices}],
                        "adjclose": [{"adjclose": prices}],
                    },
                }
            ]
        }
    }


def test_fetch_stock_parses_yahoo(tmp_path):
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    prices = [100.0, 101.0, 102.0, 103.0, 104.0]
    sess = _FakeSession(_yahoo_payload(dates, prices))
    series = d.fetch_stock("AAPL", days=30, cache_dir=tmp_path, session=sess)
    assert series.currency == "usd"
    assert series.coin == "AAPL"
    assert float(series.frame["price"].iloc[-1]) == 104.0
    assert len(series) >= 5


def test_fetch_stock_uses_cache(tmp_path):
    dates = pd.date_range("2024-01-01", periods=3, freq="D")
    sess = _FakeSession(_yahoo_payload(dates, [10.0, 11.0, 12.0]))
    d.fetch_stock("AAPL", days=30, cache_dir=tmp_path, session=sess)
    n_calls = len(sess.calls)
    # Second call within max_age should hit the cache, not the session.
    d.fetch_stock("AAPL", days=30, cache_dir=tmp_path, session=sess)
    assert len(sess.calls) == n_calls


def test_fetch_asset_dispatch_stock(tmp_path):
    dates = pd.date_range("2024-01-01", periods=4, freq="D")
    sess = _FakeSession(_yahoo_payload(dates, [1.0, 2.0, 3.0, 4.0]))
    series = d.fetch_asset("stock:AAPL", days=30, cache_dir=tmp_path, session=sess)
    assert series.coin == "AAPL"


def test_fetch_asset_unknown_type_raises():
    with pytest.raises(ValueError):
        d.fetch_asset("forex:EURUSD")
