"""Historical price data loading with on-disk caching.

Primary source is the free CoinGecko public API, which limits history to the
past 365 days. For longer backtests you can supply your own CSV via
``load_csv`` (columns: ``date,price``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
DEFAULT_CACHE_DIR = Path(".cache")
MAX_FREE_DAYS = 365


@dataclass
class PriceSeries:
    """A daily close-price series for a single asset/currency pair."""

    coin: str
    currency: str
    frame: pd.DataFrame  # index: DatetimeIndex (daily), column: "price"

    @property
    def start(self) -> pd.Timestamp:
        return self.frame.index[0]

    @property
    def end(self) -> pd.Timestamp:
        return self.frame.index[-1]

    def __len__(self) -> int:
        return len(self.frame)

    def slice(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> "PriceSeries":
        """Return a new PriceSeries restricted to [start, end] (inclusive)."""
        frame = self.frame
        if start is not None:
            frame = frame[frame.index >= pd.Timestamp(start)]
        if end is not None:
            frame = frame[frame.index <= pd.Timestamp(end)]
        if frame.empty:
            raise ValueError("No price data in the requested date range.")
        return PriceSeries(self.coin, self.currency, frame.copy())


def _normalise(prices: list[list[float]]) -> pd.DataFrame:
    """Convert CoinGecko [[ms_timestamp, price], ...] into a daily frame."""
    frame = pd.DataFrame(prices, columns=["ts", "price"])
    frame["date"] = pd.to_datetime(frame["ts"], unit="ms").dt.normalize()
    # Keep the last observation per day, then ensure a continuous daily index.
    frame = frame.groupby("date", as_index=True)["price"].last().to_frame()
    frame = frame.asfreq("D").ffill()
    frame.index.name = "date"
    return frame


def fetch(
    days: int = MAX_FREE_DAYS,
    currency: str = "eur",
    coin: str = "bitcoin",
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    max_age_hours: float = 12.0,
    session: Optional[requests.Session] = None,
) -> PriceSeries:
    """Fetch daily price history, using a local cache when fresh.

    Args:
        days: How many days of history to request (capped at 365 for the free API).
        currency: Fiat currency code (e.g. ``eur``, ``usd``).
        coin: CoinGecko coin id (default ``bitcoin``).
        cache_dir: Directory for the CSV cache.
        max_age_hours: Reuse the cache if it is younger than this.
        session: Optional requests session (useful for testing).
    """
    days = min(int(days), MAX_FREE_DAYS)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{coin}_{currency}_{days}.csv"

    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600.0
        if age_hours <= max_age_hours:
            return load_csv(cache_file, coin=coin, currency=currency)

    sess = session or requests
    url = COINGECKO_URL.format(coin=coin)
    params = {"vs_currency": currency, "days": days, "interval": "daily"}

    try:
        resp = sess.get(url, params=params, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        frame = _normalise(payload["prices"])
    except Exception as exc:  # noqa: BLE001 - fall back to stale cache if possible
        if cache_file.exists():
            return load_csv(cache_file, coin=coin, currency=currency)
        raise RuntimeError(
            f"Failed to fetch price data and no cache is available: {exc}"
        ) from exc

    frame.to_csv(cache_file)
    return PriceSeries(coin=coin, currency=currency, frame=frame)


def load_csv(
    path: Path | str,
    coin: str = "bitcoin",
    currency: str = "eur",
) -> PriceSeries:
    """Load a daily price series from a CSV with ``date`` and ``price`` columns."""
    frame = pd.read_csv(path)
    cols = {c.lower(): c for c in frame.columns}
    date_col = cols.get("date", frame.columns[0])
    price_col = cols.get("price", cols.get("close", frame.columns[-1]))
    frame = frame[[date_col, price_col]].rename(
        columns={date_col: "date", price_col: "price"}
    )
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.groupby("date", as_index=True)["price"].last().to_frame()
    frame = frame.asfreq("D").ffill()
    frame.index.name = "date"
    return PriceSeries(coin=coin, currency=currency, frame=frame)
