"""Persistent news memory keeps context across cycles."""

from pathlib import Path

from btcsim import news_memory as NM
from btcsim import watchlist as W


def test_ingest_dedupes_and_builds_context(tmp_path: Path):
    items = [
        {"title": "Bitcoin ETF inflows hit a record high", "source": "CoinDesk"},
        {"title": "Bitcoin ETF inflows hit a record high", "source": "CoinDesk"},  # dup
        {"title": "Crude oil plunges as demand drops", "source": "OilPrice"},
        {"title": "Apple rallies on strong iPhone demand", "source": "MarketWatch"},
    ]
    first = NM.ingest(tmp_path, items)
    assert first["ingested"] == 3
    second = NM.ingest(tmp_path, items)
    assert second["ingested"] == 0
    ctx = NM.load_context(tmp_path)
    assert ctx["memory_size"] == 3
    specs = {a["spec"] for a in ctx["assets"]}
    assert "bitcoin" in specs or "stock:GLD" in specs or "stock:USO" in specs
    assert "Contexto" in ctx["summary"] or "memória" in ctx["summary"].lower() or "notícias" in ctx["summary"].lower()


def test_news_bias_tilts_edge_score():
    studies = [
        {"asset": "AAPL", "edge_score": 2.0, "stance": "manter ou reforçar aos poucos"},
        {"asset": "USO", "edge_score": 2.0, "stance": "reduzir"},
    ]
    context = {
        "assets": [
            {"spec": "stock:AAPL", "bias": 0.5},
            {"spec": "stock:USO", "bias": -0.5},
        ]
    }
    out = NM.apply_news_bias_to_studies(studies, context)
    assert out[0]["edge_score"] > 2.0
    assert out[0]["news_bias"] == 0.5
    assert out[1]["edge_score"] < 2.0


def test_refresh_writes_news_memory(tmp_path: Path, monkeypatch):
    path = tmp_path / "watchlist.json"
    W.save({"assets": [{"spec": "bitcoin", "caution": False}]}, path)
    monkeypatch.setenv("BTCSIM_STATE", str(tmp_path))
    summary = W.refresh(
        path,
        headlines=[
            "Federal Reserve rate cut fuels stock rally",
            "Gold price hits a record high",
        ],
    )
    assert summary["news_memory"]["ingested"] >= 1
    assert (tmp_path / "news_memory.jsonl").exists()
    assert (tmp_path / "news_context.json").exists()
