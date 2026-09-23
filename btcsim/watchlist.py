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
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import allocation as alloc_mod
from .news import score_text

DEFAULT_FEEDS = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
)

# (asset spec, phrases that count as a mention)
UNIVERSE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bitcoin", ("bitcoin", "btc")),
    ("ethereum", ("ethereum", "ether", "eth")),
    ("solana", ("solana", "sol")),
    ("cardano", ("cardano", "ada")),
    ("dogecoin", ("dogecoin", "doge")),
    ("ripple", ("ripple", "xrp")),
    ("binancecoin", ("binance coin", "bnb")),
    ("stock:AAPL", ("aapl", "apple")),
    ("stock:MSFT", ("msft", "microsoft")),
    ("stock:GOOGL", ("googl", "google", "alphabet")),
    ("stock:AMZN", ("amzn", "amazon")),
    ("stock:NVDA", ("nvda", "nvidia")),
    ("stock:TSLA", ("tsla", "tesla")),
    ("stock:META", ("meta platforms", "facebook")),
    ("stock:SPY", ("s&p 500", "s&p500", "sp500")),
)

SEED_SPECS = ("bitcoin", "ethereum", "stock:SPY", "stock:AAPL", "stock:MSFT")
MAX_ASSETS = 12
ADD_THRESHOLD = 0.2
CAUTION_THRESHOLD = -0.2


def state_dir() -> Path:
    path = Path(os.environ.get("BTCSIM_STATE", "state"))
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    return json.loads(path.read_text(encoding="utf-8"))


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


def apply_headlines(data: dict, headlines: list[str]) -> dict:
    """Update the watchlist from headlines. Returns a summary of changes."""
    added, cautioned, skipped = [], [], []
    known = {a["spec"]: a for a in data["assets"]}

    for headline in headlines:
        score = score_text(headline)
        for spec in mentions(headline):
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


def fetch_headlines(feeds: tuple[str, ...] = DEFAULT_FEEDS, limit: int = 40) -> list[str]:
    """Pull recent titles from public RSS feeds. Failures are skipped."""
    titles: list[str] = []
    for url in feeds:
        try:
            resp = requests.get(url, timeout=20, headers={"User-Agent": "btcsim/0.1"})
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception:  # noqa: BLE001
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if title:
                titles.append(title)
            if len(titles) >= limit:
                return titles
    return titles


def refresh(path: Path | None = None, headlines: list[str] | None = None) -> dict:
    """Read news (or use ``headlines``) and persist watchlist changes."""
    path = path or (state_dir() / "watchlist.json")
    data = load(path)
    used = headlines if headlines is not None else fetch_headlines()
    summary = apply_headlines(data, used)
    data["updated_at"] = _now()
    data["headlines_read"] = len(used)
    save(data, path)
    summary["watchlist"] = data
    return summary


def _actions(previous: dict[str, float], target: dict[str, float], capital: float) -> list[dict]:
    names = sorted(set(previous) | set(target))
    actions = []
    for name in names:
        before = float(previous.get(name, 0.0))
        after = float(target.get(name, 0.0))
        delta = after - before
        if after == 0 and before == 0:
            continue
        if abs(delta) < 0.02 and after > 0 and before > 0:
            side = "HOLD"
        elif delta > 0:
            side = "BUY"
        elif delta < 0:
            side = "SELL"
        else:
            side = "HOLD"
        actions.append({
            "asset": name,
            "action": side,
            "weight_before_pct": round(before * 100, 2),
            "weight_after_pct": round(after * 100, 2),
            "amount": round(after * capital, 2),
        })
    return actions


def decide(
    path: Path | None = None,
    capital: float = 10_000.0,
    currency: str = "eur",
    method: str = "min_variance",
    prices=None,
) -> dict:
    """Allocate virtual capital across the watchlist and store the decision.

    Assets flagged ``caution`` by a bearish headline are left at weight 0
    (a virtual sell) until a later bullish headline clears the flag.
    """
    path = path or (state_dir() / "watchlist.json")
    data = load(path)
    eligible = [a["spec"] for a in data["assets"] if not a.get("caution")]
    cautious = [a["spec"] for a in data["assets"] if a.get("caution")]

    weights: dict[str, float] = {}
    note = "Alocacao defensiva (minima variancia) sobre a watchlist."
    if not eligible:
        note = "Todas as posicoes estao em cautela. Decisao: ficar em cash virtual."
    else:
        from .allocation import _spec_name

        if prices is not None:
            wanted = [_spec_name(spec) for spec in eligible]
            frame = prices[[c for c in wanted if c in prices.columns]]
        elif len(eligible) == 1:
            weights = {_spec_name(eligible[0]): 1.0}
            frame = None
        else:
            frame = alloc_mod.load_prices(eligible, currency=currency, days=365)
        if frame is not None:
            names = list(frame.columns)
            if len(names) == 1:
                weights = {names[0]: 1.0}
            elif len(names) >= 2:
                returns = alloc_mod.daily_returns(frame)
                result = alloc_mod.optimize(names, returns, method=method, n_samples=8_000)
                weights = {k: float(v) for k, v in result.weights.items()}
                note = (
                    f"Alocacao {method}: retorno esperado {result.exp_return_pct:+.1f}%, "
                    f"volatilidade {result.exp_volatility_pct:.1f}%."
                )

    decision_path = path.parent / "last_decision.json"
    previous = {}
    if decision_path.exists():
        previous = json.loads(decision_path.read_text(encoding="utf-8")).get("weights", {})

    decision = {
        "at": _now(),
        "virtual_capital": capital,
        "currency": currency.upper(),
        "method": method,
        "note": note,
        "disclaimer": "Decisao virtual. Nao e uma ordem nem aconselhamento financeiro.",
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "cautious": cautious,
        "actions": _actions(previous, weights, capital),
    }
    decision_path.write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    with (path.parent / "decision_log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(decision, ensure_ascii=False) + "\n")
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
    parser.add_argument("--every-hours", type=float, default=6.0)
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
