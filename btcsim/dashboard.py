"""Interactive web dashboard for the Bitcoin portfolio simulator.

Run it with:

    python3 -m btcsim.dashboard            # then open http://127.0.0.1:8000
    python3 -m btcsim.dashboard --port 8080 --host 0.0.0.0

The dashboard lets you pick capital, period, currency and strategies, and shows
an interactive equity-curve chart plus a metrics table. Educational only --
NOT financial advice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from . import data as data_mod
from .news import (
    FearGreedProvider,
    KeywordSentimentProvider,
    NeutralProvider,
)
from .report import DISCLAIMER
from .simulator import Simulator
from .strategies import STRATEGY_REGISTRY, build_strategy

COINS = ["bitcoin", "ethereum", "solana", "cardano", "dogecoin", "binancecoin"]

app = Flask(__name__)

DEFAULT_STRATEGIES = ["buy_and_hold", "dca", "ma_crossover", "rsi"]
SAMPLE_NEWS = Path(__file__).resolve().parent.parent / "examples" / "sample_news.csv"


def _make_strategy(name: str, contrarian: bool = False):
    """Build a strategy with sensible dashboard defaults."""
    defaults = {
        "dca": dict(every_days=7),
        "ma_crossover": dict(short=20, long=50),
        "rsi": dict(window=14, low=30.0, high=70.0),
        "sentiment": dict(threshold=0.4, contrarian=contrarian),
    }
    return build_strategy(name, **defaults.get(name, {}))


def _make_provider(news_source: str):
    """Select a sentiment provider from the dashboard 'news' option."""
    if news_source == "feargreed":
        return FearGreedProvider()
    if news_source == "sample" and SAMPLE_NEWS.exists():
        return KeywordSentimentProvider.from_csv(str(SAMPLE_NEWS))
    return NeutralProvider()


def _run(capital: float, currency: str, days: int, fee: float,
         strategy_names: list[str], news_source: str, contrarian: bool,
         coin: str) -> dict:
    series = data_mod.fetch(days=days, currency=currency, coin=coin)

    provider = _make_provider(news_source)

    sim = Simulator(series, initial_cash=capital, fee_rate=fee,
                    sentiment_provider=provider)
    results = sim.compare([_make_strategy(n, contrarian) for n in strategy_names])

    dates = [d.strftime("%Y-%m-%d") for d in series.frame.index]
    curves = {name: [round(float(v), 2) for v in r.equity_curve.values]
              for name, r in results.items()}
    price = [round(float(v), 2) for v in series.frame["price"].values]

    table = []
    for name, r in results.items():
        m = r.metrics
        table.append({
            "strategy": name,
            "end_value": m.end_value,
            "return_pct": m.total_return_pct,
            "annualized_pct": m.annualized_return_pct,
            "max_dd_pct": m.max_drawdown_pct,
            "volatility_pct": m.volatility_pct,
            "sharpe": m.sharpe,
            "trades": m.n_trades,
            "fees": m.total_fees,
        })
    table.sort(key=lambda x: x["end_value"], reverse=True)

    trades = {}
    for name, r in results.items():
        tf = r.trades
        trades[name] = [] if tf.empty else [
            {
                "date": str(t["date"])[:10],
                "side": t["side"],
                "price": t["price"],
                "units": round(float(t["units"]), 6),
                "reason": t["reason"],
            }
            for _, t in tf.tail(30).iterrows()
        ]

    return {
        "dates": dates,
        "curves": curves,
        "price": price,
        "table": table,
        "trades": trades,
        "currency": currency.upper(),
        "capital": capital,
        "coin": coin,
        "asset_label": data_mod.ticker_for(coin),
        "news_source": news_source,
        "period": {"start": dates[0], "end": dates[-1], "days": len(dates)},
        "disclaimer": DISCLAIMER,
    }


@app.route("/")
def index():
    return render_template(
        "dashboard.html",
        strategies=sorted(STRATEGY_REGISTRY),
        default_strategies=DEFAULT_STRATEGIES,
        coins=COINS,
        disclaimer=DISCLAIMER,
    )


@app.route("/api/simulate")
def api_simulate():
    try:
        capital = float(request.args.get("capital", 10_000))
        currency = request.args.get("currency", "eur").lower()
        days = int(request.args.get("days", 365))
        fee = float(request.args.get("fee", 0.001))
        coin = request.args.get("coin", "bitcoin").lower().strip() or "bitcoin"
        news_source = request.args.get("news", "none").lower()
        contrarian = request.args.get("contrarian", "false").lower() == "true"
        names = request.args.getlist("strategy") or DEFAULT_STRATEGIES
        names = [n for n in names if n in STRATEGY_REGISTRY]
        payload = _run(capital, currency, days, fee, names,
                       news_source, contrarian, coin)
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="btcsim.dashboard")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--debug", action="store_true")
    args = p.parse_args(argv)
    print(f"Dashboard em http://{args.host}:{args.port}  (Ctrl+C para parar)")
    app.run(host=args.host, port=args.port, debug=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
