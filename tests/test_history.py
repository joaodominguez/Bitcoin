"""Tests for equity history, benchmarks helpers, and sleeve backtest."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from btcsim import history as H
from btcsim import tape as T


def test_record_equity_skips_tiny_moves(tmp_path: Path):
    H.record_equity(tmp_path, 10_000.0, source="a")
    H.record_equity(tmp_path, 10_000.2, source="b")  # < 0.5 → skip
    H.record_equity(tmp_path, 10_050.0, source="c")
    rows = H.load_equity(tmp_path)
    assert len(rows) == 2
    assert rows[-1]["capital"] == 10_050.0


def test_asof_curve_indexes_to_initial():
    idx = pd.date_range("2026-01-01", periods=5, freq="D")
    frame = pd.DataFrame({"price": [100.0, 110.0, 105.0, 120.0, 130.0]}, index=idx)
    stamps = [
        pd.Timestamp("2026-01-01", tz="UTC"),
        pd.Timestamp("2026-01-03 15:00", tz="UTC"),
        pd.Timestamp("2026-01-05 12:00", tz="UTC"),
    ]
    curve = H._asof_curve(frame, stamps, 10_000.0)
    assert curve[0] == 10_000.0
    assert curve[1] == 10_500.0  # 105/100
    assert curve[2] == 13_000.0


def test_what_changed_summarises_delta(tmp_path: Path):
    path = tmp_path / "equity_history.jsonl"
    path.write_text(
        '{"at":"2026-09-23T10:00:00+00:00","capital":10000.0,"source":"seed"}\n'
        '{"at":"2026-09-24T08:00:00+00:00","capital":10100.0,"source":"decide"}\n',
        encoding="utf-8",
    )
    book = {"initial": 10_000.0, "cash": 10_100.0, "units": {}, "last_prices": {}}
    (tmp_path / "book.json").write_text(__import__("json").dumps(book), encoding="utf-8")
    changed = H.what_changed(tmp_path)
    assert changed["delta_eur"] == 100.0
    assert "Desde ontem" in changed["summary"]


def test_series_payload_includes_benchmark_slots(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(H, "attach_benchmarks", lambda *a, **k: {"spy": [10000, 10100], "btc": [10000, 9900]})
    path = tmp_path / "equity_history.jsonl"
    path.write_text(
        '{"at":"2026-09-23T10:00:00+00:00","capital":10000.0,"source":"seed"}\n'
        '{"at":"2026-09-24T08:00:00+00:00","capital":10050.0,"source":"decide"}\n',
        encoding="utf-8",
    )
    (tmp_path / "book.json").write_text(
        '{"initial":10000,"cash":10050,"units":{},"last_prices":{}}', encoding="utf-8"
    )
    payload = H.series_payload(tmp_path)
    assert payload["spy"] == [10000, 10100]
    assert payload["btc"] == [10000, 9900]
    assert "changed" in payload


def test_sleeve_backtest_waits_for_deep_dip():
    # Synthetic: flat near high, then −9% dip that should trigger first rung.
    dates = pd.date_range("2026-01-01", periods=45, freq="D")
    usd = [100_000.0] * 40 + [91_000.0] * 5
    eur = [u * 0.9 for u in usd]
    frame = pd.DataFrame({"usd": usd, "eur": eur}, index=dates)
    report = T.backtest_sleeve(frame, budget_eur=1200.0, slice_eur=200.0)
    assert report["buys"] >= 1
    assert report["sleeve_end"] > 0
    assert report["open_lots"] + report["sells"] >= 1
    assert len(report["sleeve_curve"]) == 45


def test_sleeve_backtest_no_buy_on_shallow_dip():
    dates = pd.date_range("2026-01-01", periods=40, freq="D")
    # Only −4% off the high — below the −8% first adaptive rung.
    usd = [100_000.0] * 30 + [96_000.0] * 10
    eur = [u * 0.9 for u in usd]
    frame = pd.DataFrame({"usd": usd, "eur": eur}, index=dates)
    report = T.backtest_sleeve(frame, budget_eur=1200.0)
    assert report["buys"] == 0
    assert report["cash_idle_end"] == 1200.0
