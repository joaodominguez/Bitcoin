"""Append-only ledger of virtual book and sleeve movements.

Survives refreshes under ``state/movements.jsonl``. Past activity is seeded
from ``decision_log.jsonl`` and ``tape.json`` trades on first read.
"""

from __future__ import annotations

import json
from pathlib import Path

SIDE_PT = {"BUY": "Compra", "SELL": "Venda", "HOLD": "Manter"}
ORIGIN_PT = {"carteira": "Carteira", "sleeve_btc": "Sleeve BTC"}

MOVEMENTS_FILE = "movements.jsonl"
BACKFILL_MARKER = ".movements_backfilled"


def movements_path(directory: Path) -> Path:
    return Path(directory) / MOVEMENTS_FILE


def _book_id(at: str, asset: str, side: str) -> str:
    return f"book:{at}:{asset}:{side}"


def _tape_id(at: str, side: str, level: float | None, price_usd: float | None) -> str:
    lvl = f"{float(level):.2f}" if level is not None else "-"
    px = f"{float(price_usd):.2f}" if price_usd is not None else "-"
    return f"tape:{at}:{side}:{lvl}:{px}"


def row_from_book_action(at: str, action: dict, currency: str = "EUR") -> dict:
    side = str(action.get("action") or "HOLD").upper()
    asset = str(action.get("asset") or "")
    amount = float(action.get("amount") or 0.0)
    return {
        "id": _book_id(at, asset, side),
        "at": at,
        "side": side,
        "side_pt": SIDE_PT.get(side, side),
        "asset": asset,
        "amount_eur": round(amount, 2),
        "quantity": None,
        "price_eur": None,
        "price_usd": None,
        "origin": "carteira",
        "origin_pt": ORIGIN_PT["carteira"],
        "pnl_eur": None,
        "weight_before_pct": action.get("weight_before_pct"),
        "weight_after_pct": action.get("weight_after_pct"),
        "currency": currency,
    }


def row_from_tape_fill(fill: dict) -> dict:
    side = str(fill.get("side") or "").upper()
    at = str(fill.get("at") or "")
    spent = fill.get("spent_eur")
    proceeds = fill.get("proceeds_eur")
    amount = float(spent if side == "BUY" else (proceeds if proceeds is not None else spent or 0.0))
    level = fill.get("level_usd")
    price_usd = fill.get("price_usd")
    return {
        "id": _tape_id(at, side, level, price_usd),
        "at": at,
        "side": side,
        "side_pt": SIDE_PT.get(side, side),
        "asset": "bitcoin",
        "amount_eur": round(amount, 2),
        "quantity": fill.get("units"),
        "price_eur": fill.get("price_eur"),
        "price_usd": price_usd,
        "origin": "sleeve_btc",
        "origin_pt": ORIGIN_PT["sleeve_btc"],
        "pnl_eur": fill.get("pnl_eur") if side == "SELL" else None,
        "weight_before_pct": None,
        "weight_after_pct": None,
        "currency": "EUR",
        "level_usd": level,
    }


def _existing_ids(directory: Path) -> set[str]:
    path = movements_path(directory)
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = row.get("id")
        if mid:
            ids.add(str(mid))
    return ids


def append_rows(directory: Path, rows: list[dict]) -> int:
    """Append new rows (dedup by id). Returns how many were written."""
    if not rows:
        return 0
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    known = _existing_ids(directory)
    fresh = [r for r in rows if r.get("id") and r["id"] not in known]
    if not fresh:
        return 0
    path = movements_path(directory)
    with path.open("a", encoding="utf-8") as handle:
        for row in fresh:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            known.add(row["id"])
    return len(fresh)


def record_book_decision(directory: Path, decision: dict) -> int:
    at = str(decision.get("at") or "")
    currency = str(decision.get("currency") or "EUR")
    rows = [
        row_from_book_action(at, action, currency=currency)
        for action in decision.get("actions") or []
    ]
    return append_rows(directory, rows)


def record_tape_fills(directory: Path, fills: list[dict]) -> int:
    rows = [row_from_tape_fill(fill) for fill in fills or []]
    return append_rows(directory, rows)


def backfill(directory: Path) -> int:
    """Seed the ledger from decision_log + tape trades. Idempotent via ids."""
    directory = Path(directory)
    rows: list[dict] = []
    log_path = directory / "decision_log.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                decision = json.loads(line)
            except json.JSONDecodeError:
                continue
            at = str(decision.get("at") or "")
            currency = str(decision.get("currency") or "EUR")
            for action in decision.get("actions") or []:
                rows.append(row_from_book_action(at, action, currency=currency))

    tape_path = directory / "tape.json"
    if tape_path.exists():
        try:
            tape = json.loads(tape_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tape = {}
        for fill in tape.get("trades") or []:
            rows.append(row_from_tape_fill(fill))

    written = append_rows(directory, rows)
    marker = directory / BACKFILL_MARKER
    marker.write_text("1\n", encoding="utf-8")
    return written


def ensure_backfill(directory: Path) -> int:
    directory = Path(directory)
    path = movements_path(directory)
    marker = directory / BACKFILL_MARKER
    if path.exists() and path.stat().st_size > 0 and marker.exists():
        return 0
    return backfill(directory)


def load_movements(
    directory: Path,
    *,
    limit: int = 200,
    offset: int = 0,
    origin: str | None = None,
    side: str | None = None,
) -> list[dict]:
    ensure_backfill(directory)
    path = movements_path(directory)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if origin and row.get("origin") != origin:
            continue
        if side and str(row.get("side") or "").upper() != side.upper():
            continue
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("at") or ""), reverse=True)
    limit = max(1, min(int(limit), 1000))
    offset = max(0, int(offset))
    return rows[offset : offset + limit]


def movements_payload(
    directory: Path,
    *,
    limit: int = 200,
    offset: int = 0,
    origin: str | None = None,
    side: str | None = None,
) -> dict:
    ensure_backfill(directory)
    path = movements_path(directory)
    total = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if origin and row.get("origin") != origin:
                continue
            if side and str(row.get("side") or "").upper() != side.upper():
                continue
            total += 1
    items = load_movements(
        directory, limit=limit, offset=offset, origin=origin, side=side
    )
    return {
        "items": items,
        "total": total,
        "limit": max(1, min(int(limit), 1000)),
        "offset": max(0, int(offset)),
        "note": (
            "Histórico virtual de movimentos da carteira (rebalanceamento) "
            "e do sleeve BTC (fills). Dinheiro virtual apenas."
        ),
    }
