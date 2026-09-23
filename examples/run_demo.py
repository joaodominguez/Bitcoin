"""Minimal end-to-end demo: manage 10.000 EUR over the last year.

Run with:  python3 examples/run_demo.py
"""

from btcsim import data
from btcsim.report import comparison_table, save_chart, text_report
from btcsim.simulator import Simulator
from btcsim.strategies import BuyAndHold, DCA, MACrossover, RSIStrategy

series = data.fetch(days=365, currency="eur")
sim = Simulator(series, initial_cash=10_000.0, fee_rate=0.001)

results = sim.compare(
    [BuyAndHold(), DCA(every_days=7), MACrossover(20, 50), RSIStrategy(14, 30, 70)]
)

print(comparison_table(results, currency="eur"))
print()
print(text_report(results["dca"], currency="eur"))

save_chart(results, "output/demo.png", currency="eur")
print("\nGrafico: output/demo.png")
