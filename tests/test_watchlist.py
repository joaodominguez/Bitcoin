import pandas as pd

from btcsim import watchlist as W


def test_mentions_known_assets():
    specs = W.mentions("Bitcoin ETF approval lifts ether while Apple rallies")
    assert "bitcoin" in specs
    assert "ethereum" in specs
    assert "stock:AAPL" in specs


def test_short_ticker_does_not_match_inside_words():
    assert "ethereum" not in W.mentions("The methodology was solid")
    assert "solana" not in W.mentions("A solution to the problem")


def test_bullish_headline_adds_asset():
    data = {"assets": []}
    summary = W.apply_headlines(
        data, ["Nvidia surges to a record high on huge gains"]
    )
    assert summary["added"] == ["stock:NVDA"]
    assert data["assets"][0]["spec"] == "stock:NVDA"
    assert data["assets"][0]["caution"] is False


def test_bearish_headline_sets_caution_without_removing():
    data = {"assets": [{"spec": "bitcoin", "caution": False}]}
    summary = W.apply_headlines(
        data, ["Bitcoin crash and hack spark a massive selloff"]
    )
    assert summary["cautioned"] == ["bitcoin"]
    assert data["assets"][0]["caution"] is True
    assert len(data["assets"]) == 1


def test_neutral_headline_does_not_add():
    data = {"assets": []}
    summary = W.apply_headlines(data, ["Bitcoin is a digital currency"])
    assert summary["added"] == []
    assert data["assets"] == []


def test_decide_sells_cautious_assets(tmp_path):
    path = tmp_path / "watchlist.json"
    W.save({
        "assets": [
            {"spec": "bitcoin", "caution": False},
            {"spec": "ethereum", "caution": True},
        ]
    }, path)
    idx = pd.date_range("2024-01-01", periods=40, freq="D")
    prices = pd.DataFrame({
        "bitcoin": [100 + i for i in range(40)],
        "ethereum": [50 + i * 0.2 for i in range(40)],
    }, index=idx)
    decision = W.decide(path, capital=10_000, prices=prices)
    assert "ethereum" not in decision["weights"]
    assert abs(sum(decision["weights"].values()) - 1) < 1e-6
    assert decision["cautious"] == ["ethereum"]
    assert any(a["action"] == "BUY" for a in decision["actions"])


def test_second_decision_can_hold(tmp_path):
    path = tmp_path / "watchlist.json"
    W.save({"assets": [{"spec": "bitcoin", "caution": False}]}, path)
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    prices = pd.DataFrame({"bitcoin": range(100, 110)}, index=idx)
    first = W.decide(path, prices=prices)
    second = W.decide(path, prices=prices)
    assert first["actions"][0]["action"] == "BUY"
    assert second["actions"][0]["action"] == "HOLD"
