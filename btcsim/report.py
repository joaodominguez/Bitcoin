"""Human-readable reporting: text summaries and matplotlib charts."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

import pandas as pd

from .simulator import SimulationResult

DISCLAIMER = (
    "Simulacao educativa com dinheiro virtual. NAO e aconselhamento financeiro. "
    "O desempenho passado nao garante resultados futuros."
)


def _fmt_money(x: float, currency: str) -> str:
    sym = {"eur": "EUR", "usd": "USD"}.get(currency.lower(), currency.upper())
    return f"{x:,.2f} {sym}"


def text_report(result: SimulationResult, currency: str = "eur") -> str:
    m = result.metrics
    lines = []
    lines.append("=" * 60)
    lines.append(f"Estrategia: {result.strategy_name}")
    lines.append("=" * 60)
    lines.append(
        f"Periodo:        {result.price.index[0].date()} -> {result.price.index[-1].date()} "
        f"({len(result.price)} dias)"
    )
    lines.append(f"Capital inicial: {_fmt_money(m.start_value, currency)}")
    lines.append(f"Valor final:     {_fmt_money(m.end_value, currency)}")
    lines.append(f"Retorno total:   {m.total_return_pct:+.2f}%")
    lines.append(f"Retorno anualiz: {m.annualized_return_pct:+.2f}%")
    lines.append(f"Max drawdown:    {m.max_drawdown_pct:.2f}%")
    lines.append(f"Volatilidade:    {m.volatility_pct:.2f}% (anual)")
    lines.append(f"Sharpe:          {m.sharpe:.2f}")
    lines.append(f"Nr. de trades:   {m.n_trades}")
    lines.append(f"Comissoes pagas: {_fmt_money(m.total_fees, currency)}")

    pf = result.portfolio
    final_price = float(result.price.iloc[-1])
    lines.append("-" * 60)
    lines.append(f"Posicao final:   {pf.units:.6f} BTC + {_fmt_money(pf.cash, currency)} cash")
    lines.append(f"(BTC @ {_fmt_money(final_price, currency)})")

    trades = result.trades
    if not trades.empty:
        lines.append("-" * 60)
        lines.append("Ultimos trades:")
        for _, t in trades.tail(5).iterrows():
            lines.append(
                f"  {pd.Timestamp(t['date']).date()}  {t['side']:<4}  "
                f"{t['units']:.6f} BTC @ {_fmt_money(t['price'], currency)}  "
                f"[{t['reason']}]"
            )
    lines.append("=" * 60)
    return "\n".join(lines)


def comparison_table(
    results: Mapping[str, SimulationResult], currency: str = "eur"
) -> str:
    rows = []
    for name, r in results.items():
        m = r.metrics
        rows.append(
            {
                "estrategia": name,
                "valor_final": m.end_value,
                "retorno_%": m.total_return_pct,
                "max_dd_%": m.max_drawdown_pct,
                "sharpe": m.sharpe,
                "trades": m.n_trades,
            }
        )
    frame = pd.DataFrame(rows).sort_values("valor_final", ascending=False)
    return frame.to_string(index=False)


def save_chart(
    results: Mapping[str, SimulationResult],
    path: str | Path,
    currency: str = "eur",
    title: str = "Simulador de carteira Bitcoin",
    asset_label: str = "BTC",
) -> Path:
    """Save an equity-curve comparison chart (+ BTC price) to ``path``."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 8), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )

    for name, r in results.items():
        ax1.plot(r.equity_curve.index, r.equity_curve.values, label=name, linewidth=1.6)
    ax1.set_title(title)
    ax1.set_ylabel(f"Valor da carteira ({currency.upper()})")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(True, alpha=0.3)

    any_result = next(iter(results.values()))
    ax2.plot(
        any_result.price.index,
        any_result.price.values,
        color="orange",
        linewidth=1.2,
        label=f"Preco {asset_label}",
    )
    ax2.set_ylabel(f"{asset_label} ({currency.upper()})")
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper left", fontsize=9)

    fig.text(0.5, 0.005, DISCLAIMER, ha="center", fontsize=7, style="italic", wrap=True)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


