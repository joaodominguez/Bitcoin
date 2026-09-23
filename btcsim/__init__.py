"""btcsim - A Bitcoin portfolio management simulator.

Educational tool that backtests buy/sell strategies on real historical
Bitcoin price data. NOT financial advice.
"""

__version__ = "0.1.0"

from .portfolio import Portfolio, Trade
from .simulator import Simulator, SimulationResult

__all__ = [
    "Portfolio",
    "Trade",
    "Simulator",
    "SimulationResult",
    "__version__",
]
