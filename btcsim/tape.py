"""Intraday Bitcoin sleeve: buy the dip, sell only in profit.

Virtual money only. Buys slices when the spot price touches a ladder at or
below 80,000 USD. Sells a slice only when the round trip is net positive after
fees. Underwater slices are held. This sleeve is separate from the hourly
portfolio so that rebalance cannot sell these units at a loss.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .watchlist import _equity, _load_book, _save_book, book_lock, state_dir

FEE_RATE = 0.001
MIN_NET = 0.008
BUDGET_EUR = 1500.0
SLICE_EUR = 250.0
LEVELS_USD = (80_000.0, 78_000.0, 76_000.0, 74_000.0)
SPOT_URL = "https://api.coingecko.com/api/v3/simple/price"


def sell_trigger_usd(entry_usd: float, fee_rate: float = FEE_RATE, min_net: float = MIN_NET) -> float:
    """USD price that clears fees and leaves ``min_net`` profit."""
    return float(entry_usd) * (1 + fee_rate) / (1 - fee_rate) * (1 + min_net)


def _now_iso(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def fresh_state() -> dict:
    return {
        "budget_eur": BUDGET_EUR,
        "slice_eur": SLICE_EUR,
        "levels_usd": list(LEVELS_USD),
        "fee_rate": FEE_RATE,
        "min_net": MIN_NET,
        "cash_eur": 0.0,
        "funded": False,
        "lots": [],
        "next_id": 1,
        "realized_pnl_eur": 0.0,
        "trades": [],
        "last_spot": None,
    }


def load_tape(directory: Path) -> dict:
    path = directory / "tape.json"
    if not path.exists():
        return fresh_state()
    data = json.loads(path.read_text(encoding="utf-8"))
    base = fresh_state()
    base.update(data)
    base["lots"] = list(data.get("lots") or [])
    base["trades"] = list(data.get("trades") or [])
    return base


def save_tape(directory: Path, tape: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "tape.json").write_text(
        json.dumps(tape, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def equity(tape: dict, spot_eur: float | None = None) -> float:
    total = float(tape.get("cash_eur") or 0.0)
    px = spot_eur
    if px is None and tape.get("last_spot"):
        px = tape["last_spot"].get("eur")
    for lot in tape.get("lots") or []:
        price = float(px or lot["price_eur"])
        total += float(lot["units"]) * price
    return total


def mark_to_market(directory: Path) -> float:
    path = directory / "tape.json"
    if not path.exists():
        return 0.0
    return equity(json.loads(path.read_text(encoding="utf-8")))


def fetch_spot() -> dict:
    response = requests.get(
        SPOT_URL,
        params={"ids": "bitcoin", "vs_currencies": "usd,eur"},
        timeout=20,
        headers={"User-Agent": "btcsim/0.1"},
    )
    response.raise_for_status()
    row = response.json()["bitcoin"]
    return {"usd": float(row["usd"]), "eur": float(row["eur"])}


def fund_from_book(book: dict, tape: dict, fee_rate: float = FEE_RATE) -> float:
    """Move the sleeve budget out of the strategic book. Never sells bitcoin."""
    if tape.get("funded"):
        return 0.0
    need = float(tape["budget_eur"]) - float(tape.get("cash_eur") or 0.0)
    if need <= 0.01:
        tape["funded"] = True
        return 0.0

    prices = book.get("last_prices") or {}
    units = {k: float(v) for k, v in (book.get("units") or {}).items()}
    cash = float(book.get("cash") or 0.0)
    if cash < need:
        shortfall = need - cash
        ranked = []
        for asset, qty in units.items():
            if asset == "bitcoin" or qty <= 0:
                continue
            px = prices.get(asset)
            if not px:
                continue
            ranked.append((qty * float(px), asset, float(px)))
        ranked.sort(reverse=True)
        for value, asset, px in ranked:
            if shortfall <= 0.01:
                break
            gross = min(value, shortfall / (1 - fee_rate))
            units[asset] = units[asset] - gross / px
            net = gross * (1 - fee_rate)
            cash += net
            shortfall -= net
        book["units"] = {k: v for k, v in units.items() if v > 1e-10}
        book["cash"] = cash

    moved = min(cash, need)
    if moved <= 0.01:
        return 0.0
    book["cash"] = cash - moved
    tape["cash_eur"] = float(tape.get("cash_eur") or 0.0) + moved
    tape["funded"] = tape["cash_eur"] >= float(tape["budget_eur"]) - 0.05
    return moved


def step(tape: dict, spot: dict, now: datetime | None = None) -> list[dict]:
    """Apply one price to the sleeve. Returns fills. Mutates ``tape``."""
    usd = float(spot["usd"])
    eur = float(spot["eur"])
    if usd <= 0 or eur <= 0:
        return []
    stamp = _now_iso(now)
    tape["last_spot"] = {"usd": usd, "eur": eur, "at": stamp}
    fee = float(tape.get("fee_rate", FEE_RATE))
    min_net = float(tape.get("min_net", MIN_NET))
    fills: list[dict] = []

    kept = []
    for lot in tape.get("lots") or []:
        trigger = sell_trigger_usd(lot["price_usd"], fee, min_net)
        proceeds = float(lot["units"]) * eur * (1 - fee)
        pnl = proceeds - float(lot["spent_eur"])
        if usd >= trigger and pnl > 0:
            tape["cash_eur"] = float(tape["cash_eur"]) + proceeds
            tape["realized_pnl_eur"] = float(tape.get("realized_pnl_eur") or 0.0) + pnl
            fill = {
                "at": stamp,
                "side": "SELL",
                "level_usd": lot["level_usd"],
                "price_usd": usd,
                "price_eur": eur,
                "units": lot["units"],
                "proceeds_eur": round(proceeds, 2),
                "pnl_eur": round(pnl, 2),
            }
            fills.append(fill)
            tape["trades"].append(fill)
        else:
            kept.append(lot)
    tape["lots"] = kept

    open_levels = {float(lot["level_usd"]) for lot in tape["lots"]}
    open_spent = sum(float(lot["spent_eur"]) for lot in tape["lots"])
    for level in sorted((float(x) for x in tape.get("levels_usd") or LEVELS_USD), reverse=True):
        if usd > level or level in open_levels:
            continue
        room = float(tape["budget_eur"]) - open_spent
        spend = min(float(tape.get("slice_eur", SLICE_EUR)), float(tape["cash_eur"]), room)
        if spend < 1:
            continue
        notional = spend / (1 + fee)
        units = notional / eur
        tape["cash_eur"] = float(tape["cash_eur"]) - spend
        open_spent += spend
        lot = {
            "id": int(tape.get("next_id") or 1),
            "level_usd": level,
            "units": units,
            "price_usd": usd,
            "price_eur": eur,
            "spent_eur": round(spend, 2),
            "bought_at": stamp,
        }
        tape["next_id"] = lot["id"] + 1
        tape["lots"].append(lot)
        open_levels.add(level)
        fill = {
            "at": stamp,
            "side": "BUY",
            "level_usd": level,
            "price_usd": usd,
            "price_eur": eur,
            "units": units,
            "spent_eur": round(spend, 2),
            "pnl_eur": 0.0,
        }
        fills.append(fill)
        tape["trades"].append(fill)

    tape["trades"] = tape["trades"][-100:]
    return fills


def view(tape: dict, now: datetime | None = None) -> dict:
    day = _now_iso(now)[:10]
    today = [t for t in tape.get("trades") or [] if str(t.get("at", "")).startswith(day)]
    spot = tape.get("last_spot") or {}
    return {
        "rule": (
            "Compro fatias de 250 EUR quando o Bitcoin chega a 80 000, 78 000, "
            "76 000 ou 74 000 USD, até 1 500 EUR no total. Vendo cada fatia só "
            "quando a venda fica em lucro depois das taxas. Se o preço continuar "
            "a cair, mantenho o lote."
        ),
        "budget_eur": tape.get("budget_eur", BUDGET_EUR),
        "cash_eur": round(float(tape.get("cash_eur") or 0.0), 2),
        "funded": bool(tape.get("funded")),
        "spot_usd": spot.get("usd"),
        "spot_eur": spot.get("eur"),
        "spot_at": spot.get("at"),
        "open_lots": [
            {
                "level_usd": lot["level_usd"],
                "price_usd": lot["price_usd"],
                "spent_eur": lot["spent_eur"],
                "sell_from_usd": round(sell_trigger_usd(lot["price_usd"], float(tape.get("fee_rate", FEE_RATE)), float(tape.get("min_net", MIN_NET))), 2),
            }
            for lot in tape.get("lots") or []
        ],
        "realized_pnl_eur": round(float(tape.get("realized_pnl_eur") or 0.0), 2),
        "trades_today": today,
        "equity_eur": round(equity(tape), 2),
    }


def _capitals(directory: Path, tape: dict) -> dict:
    book_path = directory / "book.json"
    initial = 10_000.0
    book_equity = 0.0
    if book_path.exists():
        book = json.loads(book_path.read_text(encoding="utf-8"))
        initial = float(book.get("initial", initial))
        book_equity = _equity(book, book.get("last_prices") or {})
    atual = round(book_equity + equity(tape), 2)
    exp = None
    decision_path = directory / "last_decision.json"
    if decision_path.exists():
        exp = json.loads(decision_path.read_text(encoding="utf-8")).get("previsao_retorno_pct")
    previsao = round(atual * (1 + (exp or 0) / 100), 2)
    return {
        "capital_inicial": round(initial, 2),
        "capital_atual": atual,
        "previsao": previsao,
        "previsao_horizonte": "12 meses",
    }


def _notify(fills: list[dict], capitals: dict) -> None:
    if not fills:
        return
    from .notify import send_tape

    for fill in fills:
        if fill["side"] == "BUY":
            advice = (
                f"Comprei {fill['spent_eur']:.0f} EUR de Bitcoin a "
                f"{fill['price_usd']:.0f} USD (patamar {fill['level_usd']:.0f}). "
                "Só vendo esta fatia quando a venda já deixa lucro depois das taxas."
            )
        else:
            advice = (
                f"Vendi a fatia de {fill['level_usd']:.0f} USD a {fill['price_usd']:.0f} USD. "
                f"Lucro desta operação: {fill['pnl_eur']:.2f} EUR."
            )
        try:
            send_tape({**capitals, "advice": advice, "side": fill["side"]})
        except Exception as exc:  # noqa: BLE001
            print(f"push falhou: {exc}")


def cycle(directory: Path | None = None, spot: dict | None = None, now: datetime | None = None) -> dict:
    directory = directory or state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    quote = spot if spot is not None else fetch_spot()
    with book_lock(directory):
        tape = load_tape(directory)
        book = _load_book(directory / "watchlist.json", 10_000.0)
        moved = fund_from_book(book, tape)
        if moved:
            _save_book(directory / "watchlist.json", book)
        fills = step(tape, quote, now=now)
        save_tape(directory, tape)
        capitals = _capitals(directory, tape)
    _notify(fills, capitals)
    return {"moved_eur": round(moved, 2), "fills": fills, "tape": view(tape, now=now), **capitals}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="btcsim.tape",
        description="Compra Bitcoin nos 80 mil e vende só em lucro. Dinheiro virtual.",
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--every-minutes", type=float, default=15.0)
    args = parser.parse_args(argv)

    def once() -> None:
        print(json.dumps(cycle(), indent=2, ensure_ascii=False))

    if args.loop:
        print(f"Bitcoin intradiário a cada {args.every_minutes} min (dinheiro virtual).")
        while True:
            try:
                once()
            except Exception as exc:  # noqa: BLE001
                print(f"ciclo falhou: {exc}")
            time.sleep(max(0.1, args.every_minutes) * 60)
    else:
        once()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
