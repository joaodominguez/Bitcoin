"""Persisted UI layout (card order, etc.). Virtual desk preferences only."""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_LAYOUT = {
    "card_order": [],
}


def load_layout(directory: Path) -> dict:
    path = directory / "ui_layout.json"
    if not path.exists():
        return dict(DEFAULT_LAYOUT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return dict(DEFAULT_LAYOUT)
    out = dict(DEFAULT_LAYOUT)
    order = data.get("card_order") or []
    if isinstance(order, list):
        out["card_order"] = [str(x) for x in order if str(x).strip()][:40]
    return out


def save_layout(directory: Path, payload: dict) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    current = load_layout(directory)
    if "card_order" in payload and isinstance(payload["card_order"], list):
        current["card_order"] = [
            str(x) for x in payload["card_order"] if str(x).strip()
        ][:40]
    path = directory / "ui_layout.json"
    path.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return current
