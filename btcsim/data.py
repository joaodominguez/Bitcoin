"""Historical price data loading with on-disk caching.

Two free data sources, both without an API key:

* **Crypto** via the CoinGecko public API (history limited to the past 365 days).
* **Stocks / ETFs / indices** via Yahoo Finance (``fetch_stock``), with long history.

Use asset specs to mix them: ``"bitcoin"`` / ``"crypto:ethereum"`` for crypto and
``"stock:AAPL"`` / ``"stock:SPY"`` for equities. ``fetch_asset`` dispatches by spec.
You can also supply your own CSV via ``load_csv`` (columns: ``date,price``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/{coin}/market_chart"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_COOKIE_URL = "https://fc.yahoo.com"
YAHOO_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
DEFAULT_CACHE_DIR = Path(".cache")
MAX_FREE_DAYS = 365

TICKERS = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "cardano": "ADA",
    "dogecoin": "DOGE",
    "binancecoin": "BNB",
    "ripple": "XRP",
    "litecoin": "LTC",
    "polkadot": "DOT",
}


def ticker_for(coin: str) -> str:
    """Return a short ticker for a CoinGecko coin id (fallback: upper-cased id)."""
    return TICKERS.get(coin.lower(), coin.upper()[:5])


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


# --------------------------------------------------------------------------- #
# Stocks (Yahoo Finance) and unified asset dispatch
# --------------------------------------------------------------------------- #

def parse_asset(spec: str) -> tuple[str, str]:
    """Parse an asset spec into ``(type, symbol)``.

    ``"stock:AAPL"`` -> ``("stock", "AAPL")``; ``"crypto:bitcoin"`` ->
    ``("crypto", "bitcoin")``; a bare value defaults to crypto:
    ``"bitcoin"`` -> ``("crypto", "bitcoin")``.
    """
    spec = spec.strip()
    if ":" in spec:
        kind, symbol = spec.split(":", 1)
        return kind.strip().lower(), symbol.strip()
    return "crypto", spec


def asset_label(spec: str) -> str:
    """A short display ticker for an asset spec (crypto id or stock symbol)."""
    kind, symbol = parse_asset(spec)
    if kind == "stock":
        return symbol.upper()
    return ticker_for(symbol)


def _yahoo_session(session: Optional[requests.Session]) -> requests.Session:
    if session is not None:
        return session
    sess = requests.Session()
    sess.headers.update({"User-Agent": YAHOO_UA, "Accept": "application/json"})
    try:  # Prime cookies; Yahoo often 429s requests without them.
        sess.get(YAHOO_COOKIE_URL, timeout=15)
    except Exception:  # noqa: BLE001
        pass
    return sess


def _normalise_yahoo(payload: dict) -> tuple[pd.DataFrame, str]:
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    closes = quote.get("close", [])
    adj = result.get("indicators", {}).get("adjclose", [{}])
    adjclose = adj[0].get("adjclose") if adj else None
    prices = adjclose if adjclose else closes
    currency = result.get("meta", {}).get("currency", "USD").lower()

    frame = pd.DataFrame({"ts": timestamps, "price": prices}).dropna()
    frame["date"] = pd.to_datetime(frame["ts"], unit="s").dt.normalize()
    frame = frame.groupby("date", as_index=True)["price"].last().to_frame()
    frame = frame.asfreq("D").ffill()  # fill weekends/holidays for daily continuity
    frame.index.name = "date"
    return frame, currency


def fetch_stock(
    symbol: str,
    days: int = MAX_FREE_DAYS,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    max_age_hours: float = 12.0,
    session: Optional[requests.Session] = None,
) -> PriceSeries:
    """Fetch daily stock/ETF/index history from Yahoo Finance (no API key).

    Args:
        symbol: Yahoo ticker, e.g. ``AAPL``, ``MSFT``, ``SPY``, ``^GSPC``.
        days: How many days of history to request.
        cache_dir: Directory for the CSV cache.
        max_age_hours: Reuse the cache if it is younger than this.
        session: Optional requests session (useful for testing).
    """
    days = int(days)
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = symbol.replace("^", "_").replace("/", "_")
    cache_file = cache_dir / f"stock_{safe}_{days}.csv"

    if cache_file.exists():
        age_hours = (time.time() - cache_file.stat().st_mtime) / 3600.0
        if age_hours <= max_age_hours:
            return load_csv(cache_file, coin=symbol.upper(), currency="usd")

    sess = _yahoo_session(session)
    now = int(time.time())
    period1 = now - days * 86400 - 5 * 86400  # small margin for weekends
    url = YAHOO_CHART_URL.format(symbol=symbol)
    params = {
        "period1": period1,
        "period2": now,
        "interval": "1d",
        "events": "div,splits",
    }
    try:
        resp = sess.get(url, params=params, timeout=30)
        resp.raise_for_status()
        frame, currency = _normalise_yahoo(resp.json())
        if frame.empty:
            raise ValueError("empty price frame")
    except Exception as exc:  # noqa: BLE001 - fall back to stale cache if possible
        if cache_file.exists():
            return load_csv(cache_file, coin=symbol.upper(), currency="usd")
        raise RuntimeError(
            f"Failed to fetch stock '{symbol}' from Yahoo Finance: {exc}"
        ) from exc

    frame.to_csv(cache_file)
    return PriceSeries(coin=symbol.upper(), currency=currency, frame=frame)


def fetch_asset(
    spec: str,
    days: int = MAX_FREE_DAYS,
    currency: str = "eur",
    **kwargs,
) -> PriceSeries:
    """Fetch a price series for any asset spec (``crypto:...`` or ``stock:...``).

    Bare specs are treated as crypto (CoinGecko). Stock prices come from Yahoo
    Finance in their native currency (usually USD).
    """
    kind, symbol = parse_asset(spec)
    if kind == "stock":
        return fetch_stock(symbol, days=days, **kwargs)
    if kind == "crypto":
        return fetch(days=days, currency=currency, coin=symbol, **kwargs)
    raise ValueError(f"Unknown asset type '{kind}' (use 'crypto:' or 'stock:').")
