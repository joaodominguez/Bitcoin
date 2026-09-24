"""Intraday Bitcoin sleeve: buy meaningful dips, sell only in profit.

Virtual money only. Levels are computed from the recent 30-day high, not a
fixed 80k print. When the market looks jumpy we wait for deeper dips and keep
smaller slices. Underwater lots are held. Separate from the hourly book so
rebalance cannot sell these units at a loss.
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
MIN_NET = 0.01  # slightly wider — noise in a volatile move is not a scalp
BUDGET_EUR = 1200.0  # keep dry powder; something may move BTC hard
SLICE_EUR = 200.0
# Pullbacks from the 30-day high. Alert stance: first buy only after ~8%.
DIP_FRACS = (0.08, 0.11, 0.14, 0.18)
SPOT_URL = "https://api.coingecko.com/api/v3/simple/price"
CHART_URL = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"


def sell_trigger_usd(entry_usd: float, fee_rate: float = FEE_RATE, min_net: float = MIN_NET) -> float:
    """USD price that clears fees and leaves ``min_net`` profit."""
    return float(entry_usd) * (1 + fee_rate) / (1 - fee_rate) * (1 + min_net)


def levels_from_high(high_usd: float, fracs: tuple[float, ...] = DIP_FRACS) -> list[float]:
    """Buy rungs as round USD levels below a recent high."""
    high = float(high_usd)
    if high <= 0:
        return []
    levels = []
    for frac in fracs:
        level = round(high * (1 - frac) / 100) * 100  # nearest 100 USD
        if level > 0 and (not levels or level < levels[-1] - 50):
            levels.append(float(level))
    return levels


def _now_iso(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def fresh_state() -> dict:
    return {
        "budget_eur": BUDGET_EUR,
        "slice_eur": SLICE_EUR,
        "levels_usd": [],
        "anchor_high_usd": None,
        "fee_rate": FEE_RATE,
        "min_net": MIN_NET,
        "cash_eur": 0.0,
        "funded": False,
        "lots": [],
        "next_id": 1,
        "realized_pnl_eur": 0.0,
        "trades": [],
        "last_spot": None,
        "stance": "alert",
        "note": (
            "Compro só em recuos face ao máximo de 30 dias (−8/−11/−14/−18%), "
            "fatias de 200 EUR até 1 200 EUR. Vendo só em lucro. Se continuar a "
            "cair, mantenho."
        ),
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
    # Migrate old fixed-80k tape to the adaptive rule without forcing buys.
    if data.get("levels_usd") == [80000.0, 78000.0, 76000.0, 74000.0] and not data.get("lots"):
        base["levels_usd"] = []
        base["budget_eur"] = BUDGET_EUR
        base["slice_eur"] = SLICE_EUR
        base["min_net"] = MIN_NET
        base["note"] = fresh_state()["note"]
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


def fetch_30d_high() -> float | None:
    try:
        response = requests.get(
            CHART_URL,
            params={"vs_currency": "usd", "days": "30"},
            timeout=25,
            headers={"User-Agent": "btcsim/0.1"},
        )
        response.raise_for_status()
        prices = response.json().get("prices") or []
        if not prices:
            return None
        return float(max(point[1] for point in prices))
    except Exception:  # noqa: BLE001
        return None


def refresh_levels(tape: dict, high_usd: float | None, spot_usd: float) -> list[float]:
    """Update buy rungs from the recent high. Never raise open lots."""
    anchor = float(high_usd or tape.get("anchor_high_usd") or spot_usd)
    # Keep the higher watermark so a bounce does not cancel deeper rungs mid-dip.
    prev = float(tape.get("anchor_high_usd") or 0)
    if prev > anchor:
        anchor = prev
    if spot_usd > anchor:
        anchor = spot_usd
    tape["anchor_high_usd"] = round(anchor, 2)
    levels = levels_from_high(anchor)
    tape["levels_usd"] = levels
    tape["stance"] = "alert"
    return levels


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
    if not tape.get("levels_usd"):
        refresh_levels(tape, tape.get("anchor_high_usd"), usd)
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
    for level in sorted((float(x) for x in tape.get("levels_usd") or []), reverse=True):
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
    levels = [float(x) for x in tape.get("levels_usd") or []]
    first = levels[0] if levels else None
    spot_usd = spot.get("usd")
    waiting = first is None or (spot_usd is not None and spot_usd > first)
    return {
        "rule": tape.get("note") or fresh_state()["note"],
        "stance": tape.get("stance", "alert"),
        "anchor_high_usd": tape.get("anchor_high_usd"),
        "levels_usd": levels,
        "budget_eur": tape.get("budget_eur", BUDGET_EUR),
        "cash_eur": round(float(tape.get("cash_eur") or 0.0), 2),
        "funded": bool(tape.get("funded")),
        "spot_usd": spot_usd,
        "spot_eur": spot.get("eur"),
        "spot_at": spot.get("at"),
        "waiting": waiting,
        "next_buy_usd": first,
        "open_lots": [
            {
                "level_usd": lot["level_usd"],
                "price_usd": lot["price_usd"],
                "spent_eur": lot["spent_eur"],
                "sell_from_usd": round(
                    sell_trigger_usd(
                        lot["price_usd"],
                        float(tape.get("fee_rate", FEE_RATE)),
                        float(tape.get("min_net", MIN_NET)),
                    ),
                    2,
                ),
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


def cycle(
    directory: Path | None = None,
    spot: dict | None = None,
    now: datetime | None = None,
    high_usd: float | None = None,
) -> dict:
    directory = directory or state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    quote = spot if spot is not None else fetch_spot()
    high = high_usd if high_usd is not None else fetch_30d_high()
    with book_lock(directory):
        tape = load_tape(directory)
        refresh_levels(tape, high, float(quote["usd"]))
        book = _load_book(directory / "watchlist.json", 10_000.0)
        moved = fund_from_book(book, tape)
        if moved:
            _save_book(directory / "watchlist.json", book)
        fills = step(tape, quote, now=now)
        save_tape(directory, tape)
        capitals = _capitals(directory, tape)
        try:
            from .history import record_equity

            record_equity(directory, capitals["capital_atual"], source="tape")
        except Exception as exc:  # noqa: BLE001
            print(f"histórico falhou: {exc}")
    _notify(fills, capitals)
    return {"moved_eur": round(moved, 2), "fills": fills, "tape": view(tape, now=now), **capitals}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="btcsim.tape",
        description="Compra Bitcoin em recuos face ao máximo recente e vende só em lucro.",
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--every-minutes", type=float, default=15.0)
    args = parser.parse_args(argv)

    def once() -> None:
        print(json.dumps(cycle(), indent=2, ensure_ascii=False))

    if args.loop:
        print(f"Bitcoin adaptativo a cada {args.every_minutes} min (dinheiro virtual).")
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