def allocation_text_report(
    result, backtest, currency: str = "eur", initial_cash: float = 10_000.0
) -> str:
    """Text summary for a multi-asset allocation (from btcsim.allocation)."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"Alocacao otima ({result.method}) de {_fmt_money(initial_cash, currency)}")
    lines.append("=" * 60)
    lines.append("Repartição sugerida:")
    for coin, w in sorted(result.weights.items(), key=lambda kv: -kv[1]):
        lines.append(
            f"  {coin:<14} {w * 100:5.1f}%   ({_fmt_money(w * initial_cash, currency)})"
        )
    lines.append("-" * 60)
    lines.append(f"Retorno esperado (anual):  {result.exp_return_pct:+.2f}%")
    lines.append(f"Volatilidade esperada:     {result.exp_volatility_pct:.2f}%")
    lines.append(f"Sharpe esperado:           {result.exp_sharpe:.2f}")
    lines.append("-" * 60)
    lines.append("Resultado real no periodo (backtest, com rebalanceamento):")
    lines.append(
        f"  Rebalanceamento a cada {backtest.rebalance_days} dias"
        if backtest.rebalance_days
        else "  Sem rebalanceamento (pesos flutuam)"
    )
    lines.append(f"  Valor final:   {_fmt_money(backtest.end_value, currency)}")
    lines.append(f"  Retorno total: {backtest.total_return_pct:+.2f}%")
    lines.append(f"  Max drawdown:  {backtest.max_drawdown_pct:.2f}%")
    lines.append(f"  Comissoes:     {_fmt_money(backtest.total_fees, currency)}")
    lines.append("=" * 60)
    return "\n".join(lines)


def save_allocation_chart(
    result,
    backtest,
    path: str | Path,
    currency: str = "eur",
    equal_backtest=None,
    title: str = "Alocacao otima da carteira",
) -> Path:
    """Chart with the weight pie, the equity curve and (if available) the frontier."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(13, 7))
    gs = fig.add_gridspec(2, 2, width_ratios=[1, 1.4], height_ratios=[1, 1])
    ax_pie = fig.add_subplot(gs[:, 0])
    ax_eq = fig.add_subplot(gs[0, 1])
    ax_fr = fig.add_subplot(gs[1, 1])

    labels = [c for c, w in result.weights.items() if w > 0.005]
    sizes = [result.weights[c] for c in labels]
    ax_pie.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90,
               textprops={"fontsize": 9})
    ax_pie.set_title(f"{title}\n({result.method})", fontsize=11)

    ax_eq.plot(backtest.equity_curve.index, backtest.equity_curve.values,
               color="#2ecc71", linewidth=1.8, label=f"Alocacao {result.method}")
    if equal_backtest is not None:
        ax_eq.plot(equal_backtest.equity_curve.index, equal_backtest.equity_curve.values,
                   color="#95a5a6", linewidth=1.3, linestyle="--", label="Peso igual")
    ax_eq.set_ylabel(f"Valor ({currency.upper()})")
    ax_eq.set_title("Valor da carteira", fontsize=10)
    ax_eq.legend(loc="upper left", fontsize=8)
    ax_eq.grid(True, alpha=0.3)

    fr = result.frontier
    if fr and fr.get("volatility"):
        sc = ax_fr.scatter(fr["volatility"], fr["returns"], c=fr["sharpe"],
                           cmap="viridis", s=6, alpha=0.5)
        ax_fr.scatter([result.exp_volatility_pct], [result.exp_return_pct],
                      color="red", marker="*", s=220, edgecolor="black",
                      label="Escolhida", zorder=5)
        fig.colorbar(sc, ax=ax_fr, label="Sharpe")
        ax_fr.set_xlabel("Volatilidade (% anual)")
        ax_fr.set_ylabel("Retorno esp. (% anual)")
        ax_fr.set_title("Fronteira eficiente (Monte Carlo)", fontsize=10)
        ax_fr.legend(loc="best", fontsize=8)
        ax_fr.grid(True, alpha=0.3)
    else:
        ax_fr.axis("off")
        ax_fr.text(0.5, 0.5, "Fronteira eficiente indisponivel\n(metodo sem otimizacao)",
                   ha="center", va="center", fontsize=9, color="gray")

    fig.text(0.5, 0.005, DISCLAIMER, ha="center", fontsize=7, style="italic")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path
