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

import pandas as pd
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

        studies.append({
            "asset": name,
            "trend": trend,
            "rsi": round(rsi_now, 1),
            "return_30d_pct": round(ret_30 * 100, 1),
            "drawdown_pct": round(drawdown * 100, 1),
            "stance": stance,
            "reading": reading,
        })
    return studies


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


def _rebalance(book: dict, weights: dict[str, float], prices: dict[str, float], fee_rate: float = 0.001) -> float:
    """Move the virtual book to ``weights`` of its current value. Returns that value."""
    merged = {**book.get("last_prices", {}), **prices}
    equity = _equity(book, merged)
    units = {k: float(v) for k, v in book.get("units", {}).items()}
    cash = float(book.get("cash", 0.0))
    targets = {name: equity * float(weight) for name, weight in weights.items()}

    for asset, qty in list(units.items()):
        px = merged.get(asset)
        if not px:
            continue
        target = targets.get(asset, 0.0)
        current = qty * px
        if current > target + 0.01:
            sell_val = current - target
            cash += sell_val * (1 - fee_rate)
            units[asset] = target / px if target > 0 else 0.0

    for asset, target in targets.items():
        px = merged.get(asset)
        if not px:
            continue
        current = units.get(asset, 0.0) * px
        if target > current + 0.01 and cash > 0:
            buy_val = min(target - current, cash / (1 + fee_rate))
            cash -= buy_val * (1 + fee_rate)
            units[asset] = units.get(asset, 0.0) + buy_val / px

    book["units"] = {k: v for k, v in units.items() if v > 1e-10}
    book["cash"] = cash
    book["last_prices"] = merged
    return equity


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
    note = "Alocacao defensiva (minima variancia) sobre a watchlist, depois de ler os padroes."
    if not eligible:
        note = "Todas as posicoes estao em cautela. Decisao: ficar em cash virtual."
    elif frame is not None:
        wanted = [_spec_name(spec) for spec in eligible]
        book = frame[[c for c in wanted if c in frame.columns]]
        names = list(book.columns)
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

    decision_path = path.parent / "last_decision.json"
    previous = {}
    if decision_path.exists():
        previous = json.loads(decision_path.read_text(encoding="utf-8")).get("weights", {})

    book = _load_book(path, capital)
    spot = _price_map(frame)
    capital_atual = round(_equity(book, {**book.get("last_prices", {}), **spot}), 2)
    if exp_return_pct is None and studies:
        held = [item for item in studies if item["asset"] in weights]
        if held:
            exp_return_pct = round(
                sum(item["return_30d_pct"] for item in held) / len(held) * 12, 2
            )
    previsao = round(capital_atual * (1 + (exp_return_pct or 0) / 100), 2)
    _rebalance(book, weights, spot)
    _save_book(path, book)

    decision = {
        "at": _now(),
        "virtual_capital": capital,
        "capital_inicial": round(float(book["initial"]), 2),
        "capital_atual": capital_atual,
        "previsao": previsao,
        "previsao_retorno_pct": exp_return_pct,
        "previsao_horizonte": "12 meses",
        "currency": currency.upper(),
        "method": method,
        "note": note,
        "disclaimer": "Decisao virtual. Nao e uma ordem nem aconselhamento financeiro.",
        "weights": {k: round(v, 4) for k, v in weights.items()},
        "cautious": cautious,
        "actions": _actions(previous, weights, capital_atual),
        "patterns": studies,
    }
    decision["advice"] = portfolio_advice(
        studies, decision["actions"], cautious
    )
    decision_path.write_text(json.dumps(decision, indent=2, ensure_ascii=False), encoding="utf-8")
    with (path.parent / "decision_log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(decision, ensure_ascii=False) + "\n")
    try:
        from .notify import send_decision

        send_decision(decision)
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
