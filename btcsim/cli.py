"""Command-line interface for the Bitcoin portfolio simulator.

Examples:
    python -m btcsim --capital 10000 --days 365 --currency eur
    python -m btcsim --strategy dca --every-days 7
    python -m btcsim --compare --chart output/compare.png
    python -m btcsim --csv my_history.csv --strategy ma_crossover --short 20 --long 50
"""

from __future__ import annotations

import argparse
import sys

from . import allocation as alloc_mod
from . import data as data_mod
from . import report as report_mod
from .news import (
    CryptoPanicProvider,
    FearGreedProvider,
    KeywordSentimentProvider,
    NeutralProvider,
)
from .simulator import Simulator
from .strategies import STRATEGY_REGISTRY, build_strategy


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="btcsim",
        description="Simulador educativo de gestao de carteira de Bitcoin (dinheiro virtual).",
    )
    p.add_argument("--capital", type=float, default=10_000.0, help="Capital inicial (default 10000).")
    p.add_argument(
        "--coin",
        default="bitcoin",
        help="Cripto a simular (id CoinGecko: bitcoin, ethereum, solana, ...).",
    )
    p.add_argument("--currency", default="eur", help="Moeda (eur, usd, ...).")
    p.add_argument("--days", type=int, default=365, help="Dias de historico (max 365 na API gratuita).")
    p.add_argument("--csv", help="Carregar historico de um CSV (colunas date,price) em vez da API.")
    p.add_argument("--fee", type=float, default=0.001, help="Comissao por trade (0.001 = 0.1%%).")
    p.add_argument("--start", help="Data inicial (YYYY-MM-DD) para recortar a serie.")
    p.add_argument("--end", help="Data final (YYYY-MM-DD) para recortar a serie.")

    p.add_argument(
        "--strategy",
        choices=sorted(STRATEGY_REGISTRY),
        default="dca",
        help="Estrategia a simular (default: dca).",
    )
    p.add_argument("--compare", action="store_true", help="Comparar todas as estrategias.")

    # Allocation mode (split capital across several cryptos).
    p.add_argument(
        "--allocate",
        action="store_true",
        help="Repartir o capital por varias criptos da melhor forma (otimizacao de carteira).",
    )
    p.add_argument(
        "--coins",
        default="bitcoin,ethereum,solana",
        help="[allocate] Lista de criptos separada por virgulas.",
    )
    p.add_argument(
        "--method",
        choices=list(alloc_mod.ALLOCATION_METHODS),
        default="max_sharpe",
        help="[allocate] Metodo de alocacao (default: max_sharpe).",
    )
    p.add_argument(
        "--rebalance-days",
        type=int,
        default=30,
        help="[allocate] Cadencia de rebalanceamento em dias (0 = sem rebalanceamento).",
    )
    p.add_argument(
        "--samples",
        type=int,
        default=20_000,
        help="[allocate] Amostras Monte Carlo para max_sharpe/min_variance.",
    )

    # Strategy parameters.
    p.add_argument("--amount", type=float, help="[dca] Valor por compra (default: reparte o capital).")
    p.add_argument("--every-days", type=int, default=7, help="[dca] Cadencia das compras em dias.")
    p.add_argument("--short", type=int, default=20, help="[ma_crossover] SMA curta.")
    p.add_argument("--long", type=int, default=50, help="[ma_crossover] SMA longa.")
    p.add_argument("--rsi-window", type=int, default=14, help="[rsi] Janela do RSI.")
    p.add_argument("--rsi-low", type=float, default=30.0, help="[rsi] Nivel de sobrevenda.")
    p.add_argument("--rsi-high", type=float, default=70.0, help="[rsi] Nivel de sobrecompra.")
    p.add_argument("--sent-threshold", type=float, default=0.2, help="[sentiment] Limiar de sentimento.")
    p.add_argument(
        "--contrarian",
        action="store_true",
        help="[sentiment] Comprar no medo / vender na ganancia (recomendado com feargreed).",
    )

    # News / sentiment source.
    p.add_argument(
        "--news",
        choices=["neutral", "feargreed", "keyword", "cryptopanic"],
        default="neutral",
        help="Fonte de sentimento (feargreed = indice Medo/Ganancia, real e com historico).",
    )
    p.add_argument("--news-csv", help="[keyword] CSV com colunas date,headline.")

    p.add_argument("--chart", help="Guardar grafico PNG neste caminho.")
    p.add_argument("--no-color", action="store_true", help="(reservado)")
    return p


