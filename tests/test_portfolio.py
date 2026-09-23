import pandas as pd

from btcsim.portfolio import Portfolio


def test_buy_reduces_cash_and_adds_units():
    pf = Portfolio(cash=10_000.0, fee_rate=0.0)
    date = pd.Timestamp("2024-01-01")
    trade = pf.buy(date, price=100.0, cash_to_spend=1_000.0)
    assert trade is not None
    assert pf.cash == 9_000.0
    assert pf.units == 10.0
    assert pf.equity(100.0) == 10_000.0


def test_buy_applies_fee():
    pf = Portfolio(cash=1_000.0, fee_rate=0.01)
    trade = pf.buy(pd.Timestamp("2024-01-01"), price=100.0, cash_to_spend=1_000.0)
    assert trade.fee == 10.0
    assert pf.units == (1_000.0 - 10.0) / 100.0


def test_cannot_spend_more_than_cash():
    pf = Portfolio(cash=500.0, fee_rate=0.0)
    pf.buy(pd.Timestamp("2024-01-01"), price=100.0, cash_to_spend=5_000.0)
    assert pf.cash == 0.0
    assert pf.units == 5.0


def test_sell_all_returns_to_cash():
    pf = Portfolio(cash=1_000.0, fee_rate=0.0)
    d = pd.Timestamp("2024-01-01")
    pf.buy_all(d, price=100.0)
    assert pf.units == 10.0
    pf.sell_all(pd.Timestamp("2024-01-02"), price=200.0)
    assert pf.units == 0.0
    assert pf.cash == 2_000.0


def test_sell_more_than_held_is_capped():
    pf = Portfolio(cash=0.0, fee_rate=0.0, units=2.0)
    trade = pf.sell(pd.Timestamp("2024-01-01"), price=100.0, units_to_sell=5.0)
    assert trade.units == 2.0
    assert pf.units == 0.0


def test_no_trade_when_nothing_to_do():
    pf = Portfolio(cash=0.0, fee_rate=0.0)
    assert pf.buy(pd.Timestamp("2024-01-01"), 100.0, 100.0) is None
    assert pf.sell(pd.Timestamp("2024-01-01"), 100.0, 1.0) is None
