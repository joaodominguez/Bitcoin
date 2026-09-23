"""Multi-asset allocation: split the capital across several cryptos.

Answers the question "como reparto os 10k da melhor maneira?" by:

1. Loading aligned daily prices for several coins.
2. Computing allocation weights with different methods (equal weight, inverse
   volatility / risk parity, maximum Sharpe, minimum variance). The Sharpe and
   variance optimisers use Monte-Carlo sampling over the weight simplex, so no
   heavy solver dependency is needed.
3. Backtesting the resulting portfolio with optional periodic rebalancing.

IMPORTANT: optimisers fit weights on *past* data. The past does not predict the
future, and optimising on history risks overfitting -- treat results as
educational, not as a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import data as data_mod
from . import metrics as metrics_mod

TRADING_DAYS = 365
ALLOCATION_METHODS = ("equal", "inverse_vol", "max_sharpe", "min_variance")


def _spec_name(spec: str) -> str:
    """A clean column name for an asset spec (crypto id or stock symbol)."""
    kind, symbol = data_mod.parse_asset(spec)
    return symbol.upper() if kind == "stock" else symbol.lower()


def load_prices(
    coins: list[str],
    currency: str = "eur",
    days: int = 365,
    **fetch_kwargs,
) -> pd.DataFrame:
    """Return a DataFrame of aligned daily prices, one column per asset.

    ``coins`` may mix crypto and stocks using asset specs, e.g.
    ``["bitcoin", "ethereum", "stock:AAPL"]``. Assets are aligned on their
    common dates (inner join), which naturally handles stock market holidays.
    """
    frames = {}
    for spec in coins:
        series = data_mod.fetch_asset(spec, days=days, currency=currency, **fetch_kwargs)
        frames[_spec_name(spec)] = series.frame["price"]
    prices = pd.DataFrame(frames).dropna()
    if prices.empty or len(prices.columns) < 1:
        raise ValueError("Not enough overlapping price data across the assets.")
    return prices


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    return prices.pct_change().dropna(how="all").dropna()


def annualized_moments(returns: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (annualized mean vector, annualized covariance matrix)."""
    mean = returns.mean().to_numpy() * TRADING_DAYS
    cov = returns.cov().to_numpy() * TRADING_DAYS
    return mean, cov


def portfolio_stats(
    weights: np.ndarray, mean: np.ndarray, cov: np.ndarray, risk_free: float = 0.0
) -> tuple[float, float, float]:
    """Return (expected annual return, annual volatility, Sharpe ratio)."""
    ret = float(weights @ mean)
    var = float(weights @ cov @ weights)
    vol = float(np.sqrt(max(var, 0.0)))
    sharpe = (ret - risk_free) / vol if vol > 0 else 0.0
    return ret, vol, sharpe


@dataclass
class AllocationResult:
    method: str
    weights: dict[str, float]
    exp_return_pct: float
    exp_volatility_pct: float
    exp_sharpe: float
    frontier: dict[str, list[float]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "exp_return_pct": self.exp_return_pct,
            "exp_volatility_pct": self.exp_volatility_pct,
            "exp_sharpe": self.exp_sharpe,
        }


def equal_weights(coins: list[str]) -> np.ndarray:
    n = len(coins)
    return np.full(n, 1.0 / n)


def inverse_vol_weights(returns: pd.DataFrame) -> np.ndarray:
    vol = returns.std().to_numpy()
    vol = np.where(vol <= 0, np.nan, vol)
    inv = 1.0 / vol
    inv = np.nan_to_num(inv, nan=0.0)
    if inv.sum() == 0:
        return equal_weights(list(returns.columns))
    return inv / inv.sum()


def _monte_carlo(
    mean: np.ndarray,
    cov: np.ndarray,
    n_samples: int = 20_000,
    risk_free: float = 0.0,
    seed: int = 42,
) -> dict:
    """Sample random long-only weight vectors and record their stats."""
    rng = np.random.default_rng(seed)
    n = len(mean)
    weights = rng.dirichlet(np.ones(n), size=n_samples)
    rets = weights @ mean
    vols = np.sqrt(np.einsum("ij,jk,ik->i", weights, cov, weights).clip(min=0))
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpes = np.where(vols > 0, (rets - risk_free) / vols, -np.inf)
    return {
        "weights": weights,
        "rets": rets,
        "vols": vols,
        "sharpes": sharpes,
    }


