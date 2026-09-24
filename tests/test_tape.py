from datetime import datetime, timezone

from btcsim import tape as T
from btcsim import watchlist as W


def _tape(**overrides):
    state = T.fresh_state()
    state["cash_eur"] = 1500.0
    state["funded"] = True
    state.update(overrides)
    return state


def test_no_buy_above_80k():
    tape = _tape()
    fills = T.step(tape, {"usd": 83860, "eur": 73638})
    assert fills == []
    assert tape["lots"] == []
    assert tape["cash_eur"] == 1500


def test_buy_one_slice_at_80k():
    tape = _tape()
    fills = T.step(tape, {"usd": 80000, "eur": 70000})
    assert len(fills) == 1
    assert fills[0]["side"] == "BUY"
    assert fills[0]["level_usd"] == 80000
    assert fills[0]["spent_eur"] == 250
    assert len(tape["lots"]) == 1
    assert tape["cash_eur"] == 1250


def test_gap_down_buys_each_touched_level():
    tape = _tape()
    fills = T.step(tape, {"usd": 75000, "eur": 65000})
    levels = sorted(f["level_usd"] for f in fills)
    assert levels == [76000, 78000, 80000]
    assert len(tape["lots"]) == 3


def test_falling_price_holds_and_adds_lower_rung():
    tape = _tape()
    T.step(tape, {"usd": 80000, "eur": 70000})
    fills = T.step(tape, {"usd": 78000, "eur": 68000})
    assert all(f["side"] == "BUY" for f in fills)
    assert [f["level_usd"] for f in fills] == [78000]
    assert len(tape["lots"]) == 2


def test_sell_only_when_round_trip_is_profitable():
    tape = _tape()
    T.step(tape, {"usd": 80000, "eur": 70000})
    held = T.step(tape, {"usd": 80500, "eur": 70400})
    assert held == []
    assert len(tape["lots"]) == 1
    trigger = T.sell_trigger_usd(80000)
    sold = T.step(tape, {"usd": trigger + 50, "eur": 70000 * (trigger + 50) / 80000})
    assert len(sold) == 1
    assert sold[0]["side"] == "SELL"
    assert sold[0]["pnl_eur"] > 0
    assert tape["lots"] == []
    assert tape["realized_pnl_eur"] > 0


def test_level_can_be_bought_again_after_a_profitable_sell():
    tape = _tape()
    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    T.step(tape, {"usd": 79000, "eur": 69000}, now=now)
    T.step(tape, {"usd": 82000, "eur": 69000 * 82000 / 79000})
    assert tape["lots"] == []
    again = T.step(tape, {"usd": 79000, "eur": 69000})
    assert [f["side"] for f in again] == ["BUY"]
    assert again[0]["level_usd"] == 80000


def test_open_exposure_never_exceeds_budget():
    tape = _tape(budget_eur=400, slice_eur=250, cash_eur=400)
    fills = T.step(tape, {"usd": 70000, "eur": 60000})
    spent = sum(f["spent_eur"] for f in fills)
    assert spent == 400
    assert tape["cash_eur"] == 0
    assert sum(lot["spent_eur"] for lot in tape["lots"]) <= 400


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
    W._rebalance(book, {"SPY": 1.0}, {"bitcoin": 110.0, "SPY": 100.0})
    assert "bitcoin" not in book["units"]


def test_fund_reserves_cash_without_selling_bitcoin(tmp_path):
    book = {
        "initial": 10000,
        "cash": 200.0,
        "units": {"bitcoin": 1.0, "SPY": 20.0},
        "last_prices": {"bitcoin": 70000.0, "SPY": 500.0},
    }
    tape = T.fresh_state()
    moved = T.fund_from_book(book, tape)
    assert moved == 1500
    assert tape["funded"] is True
    assert book["units"]["bitcoin"] == 1.0
    assert book["units"]["SPY"] < 20
    directory = tmp_path
    T.save_tape(directory, tape)
    assert T.mark_to_market(directory) == 1500
