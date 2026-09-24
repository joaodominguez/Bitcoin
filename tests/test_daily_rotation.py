"""Tests for UI layout persistence and daily rotation weights."""

from pathlib import Path

import pandas as pd

from btcsim import layout as L
from btcsim import watchlist as W


def test_layout_roundtrip(tmp_path: Path):
    saved = L.save_layout(tmp_path, {"card_order": ["AAPL", "MSFT", "SPY"]})
    assert saved["card_order"] == ["AAPL", "MSFT", "SPY"]
    loaded = L.load_layout(tmp_path)
    assert loaded["card_order"] == ["AAPL", "MSFT", "SPY"]


def test_study_patterns_include_short_horizon():
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    # Rising series → positive 1d/5d edge
    prices = pd.DataFrame({"AAPL": [100 + i * 0.8 for i in range(80)]}, index=idx)
    study = W.study_patterns(prices)[0]
    assert "return_1d_pct" in study
    assert "return_5d_pct" in study
    assert "edge_score" in study
    assert study["class"] == "tech"


def test_daily_rotation_picks_across_classes():
    studies = [
        {"asset": "NVDA", "edge_score": 12, "stance": "manter ou reforçar aos poucos", "trend": "alta confirmada", "return_1d_pct": 1.2, "class": "tech"},
        {"asset": "AAPL", "edge_score": 9, "stance": "manter ou reforçar aos poucos", "trend": "alta curta", "return_1d_pct": 0.6, "class": "tech"},
        {"asset": "SPY", "edge_score": 5, "stance": "manter ou reforçar aos poucos", "trend": "alta curta", "return_1d_pct": 0.3, "class": "etf"},
        {"asset": "GLD", "edge_score": 4, "stance": "manter ou reforçar aos poucos", "trend": "alta curta", "return_1d_pct": 0.4, "class": "commodity"},
        {"asset": "bitcoin", "edge_score": 8, "stance": "manter ou reforçar aos poucos", "trend": "alta curta", "return_1d_pct": 1.0, "class": "crypto"},
        {"asset": "USO", "edge_score": -5, "stance": "reduzir", "trend": "baixa curta", "return_1d_pct": -1.2, "class": "commodity"},
    ]
    weights = W.daily_rotation_weights(
        studies, {"NVDA", "AAPL", "SPY", "GLD", "bitcoin", "USO"}
    )
    assert "NVDA" in weights or "AAPL" in weights
    assert "SPY" in weights or "GLD" in weights
    assert "USO" not in weights
    assert sum(weights.values()) <= 1.0 - W.MIN_CASH_WEIGHT + 1e-6
    crypto = sum(v for k, v in weights.items() if k.lower() in W.CRYPTO_NAMES)
    assert crypto <= W.MAX_CRYPTO_WEIGHT + 1e-6


def test_decide_uses_daily_rotation(tmp_path: Path):
    path = tmp_path / "watchlist.json"
    W.save(
        {
            "assets": [
                {"spec": "stock:AAPL", "caution": False},
                {"spec": "stock:MSFT", "caution": False},
                {"spec": "stock:SPY", "caution": False},
                {"spec": "stock:GLD", "caution": False},
            ]
        },
        path,
    )
    idx = pd.date_range("2024-01-01", periods=80, freq="D")
    # AAPL strong up, GLD mild up, MSFT flat-down, SPY mild up
    prices = pd.DataFrame(
        {
            "AAPL": [100 + i * 1.2 for i in range(80)],
            "MSFT": [100 - i * 0.3 for i in range(80)],
            "SPY": [100 + i * 0.4 for i in range(80)],
            "GLD": [100 + i * 0.35 for i in range(80)],
        },
        index=idx,
    )
    decision = W.decide(path, prices=prices)
    assert decision["method"] == "daily_rotation"
    assert "Rotação" in decision["note"] or "rotação" in decision["note"].lower()
    assert decision["weights"]
    assert "AAPL" in decision["weights"]
    assert any(a["action"] == "BUY" for a in decision["actions"])
    assert "open_positions" in decision
    assert any(p["asset"] == "AAPL" for p in decision["open_positions"])


def test_open_positions_lists_book_units_not_zero_sells():
    book = {
        "units": {"MSFT": 2.0, "bitcoin": 0.01},
        "last_prices": {"MSFT": 400.0, "bitcoin": 70_000.0},
        "cost_eur": {"MSFT": 390.0, "bitcoin": 75_000.0},
        "cash": 1000.0,
    }
    actions = [
        {"asset": "MSFT", "action": "HOLD", "weight_after_pct": 20},
        {"asset": "NVDA", "action": "SELL", "weight_after_pct": 0, "amount": 0},
    ]
    rows = W.open_positions(book, total_capital=10_000.0, actions=actions)
    assets = {r["asset"] for r in rows}
    assert "MSFT" in assets
    assert "bitcoin" in assets
    assert "NVDA" not in assets
    msft = next(r for r in rows if r["asset"] == "MSFT")
    assert msft["pnl_eur"] == 20.0
    assert msft["amount"] == 800.0


def test_rebalance_tracks_realized_pnl():
    book = {
        "initial": 10_000.0,
        "cash": 0.0,
        "units": {"AAPL": 10.0},
        "last_prices": {"AAPL": 100.0},
        "cost_eur": {"AAPL": 90.0},
        "realized_pnl_eur": 0.0,
    }
    eq, fills = W._rebalance(book, {}, {"AAPL": 110.0})
    assert book["units"] == {}
    assert book["realized_pnl_eur"] > 0
    assert eq == 1100.0  # marked at sell price before cash settles
    assert len(fills) == 1
    assert fills[0]["side"] == "SELL"
    assert fills[0]["pnl_eur"] > 0
    assert fills[0]["price_eur"] == 110.0
