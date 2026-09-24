import numpy as np
import pandas as pd

from btcsim import indicators


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = indicators.sma(s, 2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == 1.5
    assert out.iloc[4] == 4.5


def test_ema_length_and_finite():
    s = pd.Series(np.arange(1, 21), dtype=float)
    out = indicators.ema(s, 5)
    assert len(out) == len(s)
    assert out.notna().all()


def test_rsi_all_gains_is_100():
    s = pd.Series(np.arange(1, 40), dtype=float)  # strictly increasing
    out = indicators.rsi(s, 14)
    assert out.dropna().iloc[-1] == 100.0


def test_rsi_all_losses_is_0():
    s = pd.Series(np.arange(40, 1, -1), dtype=float)  # strictly decreasing
    out = indicators.rsi(s, 14)
    assert out.dropna().iloc[-1] == 0.0


def test_rsi_in_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + np.cumsum(rng.normal(size=200)))
    out = indicators.rsi(s, 14).dropna()
    assert (out >= 0).all() and (out <= 100).all()


def test_macd_columns():
    s = pd.Series(np.linspace(100, 200, 100))
    out = indicators.macd(s)
    assert list(out.columns) == ["macd", "signal", "hist"]
