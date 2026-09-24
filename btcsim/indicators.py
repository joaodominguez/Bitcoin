"""Technical indicators used by strategies.

All functions take and return pandas Series aligned to the price index.
"""

from __future__ import annotations

import pandas as pd


def sma(prices: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return prices.rolling(window=window, min_periods=window).mean()


def ema(prices: pd.Series, span: int) -> pd.Series:
    """Exponential moving average."""
    return prices.ewm(span=span, adjust=False).mean()


def rsi(prices: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing).

    Returns values in the 0-100 range. Values > 70 are commonly read as
    "overbought" and < 30 as "oversold" (heuristics, not guarantees).
    """
    delta = prices.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)
    result = 100 - (100 / (1 + rs))
    # When there are no losses, RSI is 100; when no gains, RSI is 0.
    result = result.where(avg_loss != 0, 100.0)
    result = result.where(avg_gain != 0, result.where(avg_loss == 0, 0.0))
    return result


def macd(
    prices: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Moving Average Convergence Divergence.

    Returns a frame with ``macd``, ``signal`` and ``hist`` columns.
    """
    macd_line = ema(prices, fast) - ema(prices, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})
