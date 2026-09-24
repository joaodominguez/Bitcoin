"""Tests for the append-only movements ledger and API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from btcsim import movements as M
from btcsim.dashboard import app


def _decision(at: str, actions: list[dict]) -> dict:
    return {
        "at": at,
        "currency": "EUR",
        "actions": actions,
    }


def test_record_book_fills_include_price_and_pnl(tmp_path: Path):
    decision = {
        "at": "2026-09-24T12:00:00+00:00",
        "currency": "EUR",
        "fills": [
            {
                "side": "BUY",
                "asset": "MSFT",
                "units": 2.0,
                "price_eur": 400.0,
                "amount_eur": 800.8,
                "pnl_eur": None,
            },
            {
                "side": "SELL",
                "asset": "NVDA",
                "units": 1.0,
                "price_eur": 120.0,
                "amount_eur": 119.88,
                "pnl_eur": 15.5,
            },
        ],
        "actions": [
            {
                "asset": "MSFT",
                "action": "HOLD",
                "weight_after_pct": 20,
                "amount": 2000,
                "price_eur": 400.0,
            }
        ],
    }
    assert M.record_book_decision(tmp_path, decision) == 3
    rows = M.load_movements(tmp_path, side="BUY,SELL", limit=10)
    assert len(rows) == 2
    sell = next(r for r in rows if r["side"] == "SELL")
    assert sell["price_eur"] == 120.0
    assert sell["pnl_eur"] == 15.5
    buy = next(r for r in rows if r["side"] == "BUY")
    assert buy["price_eur"] == 400.0
    assert buy["pnl_eur"] is None


def test_record_book_and_tape_dedup(tmp_path: Path):
    decision = _decision(
        "2026-09-24T10:00:00+00:00",
        [
            {
                "asset": "AAPL",
                "action": "BUY",
                "weight_before_pct": 0,
                "weight_after_pct": 10,
                "amount": 1000.0,
            },
            {
                "asset": "MSFT",
                "action": "HOLD",
                "weight_before_pct": 20,
                "weight_after_pct": 20,
                "amount": 2000.0,
            },
        ],
    )
    assert M.record_book_decision(tmp_path, decision) == 2
    assert M.record_book_decision(tmp_path, decision) == 0  # dedup

    fills = [
        {
            "at": "2026-09-24T10:15:00+00:00",
            "side": "BUY",
            "level_usd": 80000.0,
            "price_usd": 79500.0,
            "price_eur": 70000.0,
            "units": 0.002,
            "spent_eur": 200.0,
            "pnl_eur": 0.0,
        },
        {
            "at": "2026-09-24T11:00:00+00:00",
            "side": "SELL",
            "level_usd": 80000.0,
            "price_usd": 82000.0,
            "price_eur": 72000.0,
            "units": 0.002,
            "proceeds_eur": 210.0,
            "pnl_eur": 10.0,
        },
    ]
    assert M.record_tape_fills(tmp_path, fills) == 2
    rows = M.load_movements(tmp_path, limit=50)
    assert len(rows) == 4
    assert rows[0]["side"] == "SELL"
    assert rows[0]["origin"] == "sleeve_btc"
    assert rows[0]["pnl_eur"] == 10.0
    book = [r for r in rows if r["origin"] == "carteira"]
    assert {r["side_pt"] for r in book} == {"Compra", "Manter"}
    assert any(r["side"] == "BUY" and r["asset"] == "AAPL" for r in book)


def test_backfill_from_decision_log_and_tape(tmp_path: Path):
    log = tmp_path / "decision_log.jsonl"
    log.write_text(
        json.dumps(
            _decision(
                "2026-09-24T08:00:00+00:00",
                [
                    {
                        "asset": "SPY",
                        "action": "SELL",
                        "weight_before_pct": 50,
                        "weight_after_pct": 0,
                        "amount": 0,
                        "traded_eur": 500.0,
                        "price_eur": 450.0,
                        "pnl_eur": -12.5,
                    }
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "tape.json").write_text(
        json.dumps(
            {
                "trades": [
                    {
                        "at": "2026-09-24T09:00:00+00:00",
                        "side": "BUY",
                        "level_usd": 76000,
                        "price_usd": 75500,
                        "price_eur": 66000,
                        "units": 0.003,
                        "spent_eur": 250,
                        "pnl_eur": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    written = M.backfill(tmp_path)
    assert written == 2
    assert M.backfill(tmp_path) == 0
    payload = M.movements_payload(tmp_path, limit=10)
    assert payload["total"] == 2
    assert payload["items"][0]["asset"] == "bitcoin"
    assert payload["items"][1]["side"] == "SELL"


def test_filter_by_origin(tmp_path: Path):
    M.record_book_decision(
        tmp_path,
        _decision(
            "2026-09-24T08:00:00+00:00",
            [{"asset": "AAPL", "action": "BUY", "weight_before_pct": 0, "weight_after_pct": 5, "amount": 500}],
        ),
    )
    M.record_tape_fills(
        tmp_path,
        [
            {
                "at": "2026-09-24T09:00:00+00:00",
                "side": "BUY",
                "level_usd": 70_000,
                "price_usd": 69_000,
                "price_eur": 60_000,
                "units": 0.001,
                "spent_eur": 100,
                "pnl_eur": 0,
            }
        ],
    )
    only_sleeve = M.load_movements(tmp_path, origin="sleeve_btc")
    assert len(only_sleeve) == 1
    assert only_sleeve[0]["origin"] == "sleeve_btc"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHBOARD_PASSWORD", raising=False)
    monkeypatch.setenv("BTCSIM_STATE", str(tmp_path))
    app.config["TESTING"] = True
    (tmp_path / "decision_log.jsonl").write_text(
        json.dumps(
            _decision(
                "2026-09-24T08:00:00+00:00",
                [
                    {
                        "asset": "AAPL",
                        "action": "BUY",
                        "weight_before_pct": 0,
                        "weight_after_pct": 10,
                        "amount": 1000,
                    }
                ],
            )
        )
        + "\n",
        encoding="utf-8",
    )
    return app.test_client()


def test_api_movements_returns_newest_first(client):
    resp = client.get("/api/movements?limit=50")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1
    assert data["items"][0]["side_pt"] == "Compra"
    assert data["items"][0]["origin_pt"] == "Carteira"


def test_dashboard_has_movements_nav(client):
    html = client.get("/").get_data(as_text=True)
    assert 'data-view="movements"' in html
    assert "Histórico de movimentos" in html
    assert "Movimentos recentes" in html
    assert "/api/movements" in html
