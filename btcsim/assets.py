"""Display names, sector and news phrases for each asset the book can watch.

Icons in the dashboard are original letter marks, not company logos.
"""

from __future__ import annotations

CATALOG: tuple[dict, ...] = (
    {"spec": "bitcoin", "label": "Bitcoin", "mark": "BTC", "area": "crypto", "area_pt": "Cripto", "group": "Cripto", "color": "#f7931a", "needles": ("bitcoin", "btc")},
    {"spec": "ethereum", "label": "Ethereum", "mark": "ETH", "area": "crypto", "area_pt": "Cripto", "group": "Cripto", "color": "#8c8dff", "needles": ("ethereum", "ether", "eth")},
    {"spec": "solana", "label": "Solana", "mark": "SOL", "area": "crypto", "area_pt": "Cripto", "group": "Cripto", "color": "#14f195", "needles": ("solana", "sol")},
    {"spec": "cardano", "label": "Cardano", "mark": "ADA", "area": "crypto", "area_pt": "Cripto", "group": "Cripto", "color": "#3b6cff", "needles": ("cardano", "ada")},
    {"spec": "dogecoin", "label": "Dogecoin", "mark": "DOGE", "area": "crypto", "area_pt": "Cripto", "group": "Cripto", "color": "#c2a633", "needles": ("dogecoin", "doge")},
    {"spec": "ripple", "label": "XRP", "mark": "XRP", "area": "crypto", "area_pt": "Cripto", "group": "Cripto", "color": "#346aa9", "needles": ("ripple", "xrp")},
    {"spec": "binancecoin", "label": "BNB", "mark": "BNB", "area": "crypto", "area_pt": "Cripto", "group": "Cripto", "color": "#f3ba2f", "needles": ("binance coin", "bnb")},
    {"spec": "stock:AAPL", "label": "Apple", "mark": "AAPL", "area": "tech", "area_pt": "Tecnologia", "group": "Ações", "color": "#d1d5db", "needles": ("aapl", "apple")},
    {"spec": "stock:MSFT", "label": "Microsoft", "mark": "MSFT", "area": "tech", "area_pt": "Tecnologia", "group": "Ações", "color": "#60a5fa", "needles": ("msft", "microsoft")},
    {"spec": "stock:GOOGL", "label": "Alphabet", "mark": "GOOG", "area": "tech", "area_pt": "Tecnologia", "group": "Ações", "color": "#4ade80", "needles": ("googl", "google", "alphabet")},
    {"spec": "stock:AMZN", "label": "Amazon", "mark": "AMZN", "area": "consumer", "area_pt": "Consumo", "group": "Ações", "color": "#fb923c", "needles": ("amzn", "amazon")},
    {"spec": "stock:NVDA", "label": "Nvidia", "mark": "NVDA", "area": "tech", "area_pt": "Tecnologia", "group": "Ações", "color": "#86efac", "needles": ("nvda", "nvidia")},
    {"spec": "stock:TSLA", "label": "Tesla", "mark": "TSLA", "area": "auto", "area_pt": "Automóvel", "group": "Ações", "color": "#f43f5e", "needles": ("tsla", "tesla")},
    {"spec": "stock:META", "label": "Meta", "mark": "META", "area": "tech", "area_pt": "Tecnologia", "group": "Ações", "color": "#60a5fa", "needles": ("meta platforms", "facebook")},
    {"spec": "stock:SPY", "label": "S&P 500", "mark": "SPY", "area": "index", "area_pt": "Índice", "group": "Ações", "color": "#94a3b8", "needles": ("s&p 500", "s&p500", "sp500")},
    {"spec": "stock:GLD", "label": "Ouro", "mark": "AU", "area": "metals", "area_pt": "Metais", "group": "Matérias-primas", "color": "#eab308", "needles": ("gold", "ouro", "bullion")},
    {"spec": "stock:USO", "label": "Petróleo", "mark": "OIL", "area": "energy", "area_pt": "Energia", "group": "Matérias-primas", "color": "#64748b", "needles": ("crude oil", "crude", "oil", "petroleum", "petroleo", "petróleo", "wti", "brent")},
)


def ui_catalog() -> dict[str, dict]:
    """Lookup by spec or ticker, for the dashboard."""
    out: dict[str, dict] = {}
    for item in CATALOG:
        payload = {
            "spec": item["spec"],
            "label": item["label"],
            "mark": item["mark"],
            "color": item["color"],
            "area_pt": item["area_pt"],
        }
        out[item["spec"].lower()] = payload
        symbol = item["spec"].split(":", 1)[-1].lower()
        out[symbol] = payload
    return out


# Yahoo Finance quote used for that asset's own news page.
YAHOO_SYMBOL = {
    "bitcoin": "BTC-USD",
    "ethereum": "ETH-USD",
    "solana": "SOL-USD",
    "cardano": "ADA-USD",
    "dogecoin": "DOGE-USD",
    "ripple": "XRP-USD",
    "binancecoin": "BNB-USD",
    "stock:AAPL": "AAPL",
    "stock:MSFT": "MSFT",
    "stock:GOOGL": "GOOGL",
    "stock:AMZN": "AMZN",
    "stock:NVDA": "NVDA",
    "stock:TSLA": "TSLA",
    "stock:META": "META",
    "stock:SPY": "SPY",
    "stock:GLD": "GLD",
    "stock:USO": "USO",
}

# Not positions. Headlines here move crypto, stocks and commodities together.
YAHOO_MACRO = ("CL=F", "GC=F", "^TNX", "DX-Y.NYB")


def groups() -> list[tuple[str, list[dict]]]:
    order = ("Cripto", "Ações", "Matérias-primas")
    buckets: dict[str, list[dict]] = {name: [] for name in order}
    for item in CATALOG:
        buckets.setdefault(item["group"], []).append(item)
    return [(name, buckets[name]) for name in order if buckets.get(name)]
