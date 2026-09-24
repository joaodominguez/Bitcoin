"""Equity curve and simple benchmarks for the virtual book."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .watchlist import _equity


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_equity(directory: Path, capital: float, source: str = "decide") -> None:
    path = directory / "equity_history.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": _now(), "capital": round(float(capital), 2), "source": source}
    if path.exists():
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            last = json.loads(lines[-1])
            if abs(float(last.get("capital", 0)) - row["capital"]) < 0.5:
                return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def load_equity(directory: Path, limit: int = 500) -> list[dict]:
    path = directory / "equity_history.jsonl"
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows[-limit:]


def current_capital(directory: Path) -> float:
    from .tape import mark_to_market

    book_path = directory / "book.json"
    total = 0.0
    if book_path.exists():
        book = json.loads(book_path.read_text(encoding="utf-8"))
        total += _equity(book, book.get("last_prices") or {})
    total += mark_to_market(directory)
    return round(total, 2)


def series_payload(directory: Path) -> dict:
    rows = load_equity(directory)
    if not rows:
        capital = current_capital(directory) or 10_000.0
        rows = [{"at": _now(), "capital": capital, "source": "seed"}]
    initial = 10_000.0
    book_path = directory / "book.json"
    if book_path.exists():
        initial = float(json.loads(book_path.read_text(encoding="utf-8")).get("initial", initial))
    dates = [r["at"] for r in rows]
    curve = [float(r["capital"]) for r in rows]
    return {
        "dates": dates,
        "portfolio": curve,
        "initial": initial,
        "delta": round(curve[-1] - initial, 2) if curve else 0.0,
        "delta_pct": round((curve[-1] / initial - 1) * 100, 2) if curve and initial else 0.0,
        "note": "Curva do capital virtual desde o início do livro.",
    }
