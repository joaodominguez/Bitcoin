"""Performance metrics computed from an equity curve."""

from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Metrics:
    start_value: float
    end_value: float
    total_return_pct: float
    annualized_return_pct: float
    max_drawdown_pct: float
    volatility_pct: float
    sharpe: float
    n_trades: int
    total_fees: float

    def as_dict(self) -> dict:
        return asdict(self)


def _annualization_factor(index: pd.DatetimeIndex) -> float:
    if len(index) < 2:
        return 1.0
    days = (index[-1] - index[0]).days or 1
    return 365.0 / days


def compute(
    equity: pd.Series,
    n_trades: int = 0,
    total_fees: float = 0.0,
    risk_free_rate: float = 0.0,
) -> Metrics:
    """Compute standard metrics from a daily equity curve.

    Args:
        equity: Portfolio value indexed by date (daily).
        n_trades: Number of executed trades.
        total_fees: Sum of fees paid.
        risk_free_rate: Annual risk-free rate for the Sharpe ratio.
    """
    equity = equity.dropna()
    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    total_return = (end / start - 1.0) if start else 0.0

    factor = _annualization_factor(equity.index)
    annualized = (1.0 + total_return) ** factor - 1.0 if total_return > -1 else -1.0

    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    max_dd = float(drawdown.min()) if len(drawdown) else 0.0

    daily_returns = equity.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        vol_daily = float(daily_returns.std())
        vol_annual = vol_daily * np.sqrt(365)
        excess = daily_returns.mean() - risk_free_rate / 365
        sharpe = float(excess / vol_daily * np.sqrt(365))
    else:
        vol_annual = 0.0
        sharpe = 0.0

    return Metrics(
        start_value=round(start, 2),
        end_value=round(end, 2),
        total_return_pct=round(total_return * 100, 2),
        annualized_return_pct=round(annualized * 100, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        volatility_pct=round(vol_annual * 100, 2),
        sharpe=round(sharpe, 2),
        n_trades=n_trades,
        total_fees=round(total_fees, 2),
    )
