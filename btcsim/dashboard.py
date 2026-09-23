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

from . import allocation as alloc_mod
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
STOCKS = [
    ("stock:AAPL", "Apple (AAPL)"),
    ("stock:MSFT", "Microsoft (MSFT)"),
    ("stock:GOOGL", "Alphabet (GOOGL)"),
    ("stock:AMZN", "Amazon (AMZN)"),
    ("stock:NVDA", "Nvidia (NVDA)"),
    ("stock:TSLA", "Tesla (TSLA)"),
    ("stock:SPY", "S&P 500 ETF (SPY)"),
]

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
    series = data_mod.fetch_asset(coin, days=days, currency=currency)
    currency = series.currency or currency

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
        "asset_label": data_mod.asset_label(coin),
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
        stocks=STOCKS,
        methods=list(alloc_mod.ALLOCATION_METHODS),
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


def _run_allocation(capital, currency, days, fee, coins, method, rebalance_days):
    prices = alloc_mod.load_prices(coins, currency=currency, days=days)
    returns = alloc_mod.daily_returns(prices)
    names = list(prices.columns)

    result = alloc_mod.optimize(names, returns, method=method, n_samples=15_000)
    bt = alloc_mod.backtest(prices, result.weights, initial_cash=capital,
                            fee_rate=fee, rebalance_days=rebalance_days)
    equal = alloc_mod.optimize(names, returns, method="equal")
    equal_bt = alloc_mod.backtest(prices, equal.weights, initial_cash=capital,
                                  fee_rate=fee, rebalance_days=rebalance_days)

    dates = [d.strftime("%Y-%m-%d") for d in bt.equity_curve.index]
    allocation = [
        {
            "coin": c,
            "ticker": data_mod.ticker_for(c),
            "weight_pct": round(w * 100, 2),
            "amount": round(w * capital, 2),
        }
        for c, w in sorted(result.weights.items(), key=lambda kv: -kv[1])
    ]
    frontier = result.frontier or {}
    return {
        "method": method,
        "allocation": allocation,
        "exp_return_pct": result.exp_return_pct,
        "exp_volatility_pct": result.exp_volatility_pct,
        "exp_sharpe": result.exp_sharpe,
        "dates": dates,
        "curve": [round(float(v), 2) for v in bt.equity_curve.values],
        "equal_curve": [round(float(v), 2) for v in equal_bt.equity_curve.values],
        "end_value": bt.end_value,
        "return_pct": bt.total_return_pct,
        "max_dd_pct": bt.max_drawdown_pct,
        "fees": bt.total_fees,
        "equal_end_value": equal_bt.end_value,
        "equal_return_pct": equal_bt.total_return_pct,
        "rebalance_days": rebalance_days,
        "frontier": {
            "volatility": frontier.get("volatility", []),
            "returns": frontier.get("returns", []),
            "sharpe": frontier.get("sharpe", []),
        },
        "currency": currency.upper(),
        "capital": capital,
        "disclaimer": DISCLAIMER,
    }


def _run_walk_forward(capital, currency, days, fee, coins, method,
                      rebalance_days, train_days, test_days):
    prices = alloc_mod.load_prices(coins, currency=currency, days=days)
    wf = alloc_mod.walk_forward(
        prices, method=method, train_days=train_days, test_days=test_days,
        rebalance_days=rebalance_days, initial_cash=capital, fee_rate=fee,
        n_samples=12_000,
    )
    dates = [d.strftime("%Y-%m-%d") for d in wf.oos_equity.index]
    return {
        "method": method,
        "dates": dates,
        "oos_curve": [round(float(v), 2) for v in wf.oos_equity.values],
        "equal_curve": [round(float(v), 2) for v in wf.equal_equity.values],
        "metrics": wf.metrics.as_dict(),
        "equal_metrics": wf.equal_metrics.as_dict(),
        "beat_equal": wf.metrics.end_value >= wf.equal_metrics.end_value,
        "segments": len(wf.segments),
        "train_days": train_days,
        "test_days": test_days,
        "currency": currency.upper(),
        "disclaimer": DISCLAIMER,
    }


@app.route("/api/walkforward")
def api_walkforward():
    try:
        capital = float(request.args.get("capital", 10_000))
        currency = request.args.get("currency", "eur").lower()
        days = int(request.args.get("days", 365))
        fee = float(request.args.get("fee", 0.001))
        rebalance_days = int(request.args.get("rebalance_days", 30))
        train_days = int(request.args.get("train_days", 180))
        test_days = int(request.args.get("test_days", 30))
        method = request.args.get("method", "max_sharpe")
        if method not in alloc_mod.ALLOCATION_METHODS:
            method = "max_sharpe"
        raw = request.args.get("coins", "bitcoin,ethereum,solana")
        coins = [c.strip().lower() for c in raw.split(",") if c.strip()]
        if len(coins) < 2:
            return jsonify({"error": "Escolhe pelo menos 2 ativos."}), 400
        payload = _run_walk_forward(capital, currency, days, fee, coins,
                                    method, rebalance_days, train_days, test_days)
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500


@app.route("/api/allocate")
def api_allocate():
    try:
        capital = float(request.args.get("capital", 10_000))
        currency = request.args.get("currency", "eur").lower()
        days = int(request.args.get("days", 365))
        fee = float(request.args.get("fee", 0.001))
        rebalance_days = int(request.args.get("rebalance_days", 30))
        method = request.args.get("method", "max_sharpe")
        if method not in alloc_mod.ALLOCATION_METHODS:
            method = "max_sharpe"
        raw = request.args.get("coins", "bitcoin,ethereum,solana")
        coins = [c.strip().lower() for c in raw.split(",") if c.strip()]
        if len(coins) < 2:
            return jsonify({"error": "Escolhe pelo menos 2 criptos."}), 400
        payload = _run_allocation(capital, currency, days, fee, coins,
                                  method, rebalance_days)
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
