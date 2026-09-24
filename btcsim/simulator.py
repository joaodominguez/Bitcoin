"""Backtest engine that runs a strategy over a price series."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from . import metrics as metrics_mod
from .data import PriceSeries
from .metrics import Metrics
from .news import NeutralProvider, SentimentProvider
from .portfolio import Portfolio
from .strategies import Strategy


@dataclass
class SimulationResult:
    strategy_name: str
    equity_curve: pd.Series          # daily portfolio value
    price: pd.Series                 # daily BTC price
    portfolio: Portfolio
    metrics: Metrics
    sentiment: pd.Series

    @property
    def trades(self) -> pd.DataFrame:
        return self.portfolio.trades_frame()


class Simulator:
    """Runs a single strategy over a :class:`PriceSeries`.

    Args:
        series: The price data to simulate on.
        initial_cash: Starting capital.
        fee_rate: Proportional trading fee.
        sentiment_provider: Source of daily news sentiment (default neutral).
    """

    def __init__(
        self,
        series: PriceSeries,
        initial_cash: float = 10_000.0,
        fee_rate: float = 0.001,
        sentiment_provider: Optional[SentimentProvider] = None,
    ):
        self.series = series
        self.initial_cash = float(initial_cash)
        self.fee_rate = float(fee_rate)
        self.sentiment_provider = sentiment_provider or NeutralProvider()

    def run(self, strategy: Strategy) -> SimulationResult:
        prices = self.series.frame["price"]
        sentiment = self.sentiment_provider.daily_sentiment(prices.index)

        portfolio = Portfolio(cash=self.initial_cash, fee_rate=self.fee_rate)
        strategy.prepare(prices, sentiment)

        equity = pd.Series(index=prices.index, dtype=float)
        for i, (date, price) in enumerate(prices.items()):
            strategy.on_day(i, date, float(price), portfolio)
            equity.iloc[i] = portfolio.equity(float(price))

        total_fees = sum(t.fee for t in portfolio.trades)
        result_metrics = metrics_mod.compute(
            equity,
            n_trades=len(portfolio.trades),
            total_fees=total_fees,
        )
        return SimulationResult(
            strategy_name=getattr(strategy, "name", "strategy"),
            equity_curve=equity,
            price=prices,
            portfolio=portfolio,
            metrics=result_metrics,
            sentiment=sentiment,
        )

    def compare(self, strategies: list[Strategy]) -> dict[str, SimulationResult]:
        """Run several strategies on the same data and return a name->result map."""
        return {getattr(s, "name", f"s{i}"): self.run(s) for i, s in enumerate(strategies)}
