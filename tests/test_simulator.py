import pandas as pd

from btcsim import metrics as metrics_mod
from btcsim.simulator import Simulator
from btcsim.strategies import BuyAndHold, DCA


def test_equity_curve_length(rising_series):
    sim = Simulator(rising_series, initial_cash=10_000.0)
    result = sim.run(BuyAndHold())
    assert len(result.equity_curve) == len(rising_series)
    assert result.equity_curve.notna().all()


def test_metrics_reasonable_on_rising(rising_series):
    sim = Simulator(rising_series, initial_cash=10_000.0, fee_rate=0.0)
    result = sim.run(BuyAndHold())
    m = result.metrics
    assert m.total_return_pct > 0
    assert m.max_drawdown_pct <= 0
    assert m.start_value == 10_000.0


def test_compare_returns_all(volatile_series):
    sim = Simulator(volatile_series, initial_cash=10_000.0)
    results = sim.compare([BuyAndHold(), DCA(every_days=10)])
    assert set(results) == {"buy_and_hold", "dca"}


def test_max_drawdown_computation():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    equity = pd.Series([100, 120, 90, 95, 130], index=idx, dtype=float)
    m = metrics_mod.compute(equity)
    # Peak 120 -> trough 90 == -25%
    assert m.max_drawdown_pct == -25.0