def optimize(
    coins: list[str],
    returns: pd.DataFrame,
    method: str = "max_sharpe",
    n_samples: int = 20_000,
    risk_free: float = 0.0,
    seed: int = 42,
) -> AllocationResult:
    """Compute allocation weights for ``method``.

    Methods: ``equal``, ``inverse_vol``, ``max_sharpe``, ``min_variance``.
    """
    if method not in ALLOCATION_METHODS:
        raise ValueError(f"Unknown method '{method}'. Options: {ALLOCATION_METHODS}")

    mean, cov = annualized_moments(returns)

    frontier: dict[str, list[float]] = {}
    if method == "equal":
        w = equal_weights(coins)
    elif method == "inverse_vol":
        w = inverse_vol_weights(returns)
    else:
        mc = _monte_carlo(mean, cov, n_samples=n_samples, risk_free=risk_free, seed=seed)
        if method == "max_sharpe":
            idx = int(np.argmax(mc["sharpes"]))
        else:  # min_variance
            idx = int(np.argmin(mc["vols"]))
        w = mc["weights"][idx]
        frontier = {
            "returns": (mc["rets"] * 100).round(2).tolist(),
            "volatility": (mc["vols"] * 100).round(2).tolist(),
            "sharpe": np.where(np.isfinite(mc["sharpes"]), mc["sharpes"], 0).round(3).tolist(),
        }

    ret, vol, sharpe = portfolio_stats(w, mean, cov, risk_free)
    return AllocationResult(
        method=method,
        weights={c: float(wi) for c, wi in zip(coins, w)},
        exp_return_pct=round(ret * 100, 2),
        exp_volatility_pct=round(vol * 100, 2),
        exp_sharpe=round(sharpe, 2),
        frontier=frontier,
    )


@dataclass
class AllocationBacktest:
    weights: dict[str, float]
    equity_curve: pd.Series
    per_asset_value: pd.DataFrame
    end_value: float
    total_return_pct: float
    max_drawdown_pct: float
    total_fees: float
    rebalance_days: int


def backtest(
    prices: pd.DataFrame,
    weights: dict[str, float],
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    rebalance_days: int = 30,
) -> AllocationBacktest:
    """Backtest a fixed-weight portfolio with optional periodic rebalancing.

    Args:
        prices: aligned daily prices (columns = coins).
        weights: target weight per coin (should sum to ~1).
        initial_cash: starting capital.
        fee_rate: proportional trade fee.
        rebalance_days: rebalance cadence; 0 disables rebalancing (weights drift).
    """
    coins = list(prices.columns)
    w = np.array([weights.get(c, 0.0) for c in coins], dtype=float)
    if w.sum() <= 0:
        raise ValueError("weights must sum to a positive number")
    w = w / w.sum()

    price_mat = prices.to_numpy(dtype=float)
    n_days, n_assets = price_mat.shape

    units = np.zeros(n_assets)
    cash = float(initial_cash)
    total_fees = 0.0
    equity = np.zeros(n_days)
    per_asset = np.zeros((n_days, n_assets))

    def _rebalance(day_prices: np.ndarray, target_w: np.ndarray):
        nonlocal cash, total_fees, units
        eq = cash + float(units @ day_prices)
        targets = target_w * eq
        cur_vals = units * day_prices
        # Sells first to free up cash.
        for i in range(n_assets):
            if targets[i] < cur_vals[i] and day_prices[i] > 0:
                sell_val = cur_vals[i] - targets[i]
                fee = sell_val * fee_rate
                cash += sell_val - fee
                total_fees += fee
                units[i] = targets[i] / day_prices[i]
        # Then buys, limited by available cash.
        for i in range(n_assets):
            cur = units[i] * day_prices[i]
            if targets[i] > cur and cash > 0 and day_prices[i] > 0:
                buy_val = min(targets[i] - cur, cash / (1 + fee_rate))
                fee = buy_val * fee_rate
                cash -= buy_val + fee
                total_fees += fee
                units[i] += buy_val / day_prices[i]

    for day in range(n_days):
        day_prices = price_mat[day]
        if day == 0:
            _rebalance(day_prices, w)
        elif rebalance_days > 0 and day % rebalance_days == 0:
            _rebalance(day_prices, w)
        per_asset[day] = units * day_prices
        equity[day] = cash + float(units @ day_prices)

    equity_series = pd.Series(equity, index=prices.index, name="equity")
    per_asset_df = pd.DataFrame(per_asset, index=prices.index, columns=coins)

    running_max = equity_series.cummax()
    max_dd = float((equity_series / running_max - 1.0).min()) * 100

    return AllocationBacktest(
        weights={c: float(wi) for c, wi in zip(coins, w)},
        equity_curve=equity_series,
        per_asset_value=per_asset_df,
        end_value=round(float(equity_series.iloc[-1]), 2),
        total_return_pct=round((float(equity_series.iloc[-1]) / initial_cash - 1) * 100, 2),
        max_drawdown_pct=round(max_dd, 2),
        total_fees=round(total_fees, 2),
        rebalance_days=rebalance_days,
    )


