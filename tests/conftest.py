import numpy as np
import pandas as pd
import pytest

from btcsim.data import PriceSeries


@pytest.fixture
def rising_series() -> PriceSeries:
    """A steadily rising price series (100 -> ~200 over 200 days)."""
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    prices = np.linspace(100.0, 200.0, len(idx))
    frame = pd.DataFrame({"price": prices}, index=idx)
    frame.index.name = "date"
    return PriceSeries("bitcoin", "eur", frame)


@pytest.fixture
def volatile_series() -> PriceSeries:
    """A deterministic oscillating series for exercising signal strategies."""
    idx = pd.date_range("2024-01-01", periods=200, freq="D")
    t = np.arange(len(idx))
    prices = 100.0 + 30.0 * np.sin(t / 10.0) + t * 0.1
    frame = pd.DataFrame({"price": prices}, index=idx)
    frame.index.name = "date"
    return PriceSeries("bitcoin", "eur", frame)