def _make_strategy(name: str, args) -> object:
    if name == "dca":
        return build_strategy("dca", amount=args.amount, every_days=args.every_days)
    if name == "ma_crossover":
        return build_strategy("ma_crossover", short=args.short, long=args.long)
    if name == "rsi":
        return build_strategy(
            "rsi", window=args.rsi_window, low=args.rsi_low, high=args.rsi_high
        )
    if name == "sentiment":
        return build_strategy(
            "sentiment", threshold=args.sent_threshold, contrarian=args.contrarian
        )
    return build_strategy(name)


def _make_sentiment_provider(args):
    if args.news == "feargreed":
        return FearGreedProvider()
    if args.news == "keyword":
        if not args.news_csv:
            raise SystemExit("--news keyword requer --news-csv com colunas date,headline")
        return KeywordSentimentProvider.from_csv(args.news_csv)
    if args.news == "cryptopanic":
        return CryptoPanicProvider(currency=args.currency.upper())
    return NeutralProvider()


def _run_allocation(args) -> int:
    coins = [c.strip() for c in args.coins.split(",") if c.strip()]
    if len(coins) < 2:
        raise SystemExit("--allocate requer pelo menos 2 criptos em --coins")

    print(report_mod.DISCLAIMER)
    print()
    print(f"A carregar dados de: {', '.join(coins)} ...")

    prices = alloc_mod.load_prices(coins, currency=args.currency, days=args.days)
    returns = alloc_mod.daily_returns(prices)

    result = alloc_mod.optimize(
        coins, returns, method=args.method, n_samples=args.samples
    )
    bt = alloc_mod.backtest(
        prices, result.weights, initial_cash=args.capital,
        fee_rate=args.fee, rebalance_days=args.rebalance_days,
    )
    equal = alloc_mod.optimize(coins, returns, method="equal")
    equal_bt = alloc_mod.backtest(
        prices, equal.weights, initial_cash=args.capital,
        fee_rate=args.fee, rebalance_days=args.rebalance_days,
    )

    print()
    print(report_mod.allocation_text_report(
        result, bt, currency=args.currency, initial_cash=args.capital
    ))
    print()
    print(f"(Referencia peso-igual: valor final "
          f"{equal_bt.end_value:,.2f} {args.currency.upper()}, "
          f"retorno {equal_bt.total_return_pct:+.2f}%)")

    if args.chart:
        path = report_mod.save_allocation_chart(
            result, bt, args.chart, currency=args.currency, equal_backtest=equal_bt,
        )
        print(f"\nGrafico guardado em: {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.allocate:
        return _run_allocation(args)

    if args.csv:
        series = data_mod.load_csv(args.csv, coin=args.coin, currency=args.currency)
    else:
        series = data_mod.fetch(days=args.days, currency=args.currency, coin=args.coin)
    if args.start or args.end:
        series = series.slice(args.start, args.end)

    sim = Simulator(
        series,
        initial_cash=args.capital,
        fee_rate=args.fee,
        sentiment_provider=_make_sentiment_provider(args),
    )

    print(report_mod.DISCLAIMER)
    print()

    if args.compare:
        strategies = [_make_strategy(name, args) for name in sorted(STRATEGY_REGISTRY)]
        results = sim.compare(strategies)
        print(report_mod.comparison_table(results, currency=args.currency))
        print()
        for r in results.values():
            print(report_mod.text_report(r, currency=args.currency))
            print()
        if args.chart:
            path = report_mod.save_chart(
                results,
                args.chart,
                currency=args.currency,
                title=f"Simulador de carteira - {args.coin.capitalize()}",
                asset_label=data_mod.ticker_for(args.coin),
            )
            print(f"Grafico guardado em: {path}")
    else:
        strategy = _make_strategy(args.strategy, args)
        result = sim.run(strategy)
        print(report_mod.text_report(result, currency=args.currency))
        if args.chart:
            path = report_mod.save_chart(
                {result.strategy_name: result},
                args.chart,
                currency=args.currency,
                title=f"Simulador de carteira - {args.coin.capitalize()}",
                asset_label=data_mod.ticker_for(args.coin),
            )
            print(f"\nGrafico guardado em: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
