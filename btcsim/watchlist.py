"""Standing watchlist and virtual decisions driven by news.

The operator authorized this process to keep reading public headlines, add
crypto or stocks to a watchlist, and record a virtual allocation decision.
Nothing here places a real order. Decisions are educational and use virtual
capital only.

State lives under ``state/`` (override with ``BTCSIM_STATE``):

* ``watchlist.json`` — assets being watched, with the headline that added them
* ``last_decision.json`` — latest target weights and buy/sell/hold actions
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from . import allocation as alloc_mod
from urllib.parse import quote

from .assets import CATALOG, YAHOO_MACRO, YAHOO_SYMBOL
from .news import score_text

# RSS mirrors of the public pages. Yahoo's HTML topic/quote pages are the same
# news as these feeds, which a program can actually read.
RSS_FEEDS = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("OilPrice", "https://oilprice.com/rss/main"),
)
YAHOO_LATEST = "https://finance.yahoo.com/news/rssindex"
YAHOO_HEADLINE = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbols}&region=US&lang=en-US"
DEFAULT_FEEDS = tuple(url for _, url in RSS_FEEDS)

# A headline about rates, inflation or the dollar moves assets even when it
# never says their name.
MACRO_LINKS = (
    (("federal reserve", "rate hike", "rate cut", "treasury yield", "bond yield"), ("bitcoin", "ethereum", "stock:SPY", "stock:GLD")),
    (("inflation", "consumer price"), ("bitcoin", "stock:SPY", "stock:GLD")),
    (("dollar index", "us dollar"), ("bitcoin", "stock:GLD")),
)

# (asset spec, phrases that count as a mention)
UNIVERSE: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (item["spec"], item["needles"]) for item in CATALOG
)

SEED_SPECS = (
    "bitcoin",
    "ethereum",
    "stock:SPY",
    "stock:QQQ",
    "stock:AAPL",
    "stock:MSFT",
    "stock:NVDA",
    "stock:GLD",
    "stock:USO",
)
MAX_ASSETS = 14
ADD_THRESHOLD = 0.2
CAUTION_THRESHOLD = -0.2

# Class buckets for daily rotation (equity / ETF / commodities / tech / crypto).
CLASS_OF = {
    "bitcoin": "crypto",
    "ethereum": "crypto",
    "solana": "crypto",
    "cardano": "crypto",
    "dogecoin": "crypto",
    "ripple": "crypto",
    "binancecoin": "crypto",
    "aapl": "tech",
    "msft": "tech",
    "googl": "tech",
    "nvda": "tech",
    "meta": "tech",
    "amzn": "consumer",
    "tsla": "auto",
    "spy": "etf",
    "qqq": "etf",
    "gld": "commodity",
    "slv": "commodity",
    "uso": "commodity",
}
CLASS_BUDGET = {
    "tech": 0.38,
    "etf": 0.22,
    "commodity": 0.18,
    "consumer": 0.12,
    "auto": 0.08,
    "crypto": 0.12,  # still light; deep BTC dips stay on the sleeve
}
MAX_NAMES_IN_BOOK = 6


def state_dir() -> Path:
    path = Path(os.environ.get("BTCSIM_STATE", "state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def book_lock(directory: Path):
    """Exclusive lock so the hourly book and the intraday sleeve do not clobber each other."""
    directory.mkdir(parents=True, exist_ok=True)
    handle = (directory / "book.lock").open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _empty() -> dict:
    return {
        "authorization": (
            "Autonomia permanente, dada pelo operador, apenas para dinheiro virtual: "
            "ler noticias publicas, acrescentar cripto ou acoes a esta lista e "
            "registar uma decisao de alocacao. Nao envia ordens reais."
        ),
        "assets": [
            {
                "spec": spec,
                "added_at": _now(),
                "reason": "lista inicial",
                "headline": "",
                "score": 0.0,
                "caution": False,
            }
            for spec in SEED_SPECS
        ],
    }


def load(path: Path | None = None) -> dict:
    path = path or (state_dir() / "watchlist.json")
    if not path.exists():
        data = _empty()
        save(data, path)
        return data
    data = json.loads(path.read_text(encoding="utf-8"))
    # Keep older books current: add any missing seed specs (QQQ, commodities, …).
    have = {a.get("spec") for a in data.get("assets") or []}
    changed = False
    for spec in SEED_SPECS:
        if spec in have:
            continue
        if len(data.get("assets") or []) >= MAX_ASSETS:
            break
        data.setdefault("assets", []).append({
            "spec": spec,
            "added_at": _now(),
            "reason": "universo multi-classe",
            "headline": "",
            "score": 0.0,
            "caution": False,
        })
        changed = True
    if changed:
        save(data, path)
    return data


def save(data: dict, path: Path | None = None) -> None:
    path = path or (state_dir() / "watchlist.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def mentions(text: str) -> list[str]:
    """Return asset specs named in ``text``."""
    lowered = text.lower()
    found = []
    for spec, needles in UNIVERSE:
        for needle in needles:
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", lowered):
                found.append(spec)
                break
    return found


def macro_specs(text: str) -> list[str]:
    """Assets a macro headline can move even without naming them."""
    lowered = text.lower()
    found = []
    for needles, specs in MACRO_LINKS:
        if any(needle in lowered for needle in needles):
            for spec in specs:
                if spec not in found:
                    found.append(spec)
    return found


def influenced_specs(text: str) -> list[str]:
    found = mentions(text)
    for spec in macro_specs(text):
        if spec not in found:
            found.append(spec)
    return found


def apply_headlines(data: dict, headlines: list[str]) -> dict:
    """Update the watchlist from headlines. Returns a summary of changes."""
    added, cautioned, skipped = [], [], []
    known = {a["spec"]: a for a in data["assets"]}

    for headline in headlines:
        score = score_text(headline)
        for spec in influenced_specs(headline):
            entry = known.get(spec)
            if score >= ADD_THRESHOLD and entry is None:
                if len(data["assets"]) >= MAX_ASSETS:
                    skipped.append(spec)
                    continue
                entry = {
                    "spec": spec,
                    "added_at": _now(),
                    "reason": "noticia bullish",
                    "headline": headline,
                    "score": round(score, 3),
                    "caution": False,
                }
                data["assets"].append(entry)
                known[spec] = entry
                added.append(spec)
            elif score <= CAUTION_THRESHOLD and entry is not None:
                entry["caution"] = True
                entry["headline"] = headline
                entry["score"] = round(score, 3)
                entry["reason"] = "noticia bearish (reduzir na proxima decisao)"
                cautioned.append(spec)
            elif score >= ADD_THRESHOLD and entry is not None and entry.get("caution"):
                entry["caution"] = False
                entry["headline"] = headline
                entry["score"] = round(score, 3)
                entry["reason"] = "noticia bullish (cautela levantada)"

    return {"added": added, "cautioned": cautioned, "skipped": skipped}


def collect_titles(per_source: list[list[str]], per_feed: int = 12, limit: int = 48) -> list[str]:
    """Keep a slice from each source so one feed cannot crowd out the others."""
    titles: list[str] = []
    for source in per_source:
        for title in source[:per_feed]:
            if len(titles) >= limit:
                return titles
            titles.append(title)
    return titles


def rss_titles(payload: bytes, limit: int) -> list[str]:
    root = ET.fromstring(payload)
    titles = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        titles.append(title)
        if len(titles) >= limit:
            break
    return titles


def yahoo_feed_urls(batch: int = 4) -> list[tuple[str, str]]:
    """RSS for the same news as finance.yahoo.com/topic/latest-news and /quote/TICKER/news."""
    symbols = [YAHOO_SYMBOL[item["spec"]] for item in CATALOG] + list(YAHOO_MACRO)
    feeds = [("Yahoo Finance · últimas", YAHOO_LATEST)]
    for start in range(0, len(symbols), batch):
        chunk = symbols[start:start + batch]
        encoded = ",".join(quote(symbol, safe="") for symbol in chunk)
        label = "Yahoo Finance · " + ", ".join(chunk)
        feeds.append((label, YAHOO_HEADLINE.format(symbols=encoded)))
    return feeds


def news_sources() -> list[dict]:
    """Human pages, shown on the dashboard. The fetcher reads their RSS."""
    return [
        {"name": "Yahoo Finance · últimas", "url": "https://finance.yahoo.com/topic/latest-news/"},
        {"name": "Yahoo Finance · Bitcoin", "url": "https://finance.yahoo.com/quote/BTC-USD/news/"},
        {"name": "Yahoo Finance · Nvidia", "url": "https://finance.yahoo.com/quote/NVDA/news/"},
        {"name": "Yahoo Finance · ouro", "url": "https://finance.yahoo.com/quote/GC=F/news/"},
        {"name": "Yahoo Finance · petróleo", "url": "https://finance.yahoo.com/quote/CL=F/news/"},
        {"name": "CoinDesk", "url": "https://www.coindesk.com/"},
        {"name": "Cointelegraph", "url": "https://cointelegraph.com/"},
        {"name": "MarketWatch", "url": "https://www.marketwatch.com/"},
        {"name": "OilPrice", "url": "https://oilprice.com/"},
    ]


def _pull_rss(source: str, url: str, per_feed: int) -> list[dict]:
    try:
        resp = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 btcsim/0.1"},
        )
        resp.raise_for_status()
        return [{"title": title, "source": source} for title in rss_titles(resp.content, per_feed)]
    except Exception:  # noqa: BLE001
        return []


def fetch_news_items(per_feed: int = 8, limit: int = 96) -> list[dict]:
    """Titles from crypto, stock, commodity and macro sources. One source cannot fill the list."""
    groups = []
    for source, url in RSS_FEEDS:
        items = _pull_rss(source, url, per_feed)
        if items:
            groups.append(items)
    for source, url in yahoo_feed_urls():
        items = _pull_rss(source, url, per_feed)
        if items:
            groups.append(items)
    flat = collect_titles([[item["title"] for item in group] for group in groups], per_feed=per_feed, limit=limit)
    # Rebuild source tags for the titles that survived the cap, in the same order.
    lookup = {}
    for group in groups:
        for item in group:
            lookup.setdefault(item["title"], item["source"])
    return [{"title": title, "source": lookup.get(title, "")} for title in flat]


def fetch_headlines(feeds: tuple[str, ...] = DEFAULT_FEEDS, limit: int = 96, per_feed: int = 8) -> list[str]:
    """Pull recent titles. ``feeds`` is kept for tests; the live path reads every source."""
    if feeds is not DEFAULT_FEEDS:
        per_source = []
        for url in feeds:
            items = _pull_rss("feed", url, per_feed)
            if items:
                per_source.append([item["title"] for item in items])
        return collect_titles(per_source, per_feed=per_feed, limit=limit)
    return [item["title"] for item in fetch_news_items(per_feed=per_feed, limit=limit)]


def refresh(path: Path | None = None, headlines: list[str] | None = None) -> dict:
    """Read news (or use ``headlines``) and persist watchlist changes."""
    path = path or (state_dir() / "watchlist.json")
    data = load(path)
    items = None
    if headlines is None:
        items = fetch_news_items()
        used = [item["title"] for item in items]
    else:
        used = headlines
    summary = apply_headlines(data, used)
    data["updated_at"] = _now()
    data["headlines_read"] = len(used)
    if items is not None:
        data["recent_headlines"] = items
    save(data, path)
    summary["watchlist"] = data
    return summary


def _actions(
    previous: dict[str, float],
    target: dict[str, float],
    capital: float,
    prices: dict[str, float] | None = None,
    fills: list[dict] | None = None,
) -> list[dict]:
    names = sorted(set(previous) | set(target))
    prices = prices or {}
    by_fill = {(f.get("asset"), f.get("side")): f for f in (fills or [])}
    actions = []
    for name in names:
        before = float(previous.get(name, 0.0))
        after = float(target.get(name, 0.0))
        delta = after - before
        if after == 0 and before == 0:
            continue
        if abs(delta) < 0.012 and after > 0 and before > 0:
            side = "HOLD"
        elif delta > 0:
            side = "BUY"
        elif delta < 0:
            side = "SELL"
        else:
            side = "HOLD"
        fill = by_fill.get((name, side)) or {}
        px = fill.get("price_eur")
        if px is None:
            px = prices.get(name)
        traded = fill.get("amount_eur")
        if traded is None and side in {"BUY", "SELL"}:
            traded = round(abs(delta) * capital, 2)
        actions.append({
            "asset": name,
            "action": side,
            "weight_before_pct": round(before * 100, 2),
            "weight_after_pct": round(after * 100, 2),
            "amount": round(after * capital, 2),
            "traded_eur": traded,
            "price_eur": round(float(px), 4) if px is not None else None,
            "quantity": fill.get("units"),
            "pnl_eur": fill.get("pnl_eur"),
        })
    return actions


def study_patterns(prices) -> list[dict]:
    """Read trend, RSI and recent drawdown for each column.

    Returns a plain-language stance per asset. These are pattern readings for
    the virtual book, not a guarantee of what the price will do next.
    """
    from . import indicators

    studies = []
    for name in prices.columns:
        series = prices[name].dropna()
        if len(series) < 30:
            continue
        last = float(series.iloc[-1])
        sma_fast = indicators.sma(series, 20).iloc[-1]
        sma_slow = indicators.sma(series, 50).iloc[-1] if len(series) >= 50 else float("nan")
        rsi_now = float(indicators.rsi(series, 14).iloc[-1])
        ret_30 = float(series.iloc[-1] / series.iloc[-30] - 1.0)
        ret_1 = float(series.iloc[-1] / series.iloc[-2] - 1.0) if len(series) >= 2 else 0.0
        ret_5 = float(series.iloc[-1] / series.iloc[-6] - 1.0) if len(series) >= 6 else ret_1
        window = series.iloc[-60:] if len(series) >= 60 else series
        drawdown = last / float(window.max()) - 1.0

        above_fast = last > sma_fast
        if sma_slow == sma_slow and above_fast and sma_fast > sma_slow:
            trend = "alta confirmada"
        elif sma_slow == sma_slow and not above_fast and sma_fast < sma_slow:
            trend = "baixa confirmada"
        elif above_fast:
            trend = "alta curta"
        else:
            trend = "baixa curta"

        if rsi_now >= 70:
            stance = "não perseguir"
            reading = (
                "O RSI está acima de 70, zona de sobrecompra. "
                "O padrão recente já esticou. Eu não comprava agora."
            )
        elif rsi_now <= 30 and trend.startswith("baixa"):
            stance = "não entrar com tudo"
            reading = (
                "O preço está fraco e o RSI está em sobrevenda. "
                "Isso não é, por si, um sinal para comprar o lote inteiro. "
                "No máximo uma fração pequena, e aos poucos."
            )
        elif trend.startswith("alta") and 35 <= rsi_now < 70:
            stance = "manter ou reforçar aos poucos"
            reading = (
                "A tendência curta é de subida e o RSI não está num extremo. "
                "Eu mantinha a posição e, se o peso estivesse baixo, reforçava com compras pequenas."
            )
        elif trend.startswith("baixa"):
            stance = "reduzir"
            reading = (
                "O preço está abaixo da média curta. "
                "Eu reduzia ou ficava de fora até o preço voltar a fechar acima dessa média."
            )
        else:
            stance = "esperar"
            reading = "Os sinais não apontam na mesma direção. Eu não mudava a posição por este padrão."

        edge = round(
            ret_1 * 100 * 2.2
            + ret_5 * 100 * 1.1
            + (8.0 if trend.startswith("alta") else -6.0)
            + (0.0 if 40 <= rsi_now <= 65 else (-10.0 if rsi_now >= 70 else -4.0)),
            2,
        )
        studies.append({
            "asset": name,
            "trend": trend,
            "rsi": round(rsi_now, 1),
            "return_1d_pct": round(ret_1 * 100, 2),
            "return_5d_pct": round(ret_5 * 100, 2),
            "return_30d_pct": round(ret_30 * 100, 1),
            "drawdown_pct": round(drawdown * 100, 1),
            "edge_score": edge,
            "stance": stance,
            "reading": reading,
            "class": CLASS_OF.get(str(name).lower(), "other"),
        })
    return studies


def _asset_class(name: str) -> str:
    key = str(name).replace("stock:", "").lower()
    return CLASS_OF.get(key, "other")


def daily_rotation_weights(studies: list[dict], eligible: set[str] | list[str]) -> dict[str, float]:
    """Pick a diversified book tilted to short-term edge (virtual daily rotation).

    Prefers names with positive 1d/5d momentum, healthy RSI, and uptrend — across
    tech, ETFs, commodities and a light crypto sleeve. Takes profit / cuts losers
    by simply leaving weak names at weight 0.
    """
    allowed_bare = {str(x).replace("stock:", "").lower() for x in eligible}
    ranked = []
    for item in studies:
        asset = str(item["asset"])
        bare = asset.replace("stock:", "").lower()
        if bare not in allowed_bare:
            continue
        if item.get("stance") == "não perseguir" and float(item.get("return_1d_pct") or 0) < 0.05:
            continue
        ranked.append(item)
    ranked.sort(key=lambda x: float(x.get("edge_score") or 0), reverse=True)

    def _fill(candidates: list[dict], *, min_edge: float) -> dict[str, float]:
        picked: list[dict] = []
        class_used: dict[str, float] = {}
        for item in candidates:
            if float(item.get("edge_score") or 0) < min_edge:
                continue
            cls = item.get("class") or _asset_class(item["asset"])
            budget = CLASS_BUDGET.get(cls, 0.10)
            used = class_used.get(cls, 0.0)
            if used >= budget - 1e-6:
                continue
            if len(picked) >= MAX_NAMES_IN_BOOK:
                break
            if cls == "crypto" and float(item.get("edge_score") or 0) < 1.5:
                continue
            room = budget - used
            edge = max(0.5, float(item.get("edge_score") or 0.5))
            slot = min(room, MAX_SINGLE_WEIGHT, 0.08 + edge * 0.012)
            if slot < 0.04:
                continue
            picked.append({**item, "class": cls, "slot": slot})
            class_used[cls] = used + slot
        if not picked:
            return {}
        raw = {str(p["asset"]): float(p["slot"]) for p in picked}
        total = sum(raw.values())
        max_invested = 1.0 - MIN_CASH_WEIGHT
        if total > max_invested and total > 0:
            scale = max_invested / total
            raw = {k: v * scale for k, v in raw.items()}
        return {k: round(v, 6) for k, v in raw.items() if v > 1e-6}

    # Prefer clear short-term edge; if the tape is quiet, still rotate the least-bad names.
    weights = _fill(ranked, min_edge=0.0)
    if not weights:
        weights = _fill(ranked, min_edge=-8.0)
    if not weights and ranked:
        top = ranked[0]
        weights = {str(top["asset"]): 0.25}
    return weights


def portfolio_advice(studies: list[dict], actions: list[dict], cautious: list[str]) -> str:
    """One paragraph: what the automation would do with the virtual capital."""
    if not studies and not actions:
        if cautious:
            return (
                "Eu ficava em cash virtual. As notícias recentes são negativas "
                "para o que está na lista, por isso não abriria posição."
            )
        return "Ainda não há padrões suficientes para uma leitura."

    sentences = []
    by_asset = {item["asset"]: item for item in studies}
    for action in actions:
        if action["weight_after_pct"] < 1 and action["action"] != "SELL":
            continue
        pattern = by_asset.get(action["asset"])
        stance = pattern["stance"] if pattern else "seguir o peso da carteira"
        verb = {
            "BUY": "aumentava o peso",
            "SELL": "reduzia",
            "HOLD": "mantinha",
        }[action["action"]]
        sentences.append(
            f"Em {action['asset']} {verb} para {action['weight_after_pct']:.0f}% ({stance})."
        )
    if cautious:
        sentences.append(
            "Ficava de fora de "
            + ", ".join(cautious)
            + " por causa de notícias negativas recentes."
        )
    if not sentences:
        return "Eu não mexia na carteira virtual neste ciclo."
    return " ".join(sentences)


def _load_book(path: Path, initial: float) -> dict:
    book_path = path.parent / "book.json"
    if book_path.exists():
        book = json.loads(book_path.read_text(encoding="utf-8"))
        book.setdefault("initial", initial)
        book.setdefault("cash", initial)
        book.setdefault("units", {})
        book.setdefault("last_prices", {})
        return book
    return {"initial": initial, "cash": initial, "units": {}, "last_prices": {}}


def _save_book(path: Path, book: dict) -> None:
    (path.parent / "book.json").write_text(
        json.dumps(book, indent=2), encoding="utf-8"
    )


def _price_map(frame) -> dict[str, float]:
    if frame is None or frame.empty:
        return {}
    last = frame.iloc[-1]
    prices = {}
    for name in frame.columns:
        value = last[name]
        if pd.notna(value) and float(value) > 0:
            prices[name] = float(value)
    return prices


def _equity(book: dict, prices: dict[str, float]) -> float:
    total = float(book.get("cash", 0.0))
    known = {**book.get("last_prices", {}), **prices}
    for asset, units in book.get("units", {}).items():
        px = known.get(asset)
        if px:
            total += float(units) * float(px)
    return total


CRYPTO_NAMES = {
    "bitcoin", "ethereum", "solana", "cardano", "dogecoin", "ripple", "binancecoin",
}
MAX_SINGLE_WEIGHT = 0.40
MAX_CRYPTO_WEIGHT = 0.18  # strategic sleeve stays light while BTC looks jumpy
MIN_CASH_WEIGHT = 0.08


def apply_risk_limits(weights: dict[str, float]) -> dict[str, float]:
    """Clip single-name and crypto exposure; leave room for cash."""
    if not weights:
        return {}
    capped = {k: min(float(v), MAX_SINGLE_WEIGHT) for k, v in weights.items() if float(v) > 0}
    crypto_total = sum(v for k, v in capped.items() if k.lower() in CRYPTO_NAMES)
    if crypto_total > MAX_CRYPTO_WEIGHT and crypto_total > 0:
        scale = MAX_CRYPTO_WEIGHT / crypto_total
        for name in list(capped):
            if name.lower() in CRYPTO_NAMES:
                capped[name] *= scale
    total = sum(capped.values())
    max_invested = 1.0 - MIN_CASH_WEIGHT
    if total > max_invested and total > 0:
        scale = max_invested / total
        capped = {k: v * scale for k, v in capped.items()}
    return {k: round(v, 6) for k, v in capped.items() if v > 1e-6}


def _breakeven(cost: float, fee_rate: float) -> float:
    return float(cost) * (1 + fee_rate) / (1 - fee_rate)


def _rebalance(
    book: dict,
    weights: dict[str, float],
    prices: dict[str, float],
    fee_rate: float = 0.001,
) -> tuple[float, list[dict]]:
    """Move the virtual book to ``weights``. Returns (equity_before, fills).

    Each fill carries price, quantity, notional and (on sells) realized PnL so
    the movements ledger can show lucro/prejuízo clearly. Bitcoin is not sold
    below its cost after fees.
    """
    merged = {**book.get("last_prices", {}), **prices}
    equity = _equity(book, merged)
    units = {k: float(v) for k, v in book.get("units", {}).items()}
    cash = float(book.get("cash", 0.0))
    costs = {k: float(v) for k, v in (book.get("cost_eur") or {}).items()}
    realized = float(book.get("realized_pnl_eur") or 0.0)
    fills: list[dict] = []
    for asset, qty in units.items():
        if asset not in costs and merged.get(asset) and qty > 0:
            costs[asset] = float(merged[asset])
    targets = {name: equity * float(weight) for name, weight in weights.items()}

    for asset, qty in list(units.items()):
        px = merged.get(asset)
        if not px:
            continue
        target = targets.get(asset, 0.0)
        current = qty * px
        if current > target + 0.01:
            if asset == "bitcoin":
                cost = costs.get(asset)
                if cost and px < _breakeven(cost, fee_rate):
                    continue
            sell_val = current - target
            sold_units = sell_val / px
            avg_cost = float(costs.get(asset, px))
            proceeds = sell_val * (1 - fee_rate)
            pnl = proceeds - sold_units * avg_cost
            realized += pnl
            cash += proceeds
            units[asset] = target / px if target > 0 else 0.0
            if units[asset] <= 1e-10:
                costs.pop(asset, None)
            fills.append({
                "side": "SELL",
                "asset": asset,
                "units": sold_units,
                "price_eur": float(px),
                "amount_eur": round(proceeds, 2),
                "pnl_eur": round(pnl, 2),
                "cost_eur": round(sold_units * avg_cost, 2),
            })

    for asset, target in targets.items():
        px = merged.get(asset)
        if not px:
            continue
        current = units.get(asset, 0.0) * px
        if target > current + 0.01 and cash > 0:
            buy_val = min(target - current, cash / (1 + fee_rate))
            old_units = units.get(asset, 0.0)
            new_units = old_units + buy_val / px
            old_cost = costs.get(asset, px)
            costs[asset] = (old_units * old_cost + buy_val) / new_units
            cash -= buy_val * (1 + fee_rate)
            units[asset] = new_units
            fills.append({
                "side": "BUY",
                "asset": asset,
                "units": buy_val / px,
                "price_eur": float(px),
                "amount_eur": round(buy_val * (1 + fee_rate), 2),
                "pnl_eur": None,
                "cost_eur": round(buy_val, 2),
            })

    book["units"] = {k: v for k, v in units.items() if v > 1e-10}
    book["cash"] = cash
    book["last_prices"] = merged
    book["cost_eur"] = {k: v for k, v in costs.items() if k in book["units"]}
    book["realized_pnl_eur"] = round(realized, 2)
    return equity, fills


def open_positions(
    book: dict,
    *,
    total_capital: float,
    tape: dict | None = None,
    actions: list[dict] | None = None,
) -> list[dict]:
    """Real open lots for the desk cards (book units + BTC sleeve lots)."""
    by_action = {}
    for action in actions or []:
        by_action[str(action.get("asset") or "").lower()] = action

    prices = book.get("last_prices") or {}
    costs = book.get("cost_eur") or {}
    units = book.get("units") or {}
    rows: list[dict] = []
    for asset, qty in units.items():
        qty = float(qty)
        if qty <= 1e-10:
            continue
        px = float(prices.get(asset) or 0.0)
        if px <= 0:
            continue
        value = qty * px
        avg = float(costs.get(asset) or px)
        cost_basis = qty * avg
        pnl = value - cost_basis
        act = by_action.get(str(asset).lower()) or {}
        rows.append({
            "asset": asset,
            "origin": "carteira",
            "units": round(qty, 8),
            "price_eur": round(px, 4),
            "amount": round(value, 2),
            "cost_eur": round(cost_basis, 2),
            "pnl_eur": round(pnl, 2),
            "pnl_pct": round((pnl / cost_basis) * 100, 2) if cost_basis else 0.0,
            "weight_pct": round((value / total_capital) * 100, 2) if total_capital else 0.0,
            "action": act.get("action") or "HOLD",
            "open": True,
        })

    if tape:
        spot_eur = None
        if tape.get("last_spot"):
            spot_eur = tape["last_spot"].get("eur")
        for lot in tape.get("lots") or []:
            units_lot = float(lot.get("units") or 0)
            if units_lot <= 0:
                continue
            px = float(spot_eur or lot.get("price_eur") or 0)
            spent = float(lot.get("spent_eur") or 0)
            value = units_lot * px
            pnl = value - spent
            rows.append({
                "asset": "bitcoin",
                "origin": "sleeve_btc",
                "units": round(units_lot, 8),
                "price_eur": round(px, 4),
                "amount": round(value, 2),
                "cost_eur": round(spent, 2),
                "pnl_eur": round(pnl, 2),
                "pnl_pct": round((pnl / spent) * 100, 2) if spent else 0.0,
                "weight_pct": round((value / total_capital) * 100, 2) if total_capital else 0.0,
                "action": "HOLD",
                "open": True,
                "level_usd": lot.get("level_usd"),
            })

    rows.sort(key=lambda r: float(r.get("amount") or 0), reverse=True)
    return rows


def decide(
    path: Path | None = None,
    capital: float = 10_000.0,
    currency: str = "eur",
    method: str = "daily_rotation",
    prices=None,
) -> dict:
    """Allocate virtual capital across the watchlist and store the decision.

    Default ``daily_rotation`` tilts to short-term edge across tech / ETF /
    commodities / crypto (light). Assets flagged ``caution`` stay at weight 0.
    """
    path = path or (state_dir() / "watchlist.json")
    data = load(path)
    eligible = [a["spec"] for a in data["assets"] if not a.get("caution")]
    cautious = [a["spec"] for a in data["assets"] if a.get("caution")]

    from .allocation import _spec_name

    all_specs = [a["spec"] for a in data["assets"]]
    frame = None
    if prices is not None:
        frame = prices
    elif all_specs:
        frame = alloc_mod.load_prices(all_specs, currency=currency, days=365)

    studies = study_patterns(frame) if frame is not None and not frame.empty else []
    weights: dict[str, float] = {}
    exp_return_pct = None
    note = (
        "Rotação diária virtual: reforço o que sobe com RSI saudável "
        "(tech, ETF, commodities; cripto leve) e corto o que perde força."
    )
    if not eligible:
        note = "Todas as posicoes estao em cautela. Decisao: ficar em cash virtual."
    elif frame is not None:
        wanted = [_spec_name(spec) for spec in eligible]
        book = frame[[c for c in wanted if c in frame.columns]]
        names = list(book.columns)
        eligible_names = set(names)
        if method == "daily_rotation":
            weights = daily_rotation_weights(studies, eligible_names)
            if weights:
                held = [s for s in studies if s["asset"] in weights]
                if held:
                    # Annualise a blend of 5d momentum as a rough forecast label.
                    avg5 = sum(float(s.get("return_5d_pct") or 0) for s in held) / len(held)
                    exp_return_pct = round(avg5 * (252 / 5), 2)
                note = (
                    "Rotação diária por classes (tech / ETF / commodities / cripto leve): "
                    f"{len(weights)} posições com melhor edge de curto prazo."
                )
            elif len(names) == 1:
                weights = {names[0]: 1.0}
            elif len(names) >= 2:
                method = "min_variance"
        if method != "daily_rotation" and not weights:
            if len(names) == 1:
                weights = {names[0]: 1.0}
            elif len(names) >= 2:
                returns = alloc_mod.daily_returns(book)
                result = alloc_mod.optimize(names, returns, method=method, n_samples=8_000)
                weights = {k: float(v) for k, v in result.weights.items()}
                exp_return_pct = result.exp_return_pct
                note = (
                    f"Alocacao {method} depois da leitura de padroes: "
                    f"retorno esperado {result.exp_return_pct:+.1f}%, "
                    f"volatilidade {result.exp_volatility_pct:.1f}%."
                )

    weights = apply_risk_limits(weights)
    if weights and ("Rotação" in note or note.startswith("Alocacao")):
        note += (
            f" Limites: max {MAX_SINGLE_WEIGHT:.0%} por ativo, "
            f"max {MAX_CRYPTO_WEIGHT:.0%} cripto, min {MIN_CASH_WEIGHT:.0%} cash."
        )

    decision_path = path.parent / "last_decision.json"
    previous = {}
    previous_decision = {}
    if decision_path.exists():
        previous_decision = json.loads(decision_path.read_text(encoding="utf-8"))
        previous = previous_decision.get("weights", {})

    spot = _price_map(frame)
    if exp_return_pct is None and studies:
        held = [item for item in studies if item["asset"] in weights]
        if held:
            exp_return_pct = round(
                sum(item["return_30d_pct"] for item in held) / len(held) * 12, 2
            )
    from .tape import load_tape, mark_to_market

    fills: list[dict] = []
    with book_lock(path.parent):
        book = _load_book(path, capital)
        capital_atual = round(
            _equity(book, {**book.get("last_prices", {}), **spot}) + mark_to_market(path.parent),
            2,
        )
        previsao = round(capital_atual * (1 + (exp_return_pct or 0) / 100), 2)
        _equity_before, fills = _rebalance(book, weights, spot)
        capital_atual = round(
            _equity(book, book.get("last_prices") or {}) + mark_to_market(path.parent),
            2,
        )
        previsao = round(capital_atual * (1 + (exp_return_pct or 0) / 100), 2)
        _save_book(path, book)
        tape_state = load_tape(path.parent)

    actions = _actions(previous, weights, capital_atual, prices=spot, fills=fills)
    positions = open_positions(
        book,
        total_capital=capital_atual,
        tape=tape_state,
        actions=actions,
    )
    decision = {
        "at": _now(),
        "virtual_capital": capital,
        "capital_inicial": round(float(book["initial"]), 2),
        "capital_atual": capital_atual,
        "cash_eur": round(float(book.get("cash") or 0.0), 2),
        "realized_pnl_eur": round(float(book.get("realized_pnl_eur") or 0.0), 2),
        "unrealized_pnl_eur": round(sum(float(p.get("pnl_eur") or 0) for p in positions), 2),
        "previsao": previsao,
        "previsao_retorno_pct": exp_return_pct,
        "previsao_horizonte": "12 meses",
        "currency": currency.upper(),
        "method": method,
        "note": note,
        "disclaimer": "Decisao virtual. Nao e uma ordem nem aconselhamento financeiro.",
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "cautious": cautious,
        "actions": actions,
        "fills": [
            {
                "side": f["side"],
                "asset": f["asset"],
                "units": round(float(f["units"]), 8),
                "price_eur": round(float(f["price_eur"]), 4),
                "amount_eur": f["amount_eur"],
                "pnl_eur": f.get("pnl_eur"),
            }
            for f in fills
        ],
        "open_positions": positions,
        "patterns": studies,
        "risk_limits": {
            "max_single": MAX_SINGLE_WEIGHT,
            "max_crypto": MAX_CRYPTO_WEIGHT,
            "min_cash": MIN_CASH_WEIGHT,
        },
    }
    decision["advice"] = portfolio_advice(
        studies, decision["actions"], cautious
    )
    decision_path.write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    with (path.parent / "decision_log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(decision, ensure_ascii=False) + "\n")
    try:
        from .movements import record_book_decision

        record_book_decision(path.parent, decision)
    except Exception as exc:  # noqa: BLE001
        print(f"movimentos falharam: {exc}")
    try:
        from .history import record_equity

        record_equity(path.parent, capital_atual, source="decide")
    except Exception as exc:  # noqa: BLE001
        print(f"histórico falhou: {exc}")
    try:
        from .notify import send_decision

        send_decision(decision, state_dir=path.parent)
    except Exception as exc:  # noqa: BLE001
        print(f"push falhou: {exc}")
    return decision


def run_once(path: Path | None = None, headlines: list[str] | None = None) -> dict:
    summary = refresh(path, headlines=headlines)
    decision = decide(path)
    return {"changes": {k: summary[k] for k in ("added", "cautioned", "skipped")}, "decision": decision}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="btcsim.watchlist",
        description="Le noticias, atualiza a watchlist e regista uma decisao virtual.",
    )
    parser.add_argument("--refresh", action="store_true", help="So ler noticias e atualizar a lista.")
    parser.add_argument("--decide", action="store_true", help="So calcular a decisao virtual atual.")
    parser.add_argument("--loop", action="store_true", help="Repetir para sempre (uso no servidor).")
    parser.add_argument("--every-hours", type=float, default=1.0)
    parser.add_argument("--capital", type=float, default=10_000.0)
    args = parser.parse_args(argv)

    def cycle() -> None:
        if args.decide and not args.refresh and not args.loop:
            print(json.dumps(decide(capital=args.capital), indent=2, ensure_ascii=False))
            return
        if args.refresh and not args.loop:
            summary = refresh()
            print(json.dumps({k: summary[k] for k in ("added", "cautioned", "skipped", "headlines_read") if k in summary or True}, indent=2, ensure_ascii=False))
            if not args.decide:
                return
        result = run_once()
        print(json.dumps(result["changes"], indent=2, ensure_ascii=False))
        print(json.dumps(result["decision"], indent=2, ensure_ascii=False))

    if args.loop:
        print(f"Watchlist autonoma a cada {args.every_hours}h (dinheiro virtual). Ctrl+C para parar.")
        while True:
            try:
                cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"ciclo falhou: {exc}")
            time.sleep(max(0.1, args.every_hours) * 3600)
    else:
        cycle()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
