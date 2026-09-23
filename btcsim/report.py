"""Human-readable reporting: text summaries and matplotlib charts."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

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
