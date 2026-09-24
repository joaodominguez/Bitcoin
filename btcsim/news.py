"""Pluggable news / sentiment analysis.

Historical, per-day news sentiment is hard to obtain for free, so this module
is designed as a clean plug-in point:

* ``SentimentProvider`` is the interface every provider implements.
* ``NeutralProvider`` is the default no-op (score 0 everywhere).
* ``KeywordSentimentProvider`` scores free-text headlines with a small lexicon
  so you can feed it your own news dump (CSV: ``date,headline``).
* ``CryptoPanicProvider`` is a thin wrapper around the CryptoPanic API for
  *current* sentiment; it needs ``CRYPTOPANIC_TOKEN`` in the environment.

A sentiment score is a float in [-1, 1]: negative = bearish, positive = bullish.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Iterable, Optional

import pandas as pd
import requests

BULLISH_WORDS = {
    "surge", "soar", "rally", "bull", "bullish", "gain", "gains", "record",
    "high", "adopt", "adoption", "approve", "approval", "etf", "institutional",
    "buy", "breakout", "moon", "up", "rise", "rises", "boom", "optimism",
    "greenlight", "inflow", "inflows", "halving", "surges", "positive",
}
BEARISH_WORDS = {
    "crash", "plunge", "plummet", "bear", "bearish", "loss", "losses", "ban",
    "hack", "hacked", "fraud", "scam", "sell", "selloff", "dump", "fear",
    "down", "fall", "falls", "drop", "slump", "regulation", "crackdown",
    "lawsuit", "collapse", "outflow", "outflows", "negative", "warning",
}


def score_text(text: str) -> float:
    """Score a single piece of text into [-1, 1] using the built-in lexicon."""
    tokens = [t.strip(".,!?:;()[]\"'").lower() for t in text.split()]
    pos = sum(1 for t in tokens if t in BULLISH_WORDS)
    neg = sum(1 for t in tokens if t in BEARISH_WORDS)
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


class SentimentProvider(ABC):
    """Interface for anything that yields a daily sentiment series."""

    @abstractmethod
    def daily_sentiment(self, index: pd.DatetimeIndex) -> pd.Series:
        """Return a sentiment score in [-1, 1] for each date in ``index``."""


class NeutralProvider(SentimentProvider):
    """Default provider: sentiment is 0 (neutral) for every day."""

    def daily_sentiment(self, index: pd.DatetimeIndex) -> pd.Series:
        return pd.Series(0.0, index=index, name="sentiment")


class KeywordSentimentProvider(SentimentProvider):
    """Scores per-day sentiment from a set of dated headlines.

    Args:
        headlines: iterable of (date, text) pairs, or a DataFrame with
            ``date`` and ``headline`` columns.
    """

    def __init__(self, headlines) -> None:
        if isinstance(headlines, pd.DataFrame):
            pairs: Iterable = zip(headlines["date"], headlines["headline"])
        else:
            pairs = headlines
        rows = []
        for date, text in pairs:
            rows.append((pd.Timestamp(date).normalize(), score_text(str(text))))
        if rows:
            frame = pd.DataFrame(rows, columns=["date", "score"])
            self._daily = frame.groupby("date")["score"].mean()
        else:
            self._daily = pd.Series(dtype=float)

    @classmethod
    def from_csv(cls, path: str) -> "KeywordSentimentProvider":
        frame = pd.read_csv(path)
        return cls(frame)

    def daily_sentiment(self, index: pd.DatetimeIndex) -> pd.Series:
        series = self._daily.reindex(index).fillna(0.0)
        series.name = "sentiment"
        return series


class FearGreedProvider(SentimentProvider):
    """Crypto Fear & Greed Index from alternative.me (free, with history).

    The raw index is 0-100 (0 = extreme fear, 100 = extreme greed). It is a
    real, market-wide sentiment gauge aggregating volatility, momentum, social
    media and trends. It is *market-wide*, so it applies to all coins.

    ``daily_sentiment`` maps the index to [-1, 1] via ``(value - 50) / 50``:
    greed -> positive (bullish momentum reading). For the classic contrarian
    reading ("buy fear, sell greed"), use ``SentimentStrategy(contrarian=True)``.
    """

    API_URL = "https://api.alternative.me/fng/"

    def __init__(
        self,
        limit: int = 0,
        session: Optional[requests.Session] = None,
        cache_dir=None,
        max_age_hours: float = 12.0,
    ) -> None:
        self.limit = limit  # 0 == full available history
        self.session = session
        self.max_age_hours = max_age_hours
        from pathlib import Path

        from .data import DEFAULT_CACHE_DIR

        self.cache_file = Path(cache_dir or DEFAULT_CACHE_DIR) / "fear_greed.csv"

    def _fetch_frame(self) -> pd.Series:
        import time as _time

        if self.cache_file.exists():
            age_h = (_time.time() - self.cache_file.stat().st_mtime) / 3600.0
            if age_h <= self.max_age_hours:
                cached = pd.read_csv(self.cache_file, parse_dates=["date"])
                return cached.set_index("date")["value"]

        sess = self.session or requests
        resp = sess.get(self.API_URL, params={"limit": self.limit}, timeout=30)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        rows = [
            (
                pd.to_datetime(int(d["timestamp"]), unit="s").normalize(),
                float(d["value"]),
            )
            for d in data
        ]
        frame = pd.DataFrame(rows, columns=["date", "value"]).sort_values("date")
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(self.cache_file, index=False)
        except OSError:
            pass
        return frame.set_index("date")["value"]

    def daily_sentiment(self, index: pd.DatetimeIndex) -> pd.Series:
        try:
            raw = self._fetch_frame()
        except Exception:  # noqa: BLE001
            if self.cache_file.exists():
                cached = pd.read_csv(self.cache_file, parse_dates=["date"])
                raw = cached.set_index("date")["value"]
            else:
                return pd.Series(0.0, index=index, name="sentiment")
        aligned = raw.reindex(index).ffill()
        series = ((aligned - 50.0) / 50.0).clip(-1.0, 1.0).fillna(0.0)
        series.name = "sentiment"
        return series


class CryptoPanicProvider(SentimentProvider):
    """Fetches *current* headlines from CryptoPanic and scores them.

    This is intended for live decision support, not historical backtests
    (the free API does not expose deep history). Requires the environment
    variable ``CRYPTOPANIC_TOKEN``.
    """

    API_URL = "https://cryptopanic.com/api/v1/posts/"

    def __init__(self, token: Optional[str] = None, currency: str = "BTC") -> None:
        self.token = token or os.environ.get("CRYPTOPANIC_TOKEN")
        self.currency = currency

    def latest_headlines(self, limit: int = 50) -> list[str]:
        if not self.token:
            raise RuntimeError(
                "CryptoPanic requires CRYPTOPANIC_TOKEN in the environment."
            )
        params = {
            "auth_token": self.token,
            "currencies": self.currency,
            "public": "true",
        }
        resp = requests.get(self.API_URL, params=params, timeout=30)
        resp.raise_for_status()
        results = resp.json().get("results", [])
        return [r.get("title", "") for r in results][:limit]

    def current_score(self, limit: int = 50) -> float:
        headlines = self.latest_headlines(limit=limit)
        if not headlines:
            return 0.0
        scores = [score_text(h) for h in headlines]
        return sum(scores) / len(scores)

    def daily_sentiment(self, index: pd.DatetimeIndex) -> pd.Series:
        # Apply the current score to the most recent day only; history stays neutral.
        series = pd.Series(0.0, index=index, name="sentiment")
        if len(series):
            series.iloc[-1] = self.current_score()
        return series
