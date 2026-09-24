from datetime import datetime, timezone

from btcsim import tape as T
from btcsim import watchlist as W


def _tape(**overrides):
    state = T.fresh_state()
    state["cash_eur"] = 1200.0
    state["funded"] = True
    state["anchor_high_usd"] = 87000.0
    state["levels_usd"] = T.levels_from_high(87000.0)
    state.update(overrides)
    return state


def test_levels_from_high_are_deeper_than_a_shallow_dip():
    levels = T.levels_from_high(87000)
    assert levels[0] <= 87000 * 0.93  # first buy at least ~7% off
    assert levels == sorted(levels, reverse=True)
    assert len(levels) == 4


def test_no_buy_near_spot_when_high_is_elevated():
    tape = _tape()
    fills = T.step(tape, {"usd": 83200, "eur": 73000})
    assert fills == []
    assert tape["lots"] == []


def test_buy_when_price_touches_first_adaptive_level():
    tape = _tape()
    level = tape["levels_usd"][0]
    fills = T.step(tape, {"usd": level, "eur": level * 0.88})
    assert len(fills) == 1
    assert fills[0]["side"] == "BUY"
    assert fills[0]["level_usd"] == level
    assert fills[0]["spent_eur"] == 200


def test_gap_down_buys_each_touched_level():
    tape = _tape()
    deep = tape["levels_usd"][2]
    fills = T.step(tape, {"usd": deep, "eur": deep * 0.88})
    assert len(fills) == 3
    assert all(f["side"] == "BUY" for f in fills)


def test_falling_price_holds_and_adds_lower_rung():
    tape = _tape()
    first = tape["levels_usd"][0]
    second = tape["levels_usd"][1]
    T.step(tape, {"usd": first, "eur": first * 0.88})
    fills = T.step(tape, {"usd": second, "eur": second * 0.88})
    assert [f["level_usd"] for f in fills] == [second]
    assert len(tape["lots"]) == 2


def test_sell_only_when_round_trip_is_profitable():
    tape = _tape()
    first = tape["levels_usd"][0]
    T.step(tape, {"usd": first, "eur": first * 0.88})
    held = T.step(tape, {"usd": first * 1.005, "eur": first * 0.88 * 1.005})
    assert held == []
    trigger = T.sell_trigger_usd(first)
    sold = T.step(tape, {"usd": trigger + 50, "eur": first * 0.88 * (trigger + 50) / first})
    assert len(sold) == 1
    assert sold[0]["side"] == "SELL"
    assert sold[0]["pnl_eur"] > 0


def test_level_can_be_bought_again_after_a_profitable_sell():
    tape = _tape()
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    first = tape["levels_usd"][0]
    T.step(tape, {"usd": first, "eur": first * 0.88}, now=now)
    trigger = T.sell_trigger_usd(first)
    T.step(tape, {"usd": trigger + 100, "eur": first * 0.88 * (trigger + 100) / first})
    assert tape["lots"] == []
    again = T.step(tape, {"usd": first, "eur": first * 0.88})
    assert [f["side"] for f in again] == ["BUY"]


def test_open_exposure_never_exceeds_budget():
    tape = _tape(budget_eur=400, slice_eur=200, cash_eur=400)
    deep = min(tape["levels_usd"])
    fills = T.step(tape, {"usd": deep - 100, "eur": (deep - 100) * 0.88})
    spent = sum(f["spent_eur"] for f in fills)
    assert spent == 400
    assert sum(lot["spent_eur"] for lot in tape["lots"]) <= 400


def test_refresh_levels_tracks_new_highs():
    tape = _tape(anchor_high_usd=80000, levels_usd=[])
    levels = T.refresh_levels(tape, 90000, 85000)
    assert tape["anchor_high_usd"] == 90000
    assert levels[0] < 90000


def test_risk_limits_cap_crypto_and_leave_cash():
    capped = W.apply_risk_limits({"bitcoin": 0.5, "SPY": 0.5})
    assert capped["bitcoin"] <= W.MAX_CRYPTO_WEIGHT + 1e-9
    assert sum(capped.values()) <= 1 - W.MIN_CASH_WEIGHT + 1e-9


def test_rebalance_holds_bitcoin_below_cost():
    book = {
        "initial": 10000,
        "cash": 0.0,
        "units": {"bitcoin": 10.0, "SPY": 50.0},
        "last_prices": {"bitcoin": 100.0, "SPY": 100.0},
        "cost_eur": {"bitcoin": 100.0, "SPY": 100.0},
    }
    W._rebalance(book, {"SPY": 1.0}, {"bitcoin": 80.0, "SPY": 100.0})
    assert book["units"]["bitcoin"] == 10.0


def test_rebalance_can_sell_bitcoin_above_cost():
    book = {
        "initial": 10000,
        "cash": 0.0,
        "units": {"bitcoin": 10.0, "SPY": 50.0},
        "last_prices": {"bitcoin": 100.0, "SPY": 100.0},
        "cost_eur": {"bitcoin": 100.0, "SPY": 100.0},
    }
    _, fills = W._rebalance(book, {"SPY": 1.0}, {"bitcoin": 110.0, "SPY": 100.0})
    assert "bitcoin" not in book["units"]
    assert any(f["side"] == "SELL" and f["asset"] == "bitcoin" and f["pnl_eur"] > 0 for f in fills)


def test_fund_reserves_cash_without_selling_bitcoin(tmp_path):
    book = {
        "initial": 10000,
        "cash": 200.0,
        "units": {"bitcoin": 1.0, "SPY": 20.0},
        "last_prices": {"bitcoin": 70000.0, "SPY": 500.0},
    }
    tape = T.fresh_state()
    moved = T.fund_from_book(book, tape)
    assert moved == 1200
    assert tape["funded"] is True
    assert book["units"]["bitcoin"] == 1.0
    T.save_tape(tmp_path, tape)
    assert T.mark_to_market(tmp_path) == 1200
