"""Virtual portfolio: tracks cash, BTC holdings, and executed trades."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

Side = Literal["BUY", "SELL"]


@dataclass
class Trade:
    """A single executed trade."""

    date: pd.Timestamp
    side: Side
    price: float
    units: float          # BTC bought/sold
    cash_amount: float    # gross cash moved (before fees on buy / after price)
    fee: float
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "date": self.date,
            "side": self.side,
            "price": round(self.price, 2),
            "units": round(self.units, 8),
            "cash_amount": round(self.cash_amount, 2),
            "fee": round(self.fee, 2),
            "reason": self.reason,
        }


@dataclass
class Portfolio:
    """Mutable portfolio state used by the simulator.

    Args:
        cash: Starting cash balance.
        fee_rate: Proportional trading fee (e.g. 0.001 == 0.1%).
    """

    cash: float
    fee_rate: float = 0.001
    units: float = 0.0
    trades: list[Trade] = field(default_factory=list)

    def equity(self, price: float) -> float:
        """Total portfolio value (cash + BTC marked to ``price``)."""
        return self.cash + self.units * price

    def buy(
        self,
        date: pd.Timestamp,
        price: float,
        cash_to_spend: float,
        reason: str = "",
    ) -> Trade | None:
        """Spend ``cash_to_spend`` (fees included) buying BTC.

        Returns the executed Trade, or ``None`` if nothing was bought.
        """
        cash_to_spend = min(cash_to_spend, self.cash)
        if cash_to_spend <= 0 or price <= 0:
            return None
        fee = cash_to_spend * self.fee_rate
        net = cash_to_spend - fee
        units = net / price
        if units <= 0:
            return None
        self.cash -= cash_to_spend
        self.units += units
        trade = Trade(
            date=date,
            side="BUY",
            price=price,
            units=units,
            cash_amount=cash_to_spend,
            fee=fee,
            reason=reason,
        )
        self.trades.append(trade)
        return trade

    def sell(
        self,
        date: pd.Timestamp,
        price: float,
        units_to_sell: float,
        reason: str = "",
    ) -> Trade | None:
        """Sell ``units_to_sell`` BTC, crediting cash net of fees."""
        units_to_sell = min(units_to_sell, self.units)
        if units_to_sell <= 0 or price <= 0:
            return None
        gross = units_to_sell * price
        fee = gross * self.fee_rate
        net = gross - fee
        self.units -= units_to_sell
        self.cash += net
        trade = Trade(
            date=date,
            side="SELL",
            price=price,
            units=units_to_sell,
            cash_amount=gross,
            fee=fee,
            reason=reason,
        )
        self.trades.append(trade)
        return trade

    def sell_all(
        self, date: pd.Timestamp, price: float, reason: str = ""
    ) -> Trade | None:
        return self.sell(date, price, self.units, reason)

    def buy_all(
        self, date: pd.Timestamp, price: float, reason: str = ""
    ) -> Trade | None:
        return self.buy(date, price, self.cash, reason)

    def trades_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(
                columns=["date", "side", "price", "units", "cash_amount", "fee", "reason"]
            )
        return pd.DataFrame([t.as_dict() for t in self.trades])
