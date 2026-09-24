import pandas as pd

from btcsim.news import KeywordSentimentProvider
from btcsim.simulator import Simulator
from btcsim.strategies import (
    BuyAndHold,
    DCA,
    MACrossover,
    RSIStrategy,
    SentimentStrategy,
    build_strategy,
)


def test_buy_and_hold_matches_price_return(rising_series):
    sim = Simulator(rising_series, initial_cash=10_000.0, fee_rate=0.0)
    result = sim.run(BuyAndHold())
    price_return = rising_series.frame["price"].iloc[-1] / rising_series.frame["price"].iloc[0]
    assert result.metrics.end_value == round(10_000.0 * price_return, 2)
    assert result.metrics.n_trades == 1


def test_dca_spreads_capital(rising_series):
    sim = Simulator(rising_series, initial_cash=10_000.0, fee_rate=0.0)
    result = sim.run(DCA(every_days=10))
    assert result.metrics.n_trades > 1
    # On a rising market, buying over time beats zero but underperforms all-in.
    assert result.metrics.end_value > 10_000.0


def test_dca_fixed_amount(rising_series):
    sim = Simulator(rising_series, initial_cash=10_000.0, fee_rate=0.0)
    result = sim.run(DCA(amount=100.0, every_days=7))
    total_bought = result.trades["cash_amount"].sum()
    assert total_bought <= 10_000.0 + 1e-6


def test_ma_crossover_trades_on_volatile(volatile_series):
    sim = Simulator(volatile_series, initial_cash=10_000.0, fee_rate=0.0)
    result = sim.run(MACrossover(short=10, long=30))
    assert result.metrics.n_trades >= 1


def test_rsi_trades_on_volatile(volatile_series):
    sim = Simulator(volatile_series, initial_cash=10_000.0, fee_rate=0.0)
    result = sim.run(RSIStrategy(window=14, low=40, high=60))
    assert result.metrics.n_trades >= 1


def test_sentiment_neutral_does_nothing(volatile_series):
    sim = Simulator(volatile_series, initial_cash=10_000.0, fee_rate=0.0)
    result = sim.run(SentimentStrategy(threshold=0.2))
    assert result.metrics.n_trades == 0


def test_sentiment_reacts_to_headlines(volatile_series):
    idx = volatile_series.frame.index
    headlines = [
        (idx[5], "Bitcoin ETF approval sparks massive rally and record inflows"),
        (idx[6], "Institutional adoption surges as bulls take control"),
        (idx[100], "Market crash: exchange hacked, massive selloff and fear"),
        (idx[101], "Regulators announce crackdown, prices plummet"),
    ]
    provider = KeywordSentimentProvider(headlines)
    sim = Simulator(
        volatile_series, initial_cash=10_000.0, fee_rate=0.0,
        sentiment_provider=provider,
    )
    result = sim.run(SentimentStrategy(threshold=0.2, smooth=1))
    assert result.metrics.n_trades >= 1


def test_build_strategy_registry():
    assert isinstance(build_strategy("dca"), DCA)
    assert isinstance(build_strategy("buy_and_hold"), BuyAndHold)
