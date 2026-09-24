"""Equity curve and live benchmarks (SPY / BTC buy-and-hold) for the virtual book."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .watchlist import _equity

_CACHE_HOURS = 6.0


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


def _parse_ts(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _asof_curve(frame: pd.DataFrame, stamps: list[pd.Timestamp], initial: float) -> list[float | None]:
    """Map a daily price frame onto irregular equity timestamps (buy-and-hold index)."""
    if frame is None or frame.empty or not stamps or initial <= 0:
        return [None] * len(stamps)
    series = frame["price"].astype(float).copy()
    series.index = pd.DatetimeIndex(series.index).tz_localize("UTC") if series.index.tz is None else series.index.tz_convert("UTC")
    series = series.sort_index()
    first = float(series.iloc[0])
    if first <= 0:
        return [None] * len(stamps)
    out: list[float | None] = []
    for stamp in stamps:
        # Use last close on or before the snapshot; before history starts → None.
        past = series.loc[:stamp]
        if past.empty:
            out.append(None)
            continue
        out.append(round(initial * float(past.iloc[-1]) / first, 2))
    return out


def _load_price_frame(spec: str, days: int, cache_dir: Path) -> pd.DataFrame | None:
    try:
        from . import data as data_mod

        series = data_mod.fetch_asset(spec, days=days, currency="eur", cache_dir=cache_dir)
        return series.frame
    except Exception:  # noqa: BLE001
        return None


def _benchmark_cache_path(directory: Path) -> Path:
    return directory / "benchmarks_cache.json"


def _read_benchmark_cache(directory: Path) -> dict | None:
    path = _benchmark_cache_path(directory)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    age_h = (time.time() - float(payload.get("fetched_at", 0))) / 3600.0
    if age_h > _CACHE_HOURS:
        return None
    return payload


def _write_benchmark_cache(directory: Path, spy: list, btc: list, dates: list[str]) -> None:
    path = _benchmark_cache_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "fetched_at": time.time(),
                "dates": dates,
                "spy": spy,
                "btc": btc,
            }
        ),
        encoding="utf-8",
    )


def attach_benchmarks(
    directory: Path,
    dates: list[str],
    initial: float,
    *,
    force: bool = False,
) -> dict[str, list]:
    """Buy-and-hold SPY and BTC curves in EUR, indexed to ``initial`` at the first date."""
    if len(dates) < 2:
        return {"spy": [], "btc": []}

    cached = None if force else _read_benchmark_cache(directory)
    if cached and cached.get("dates") == dates:
        return {"spy": cached.get("spy") or [], "btc": cached.get("btc") or []}

    stamps = [_parse_ts(d) for d in dates]
    span_days = max(30, int((stamps[-1] - stamps[0]).total_seconds() / 86400) + 14)
    cache_dir = directory / ".cache"
    spy_frame = _load_price_frame("stock:SPY", span_days, cache_dir)
    btc_frame = _load_price_frame("bitcoin", min(span_days, 365), cache_dir)
    spy = _asof_curve(spy_frame, stamps, initial) if spy_frame is not None else []
    btc = _asof_curve(btc_frame, stamps, initial) if btc_frame is not None else []
    if spy or btc:
        _write_benchmark_cache(directory, spy, btc, dates)
    return {"spy": spy, "btc": btc}


def what_changed(directory: Path) -> dict:
    """Compact delta since yesterday's last equity snapshot (and last decision)."""
    rows = load_equity(directory, limit=200)
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    yesterday_rows = [
        r for r in rows if str(r.get("at", ""))[:10] < today
    ]
    last_y = yesterday_rows[-1] if yesterday_rows else (rows[0] if rows else None)
    current = current_capital(directory)
    if last_y is None:
        return {
            "since": None,
            "capital_then": None,
            "capital_now": current,
            "delta_eur": None,
            "delta_pct": None,
            "summary": "Ainda sem histórico de ontem — a curva começa agora.",
        }
    then = float(last_y["capital"])
    delta = round(current - then, 2)
    pct = round((current / then - 1) * 100, 2) if then else None
    decision_path = directory / "last_decision.json"
    advice = None
    if decision_path.exists():
        try:
            advice = json.loads(decision_path.read_text(encoding="utf-8")).get("advice")
        except Exception:  # noqa: BLE001
            advice = None
    sign = "+" if delta >= 0 else ""
    summary = f"Desde ontem: {sign}{delta:.2f} EUR ({sign}{pct}%)."
    if advice:
        summary += f" Última leitura: {advice}"
    return {
        "since": last_y.get("at"),
        "capital_then": then,
        "capital_now": current,
        "delta_eur": delta,
        "delta_pct": pct,
        "summary": summary,
    }


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
    benches = attach_benchmarks(directory, dates, initial) if len(dates) >= 2 else {"spy": [], "btc": []}
    changed = what_changed(directory)
    return {
        "dates": dates,
        "portfolio": curve,
        "spy": benches.get("spy") or [],
        "btc": benches.get("btc") or [],
        "initial": initial,
        "delta": round(curve[-1] - initial, 2) if curve else 0.0,
        "delta_pct": round((curve[-1] / initial - 1) * 100, 2) if curve and initial else 0.0,
        "changed": changed,
        "note": (
            "Curva do capital virtual vs buy-and-hold SPY e Bitcoin "
            "(mesma base, EUR). Sleeve BTC compra só em dips profundos."
        ),
    }
