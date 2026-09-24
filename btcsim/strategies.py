"""Trading strategies.

Each strategy implements a small event interface:

* ``prepare(prices, sentiment)`` -- precompute indicators once.
* ``on_day(i, date, price, portfolio)`` -- optionally place orders for day ``i``.

Strategies never see the future: ``on_day`` may only use data up to index ``i``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from . import indicators
from .portfolio import Portfolio


class Strategy(ABC):
    name: str = "strategy"

    def prepare(self, prices: pd.Series, sentiment: pd.Series) -> None:
        self.prices = prices
        self.sentiment = sentiment

    @abstractmethod
    def on_day(
        self, i: int, date: pd.Timestamp, price: float, portfolio: Portfolio
    ) -> None:
        ...


class BuyAndHold(Strategy):
    """Invest everything on day one and never trade again (the benchmark)."""

    name = "buy_and_hold"

    def on_day(self, i, date, price, portfolio):
        if i == 0:
            portfolio.buy_all(date, price, reason="initial buy & hold")


class DCA(Strategy):
    """Dollar-Cost Averaging: invest a fixed amount at a fixed cadence.

    If ``amount`` is None, the starting cash is spread evenly across the whole
    period so the capital is roughly fully deployed by the end.
    """

    name = "dca"

    def __init__(self, amount: float | None = None, every_days: int = 7):
        self.amount = amount
        self.every_days = max(1, int(every_days))
        self._per_buy: float | None = None

    def prepare(self, prices, sentiment):
        super().prepare(prices, sentiment)
        if self.amount is None:
            n_buys = max(1, len(prices) // self.every_days)
            self._per_buy = None  # resolved on first day from starting cash
            self._n_buys = n_buys
        else:
            self._per_buy = self.amount

    def on_day(self, i, date, price, portfolio):
        if i % self.every_days != 0:
            return
        if self._per_buy is None:
            # First eligible day: size buys from the initial cash balance.
            self._per_buy = portfolio.cash / self._n_buys
        portfolio.buy(date, price, self._per_buy, reason="DCA buy")


class MACrossover(Strategy):
    """Go all-in when the short SMA crosses above the long SMA, all-out below."""

    name = "ma_crossover"

    def __init__(self, short: int = 20, long: int = 50):
        if short >= long:
            raise ValueError("short window must be smaller than long window")
        self.short = short
        self.long = long

    def prepare(self, prices, sentiment):
        super().prepare(prices, sentiment)
        self.sma_short = indicators.sma(prices, self.short)
        self.sma_long = indicators.sma(prices, self.long)

    def on_day(self, i, date, price, portfolio):
        s = self.sma_short.iloc[i]
        l = self.sma_long.iloc[i]
        if pd.isna(s) or pd.isna(l):
            return
        if s > l and portfolio.units == 0 and portfolio.cash > 0:
            portfolio.buy_all(date, price, reason="SMA bullish crossover")
        elif s < l and portfolio.units > 0:
            portfolio.sell_all(date, price, reason="SMA bearish crossover")


class RSIStrategy(Strategy):
    """Buy when oversold (RSI < low), sell when overbought (RSI > high)."""

    name = "rsi"

    def __init__(self, window: int = 14, low: float = 30.0, high: float = 70.0):
        self.window = window
        self.low = low
        self.high = high

    def prepare(self, prices, sentiment):
        super().prepare(prices, sentiment)
        self.rsi = indicators.rsi(prices, self.window)

    def on_day(self, i, date, price, portfolio):
        r = self.rsi.iloc[i]
        if pd.isna(r):
            return
        if r < self.low and portfolio.cash > 0:
            portfolio.buy_all(date, price, reason=f"RSI oversold ({r:.0f})")
        elif r > self.high and portfolio.units > 0:
            portfolio.sell_all(date, price, reason=f"RSI overbought ({r:.0f})")


class SentimentStrategy(Strategy):
    """Trade on news/market sentiment.

    Sentiment is smoothed over ``smooth`` days to reduce noise. With the
    default NeutralProvider this strategy never trades, which is by design --
    plug in a real sentiment source (e.g. FearGreedProvider) to activate it.

    Args:
        threshold: absolute sentiment level (0-1) required to act.
        smooth: rolling window (days) applied to the sentiment signal.
        contrarian: if True, invert the logic -- buy on extreme fear
            (very negative sentiment) and sell on extreme greed (very
            positive). This is the classic "be greedy when others are
            fearful" approach and pairs well with the Fear & Greed index.
    """

    name = "sentiment"

    def __init__(self, threshold: float = 0.2, smooth: int = 3, contrarian: bool = False):
        self.threshold = threshold
        self.smooth = max(1, int(smooth))
        self.contrarian = contrarian

    def prepare(self, prices, sentiment):
        super().prepare(prices, sentiment)
        self.smoothed = sentiment.rolling(self.smooth, min_periods=1).mean()

    def on_day(self, i, date, price, portfolio):
        s = self.smoothed.iloc[i]
        if pd.isna(s):
            return
        buy_signal = s <= -self.threshold if self.contrarian else s >= self.threshold
        sell_signal = s >= self.threshold if self.contrarian else s <= -self.threshold
        if buy_signal and portfolio.cash > 0:
            label = "fear (contrarian buy)" if self.contrarian else "bullish news"
            portfolio.buy_all(date, price, reason=f"{label} ({s:+.2f})")
        elif sell_signal and portfolio.units > 0:
            label = "greed (contrarian sell)" if self.contrarian else "bearish news"
            portfolio.sell_all(date, price, reason=f"{label} ({s:+.2f})")


STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    BuyAndHold.name: BuyAndHold,
    DCA.name: DCA,
    MACrossover.name: MACrossover,
    RSIStrategy.name: RSIStrategy,
    SentimentStrategy.name: SentimentStrategy,
}


def build_strategy(name: str, **kwargs) -> Strategy:
    """Instantiate a strategy by name from the registry."""
    if name not in STRATEGY_REGISTRY:
        raise KeyError(
            f"Unknown strategy '{name}'. Available: {sorted(STRATEGY_REGISTRY)}"
        )
    return STRATEGY_REGISTRY[name](**kwargs)
