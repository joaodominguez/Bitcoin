"""Persistent news memory — read once, keep context for future decisions.

Every headline the automation reads is appended to ``state/news_memory.jsonl``
(deduped). A compact ``state/news_context.json`` rolls up per-asset sentiment
and macro themes so later cycles still “remember” what the news said, instead
of reacting only to the latest RSS batch.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .news import score_text

MEMORY_FILE = "news_memory.jsonl"
CONTEXT_FILE = "news_context.json"
MAX_MEMORY_LINES = 4000
MAX_CONTEXT_ASSETS = 40
MAX_THEMES = 12
MAX_RECENT = 40


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _title_id(title: str, source: str = "") -> str:
    blob = f"{source.strip().lower()}|{title.strip().lower()}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def memory_path(directory: Path) -> Path:
    return Path(directory) / MEMORY_FILE


def context_path(directory: Path) -> Path:
    return Path(directory) / CONTEXT_FILE


def _existing_ids(directory: Path) -> set[str]:
    path = memory_path(directory)
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


def ingest(
    directory: Path,
    items: list[dict] | list[str],
    *,
    related_specs_fn=None,
) -> dict:
    """Append new headlines to durable memory and refresh the rolling context.

    ``items`` may be ``{"title", "source"}`` dicts or bare title strings.
    ``related_specs_fn(title) -> list[str]`` maps a headline to asset specs.
    """
    from .watchlist import influenced_specs

    resolve = related_specs_fn or influenced_specs
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    known = _existing_ids(directory)
    fresh: list[dict] = []
    for raw in items or []:
        if isinstance(raw, str):
            title, source = raw.strip(), ""
        else:
            title = str(raw.get("title") or "").strip()
            source = str(raw.get("source") or "").strip()
        if not title:
            continue
        mid = _title_id(title, source)
        if mid in known:
            continue
        score = score_text(title)
        specs = list(resolve(title) or [])
        row = {
            "id": mid,
            "at": _now(),
            "title": title,
            "source": source,
            "score": round(float(score), 3),
            "assets": specs,
        }
        fresh.append(row)
        known.add(mid)

    if fresh:
        path = memory_path(directory)
        with path.open("a", encoding="utf-8") as handle:
            for row in fresh:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        _trim_memory(directory)

    context = rebuild_context(directory)
    return {
        "ingested": len(fresh),
        "memory_size": _memory_size(directory),
        "context": context,
    }


def _memory_size(directory: Path) -> int:
    path = memory_path(directory)
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _trim_memory(directory: Path) -> None:
    path = memory_path(directory)
    if not path.exists():
        return
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if len(lines) <= MAX_MEMORY_LINES:
        return
    path.write_text("\n".join(lines[-MAX_MEMORY_LINES:]) + "\n", encoding="utf-8")


def load_memory(directory: Path, limit: int = 500) -> list[dict]:
    path = memory_path(directory)
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows[-limit:]


def rebuild_context(directory: Path) -> dict:
    """Roll memory into a compact bias map used by decide()."""
    rows = load_memory(directory, limit=800)
    per_asset: dict[str, dict] = defaultdict(lambda: {
        "score_sum": 0.0,
        "count": 0,
        "bull": 0,
        "bear": 0,
        "last_title": "",
        "last_at": "",
        "last_score": 0.0,
    })
    themes: list[dict] = []
    for i, row in enumerate(rows):
        score = float(row.get("score") or 0.0)
        title = str(row.get("title") or "")
        at = str(row.get("at") or "")
        assets = row.get("assets") or []
        if not assets and abs(score) >= 0.2:
            themes.append({"title": title, "score": score, "at": at, "source": row.get("source")})
        # Recency: later rows (higher i) weigh a bit more.
        weight = 1.0 + 0.02 * (i / max(1, len(rows) - 1))
        for spec in assets:
            key = str(spec)
            bucket = per_asset[key]
            bucket["score_sum"] += score * weight
            bucket["count"] += 1
            if score >= 0.2:
                bucket["bull"] += 1
            elif score <= -0.2:
                bucket["bear"] += 1
            bucket["last_title"] = title
            bucket["last_at"] = at
            bucket["last_score"] = score

    assets_out = []
    for spec, bucket in per_asset.items():
        if bucket["count"] <= 0:
            continue
        avg = bucket["score_sum"] / bucket["count"]
        # Soft persistence: lean toward last headline a bit.
        bias = 0.7 * avg + 0.3 * float(bucket["last_score"])
        assets_out.append({
            "spec": spec,
            "bias": round(bias, 3),
            "mentions": bucket["count"],
            "bull": bucket["bull"],
            "bear": bucket["bear"],
            "last_title": bucket["last_title"],
            "last_at": bucket["last_at"],
            "last_score": bucket["last_score"],
        })
    assets_out.sort(key=lambda r: (abs(float(r["bias"])), r["mentions"]), reverse=True)
    assets_out = assets_out[:MAX_CONTEXT_ASSETS]

    # Theme list: most recent polar headlines without a single-asset tag, plus
    # strongest asset-linked ones for the desk.
    themes = themes[-MAX_THEMES:]
    summary_bits = []
    for item in assets_out[:6]:
        lean = "positivo" if item["bias"] > 0.15 else ("negativo" if item["bias"] < -0.15 else "neutro")
        summary_bits.append(f"{item['spec']} {lean} ({item['bias']:+.2f}, {item['mentions']} menções)")
    context = {
        "updated_at": _now(),
        "memory_size": len(rows),
        "assets": assets_out,
        "themes": themes,
        "recent": [
            {
                "at": r.get("at"),
                "title": r.get("title"),
                "source": r.get("source"),
                "score": r.get("score"),
                "assets": r.get("assets") or [],
            }
            for r in rows[-MAX_RECENT:]
        ][::-1],
        "summary": (
            "Contexto de notícias acumulado: " + "; ".join(summary_bits)
            if summary_bits
            else "Ainda a construir memória de notícias."
        ),
    }
    context_path(directory).write_text(
        json.dumps(context, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return context


def load_context(directory: Path) -> dict:
    path = context_path(directory)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    if memory_path(directory).exists():
        return rebuild_context(directory)
    return {
        "updated_at": None,
        "memory_size": 0,
        "assets": [],
        "themes": [],
        "recent": [],
        "summary": "Ainda sem memória de notícias.",
    }


def bias_map(context: dict | None) -> dict[str, float]:
    """spec / bare ticker -> persistent news bias in roughly [-1, 1]."""
    out: dict[str, float] = {}
    for item in (context or {}).get("assets") or []:
        spec = str(item.get("spec") or "")
        bias = float(item.get("bias") or 0.0)
        if not spec:
            continue
        out[spec.lower()] = bias
        bare = spec.split(":", 1)[-1].lower()
        out[bare] = bias
    return out


def apply_news_bias_to_studies(studies: list[dict], context: dict | None) -> list[dict]:
    """Tilt edge_score with durable news memory (does not invent prices)."""
    biases = bias_map(context)
    if not biases:
        return studies
    out = []
    for item in studies:
        copy = dict(item)
        key = str(item.get("asset") or "").lower()
        bare = key.replace("stock:", "")
        bias = biases.get(key, biases.get(bare, 0.0))
        if abs(bias) >= 0.05:
            # Up to ~±6 edge points from persistent news context.
            copy["edge_score"] = round(float(item.get("edge_score") or 0.0) + bias * 6.0, 2)
            copy["news_bias"] = round(bias, 3)
        out.append(copy)
    return out


def context_payload(directory: Path) -> dict:
    ctx = load_context(directory)
    return {
        "summary": ctx.get("summary"),
        "updated_at": ctx.get("updated_at"),
        "memory_size": ctx.get("memory_size", 0),
        "assets": (ctx.get("assets") or [])[:12],
        "recent": (ctx.get("recent") or [])[:20],
        "themes": (ctx.get("themes") or [])[:8],
        "note": (
            "Base de notícias permanente: cada ciclo lê feeds públicos, "
            "grava o que importa e usa esse contexto nas decisões seguintes."
        ),
    }