@dataclass
class WalkForwardResult:
    method: str
    oos_equity: pd.Series          # out-of-sample equity curve of the strategy
    equal_equity: pd.Series        # equal-weight benchmark over the same window
    segments: list[dict]           # per-window {start, end, weights}
    metrics: "metrics_mod.Metrics"
    equal_metrics: "metrics_mod.Metrics"
    train_days: int
    test_days: int


def walk_forward(
    prices: pd.DataFrame,
    method: str = "max_sharpe",
    train_days: int = 180,
    test_days: int = 30,
    rebalance_days: int = 30,
    initial_cash: float = 10_000.0,
    fee_rate: float = 0.001,
    n_samples: int = 15_000,
) -> WalkForwardResult:
    """Out-of-sample validation: optimise on a training window, then apply the
    weights to the *next* (unseen) window, roll forward, and chain the results.

    This is the honest way to judge an allocation method: it never optimises on
    the data it is then scored on, so it exposes overfitting that a single
    full-history optimisation would hide. Compared against an equal-weight
    benchmark over the same out-of-sample period.
    """
    names = list(prices.columns)
    n = len(prices)
    if n <= train_days + test_days:
        raise ValueError(
            "Not enough data for walk-forward: need more than "
            f"train_days + test_days ({train_days + test_days}) rows, got {n}."
        )

    oos_segments: list[pd.Series] = []
    equal_segments: list[pd.Series] = []
    segments: list[dict] = []

    equity = initial_cash
    equal_equity = initial_cash
    pos = train_days
    while pos < n:
        train = prices.iloc[max(0, pos - train_days):pos]
        test = prices.iloc[pos:pos + test_days]
        if len(test) < 2:
            break
        returns_train = daily_returns(train)
        result = optimize(names, returns_train, method=method, n_samples=n_samples)
        bt = backtest(test, result.weights, initial_cash=equity,
                      fee_rate=fee_rate, rebalance_days=rebalance_days)
        oos_segments.append(bt.equity_curve)
        equity = bt.end_value

        eq_bt = backtest(test, {c: 1.0 / len(names) for c in names},
                         initial_cash=equal_equity, fee_rate=fee_rate,
                         rebalance_days=rebalance_days)
        equal_segments.append(eq_bt.equity_curve)
        equal_equity = eq_bt.end_value

        segments.append({
            "start": str(test.index[0].date()),
            "end": str(test.index[-1].date()),
            "weights": {k: round(v, 4) for k, v in result.weights.items()},
        })
        pos += test_days

    oos = pd.concat(oos_segments)
    oos = oos[~oos.index.duplicated(keep="last")]
    equal = pd.concat(equal_segments)
    equal = equal[~equal.index.duplicated(keep="last")]

    return WalkForwardResult(
        method=method,
        oos_equity=oos,
        equal_equity=equal,
        segments=segments,
        metrics=metrics_mod.compute(oos),
        equal_metrics=metrics_mod.compute(equal),
        train_days=train_days,
        test_days=test_days,
    )
