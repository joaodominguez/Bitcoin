import pandas as pd

from btcsim import watchlist as W


def test_collect_titles_keeps_every_source():
    titles = W.collect_titles(
        [["crypto %s" % i for i in range(20)], ["Gold rallies", "Crude oil falls"]],
        per_feed=12,
        limit=48,
    )
    assert len(titles) == 14
    assert "Gold rallies" in titles
    assert "Crude oil falls" in titles


def test_gold_and_oil_are_recognized():
    assert "stock:GLD" in W.mentions("Gold price hits a record high")
    assert "stock:USO" in W.mentions("Crude oil plunges as demand drops")
    assert W.mentions("The methodology was solid") == []


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


def test_rising_pattern_suggests_keeping():
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    values = [100 + i * 1.2 for i in range(40)]
    base = values[-1]
    for i in range(40):
        values.append(base + (i % 6 - 2) * 1.5 + i * 0.15)
    prices = pd.DataFrame({"bitcoin": values}, index=idx)
    study = W.study_patterns(prices)[0]
    assert study["trend"].startswith("alta")
    assert study["rsi"] < 70
    assert study["stance"] == "manter ou reforçar aos poucos"


def test_stretched_rally_says_do_not_chase():
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    # Flat, then a vertical jump so RSI saturates.
    values = [100.0] * 60 + [100 + (i + 1) * 8 for i in range(20)]
    prices = pd.DataFrame({"bitcoin": values}, index=idx)
    study = W.study_patterns(prices)[0]
    assert study["rsi"] >= 70
    assert study["stance"] == "não perseguir"


def test_downtrend_says_reduce():
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    prices = pd.DataFrame({"bitcoin": [200 - i * 1.2 for i in range(80)]}, index=idx)
    study = W.study_patterns(prices)[0]
    assert study["trend"].startswith("baixa")
    assert study["stance"] in {"reduzir", "não entrar com tudo"}


def test_decision_includes_advice(tmp_path):
    path = tmp_path / "watchlist.json"
    W.save({"assets": [{"spec": "bitcoin", "caution": False}]}, path)
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    prices = pd.DataFrame({"bitcoin": [100 + i * 0.5 for i in range(80)]}, index=idx)
    decision = W.decide(path, prices=prices)
    assert "bitcoin" in decision["advice"]
    assert decision["patterns"][0]["asset"] == "bitcoin"
    assert decision["capital_inicial"] == 10000
    assert decision["capital_atual"] == 10000
    assert decision["previsao"] > 0


def test_second_decision_can_hold(tmp_path):
    path = tmp_path / "watchlist.json"
    W.save({"assets": [{"spec": "bitcoin", "caution": False}]}, path)
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    prices = pd.DataFrame({"bitcoin": range(100, 110)}, index=idx)
    first = W.decide(path, prices=prices)
    second = W.decide(path, prices=prices)
    assert first["actions"][0]["action"] == "BUY"
    assert second["actions"][0]["action"] == "HOLD"
