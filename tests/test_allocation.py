import numpy as np
import pandas as pd
import pytest

from btcsim import allocation as A


@pytest.fixture
def two_asset_prices():
    """Asset A: low volatility; Asset B: high volatility. 200 daily points."""
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    t = np.arange(len(idx))
    a = 100.0 + t * 0.2                                  # smooth, low vol
    b = 100.0 + 20.0 * np.sin(t / 5.0) + t * 0.2        # oscillating, high vol
    return pd.DataFrame({"coin_a": a, "coin_b": b}, index=idx)


def test_equal_weights_sum_to_one():
    w = A.equal_weights(["x", "y", "z"])
    assert w.shape == (3,)
    assert abs(w.sum() - 1.0) < 1e-9
    assert np.allclose(w, 1 / 3)


def test_inverse_vol_prefers_low_vol_asset(two_asset_prices):
    returns = A.daily_returns(two_asset_prices)
    w = A.inverse_vol_weights(returns)
    assert abs(w.sum() - 1.0) < 1e-9
    # coin_a is calmer, so it should get more weight.
    assert w[0] > w[1]


def test_optimize_methods_valid_weights(two_asset_prices):
    coins = list(two_asset_prices.columns)
    returns = A.daily_returns(two_asset_prices)
    for method in A.ALLOCATION_METHODS:
        r = A.optimize(coins, returns, method=method, n_samples=3000)
        w = np.array(list(r.weights.values()))
        assert abs(w.sum() - 1.0) < 1e-6
        assert (w >= -1e-9).all() and (w <= 1 + 1e-9).all()


def test_min_variance_favours_low_vol(two_asset_prices):
    coins = list(two_asset_prices.columns)
    returns = A.daily_returns(two_asset_prices)
    r = A.optimize(coins, returns, method="min_variance", n_samples=5000)
    assert r.weights["coin_a"] > r.weights["coin_b"]


def test_unknown_method_raises(two_asset_prices):
    returns = A.daily_returns(two_asset_prices)
    with pytest.raises(ValueError):
        A.optimize(list(two_asset_prices.columns), returns, method="nope")


def test_backtest_equal_no_rebalance_matches_sum_of_holdings(two_asset_prices):
    weights = {"coin_a": 0.5, "coin_b": 0.5}
    bt = A.backtest(two_asset_prices, weights, initial_cash=10_000.0,
                    fee_rate=0.0, rebalance_days=0)
    # With no fees and no rebalance, final value == growth of each half.
    p = two_asset_prices
    expected = 5_000 * (p["coin_a"].iloc[-1] / p["coin_a"].iloc[0]) \
        + 5_000 * (p["coin_b"].iloc[-1] / p["coin_b"].iloc[0])
    assert bt.end_value == pytest.approx(expected, rel=1e-6)


def test_backtest_weights_normalized_and_curve_length(two_asset_prices):
    bt = A.backtest(two_asset_prices, {"coin_a": 2.0, "coin_b": 2.0},
                    initial_cash=10_000.0, fee_rate=0.0, rebalance_days=30)
    assert abs(sum(bt.weights.values()) - 1.0) < 1e-9
    assert len(bt.equity_curve) == len(two_asset_prices)
    assert bt.max_drawdown_pct <= 0


def test_backtest_rebalancing_incurs_fees(two_asset_prices):
    bt = A.backtest(two_asset_prices, {"coin_a": 0.5, "coin_b": 0.5},
                    initial_cash=10_000.0, fee_rate=0.001, rebalance_days=10)
    assert bt.total_fees > 0


@pytest.fixture
def long_two_asset_prices():
    idx = pd.date_range("2023-01-01", periods=300, freq="D")
    t = np.arange(len(idx))
    a = 100.0 + t * 0.2
    b = 100.0 + 25.0 * np.sin(t / 6.0) + t * 0.15
    return pd.DataFrame({"coin_a": a, "coin_b": b}, index=idx)


def test_walk_forward_produces_oos_curve(long_two_asset_prices):
    wf = A.walk_forward(long_two_asset_prices, method="min_variance",
                        train_days=120, test_days=30, n_samples=2000)
    assert len(wf.segments) >= 1
    assert len(wf.oos_equity) > 0
    assert len(wf.equal_equity) == len(wf.oos_equity)
    # OOS window starts after the first training window.
    assert wf.oos_equity.index[0] >= long_two_asset_prices.index[120]
    assert wf.metrics.end_value > 0


def test_walk_forward_needs_enough_data(two_asset_prices):
    with pytest.raises(ValueError):
        A.walk_forward(two_asset_prices, train_days=180, test_days=60)
