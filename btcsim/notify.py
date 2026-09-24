"""Push notifications for virtual portfolio decisions.

Uses ntfy (https://ntfy.sh). Sends only when something material changed —
a buy/sell, a tape fill, or a capital move above the threshold.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

ACTION_PT = {"BUY": "Comprar", "SELL": "Vender", "HOLD": "Manter"}
CAPITAL_MOVE_PCT = 1.0


def _eur(value) -> str:
    return f"{float(value):,.0f} EUR".replace(",", " ")


def _message(decision: dict) -> str:
    lines = []
    if decision.get("capital_inicial") is not None:
        lines.append(f"Capital inicial: {_eur(decision['capital_inicial'])}")
        lines.append(f"Capital atual: {_eur(decision.get('capital_atual', 0))}")
        lines.append(
            f"A minha previsão ({decision.get('previsao_horizonte', '12 meses')}): "
            f"{_eur(decision.get('previsao', 0))}"
        )
        lines.append("")
    for action in decision.get("actions") or []:
        weight = float(action.get("weight_after_pct") or 0)
        if weight < 0.5 and action.get("action") != "SELL":
            continue
        if action.get("action") == "HOLD":
            continue
        label = ACTION_PT.get(action.get("action"), action.get("action"))
        amount = float(action.get("amount") or 0)
        lines.append(f"{label} {action.get('asset')}: {amount:.0f} EUR ({weight:.0f}%)")
    advice = (decision.get("advice") or "").strip()
    body = advice
    if lines:
        body = (advice + "\n\n" if advice else "") + "\n".join(lines)
    return body or "Decisão virtual registada."


def _post(title: str, message: str, tags: list[str]) -> bool:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    base = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    payload = {
        "topic": topic,
        "title": title,
        "message": message,
        "tags": tags,
        "click": os.environ.get("DASHBOARD_URL", "http://91.99.167.243:8000"),
    }
    response = requests.post(base, json=payload, timeout=20)
    response.raise_for_status()
    return True


def material_decision(decision: dict, previous: dict | None = None) -> bool:
    """True when the hourly cycle warrants a push."""
    for action in decision.get("actions") or []:
        if action.get("action") in {"BUY", "SELL"} and (
            action.get("action") == "SELL" or float(action.get("weight_after_pct") or 0) >= 0.5
        ):
            # Ignore HOLD-shaped noise: require a real weight change.
            before = float(action.get("weight_before_pct") or 0)
            after = float(action.get("weight_after_pct") or 0)
            if abs(after - before) >= 2.0:
                return True
    if decision.get("cautious") and previous is not None:
        if set(decision.get("cautious") or []) != set(previous.get("cautious") or []):
            return True
    if previous:
        prev_cap = float(previous.get("capital_atual") or 0)
        now_cap = float(decision.get("capital_atual") or 0)
        if prev_cap > 0 and abs(now_cap - prev_cap) / prev_cap * 100 >= CAPITAL_MOVE_PCT:
            return True
    return False


def send_decision(decision: dict, state_dir: Path | None = None) -> bool:
    """Send one push when the decision is material. Returns True when sent."""
    previous = None
    snap_path = None
    if state_dir is not None:
        snap_path = Path(state_dir) / "last_push.json"
        if snap_path.exists():
            previous = json.loads(snap_path.read_text(encoding="utf-8"))
    if not material_decision(decision, previous):
        return False
    ok = _post("Carteira virtual — mudança", _message(decision), ["chart"])
    if ok and snap_path is not None:
        snap_path.write_text(
            json.dumps(
                {
                    "capital_atual": decision.get("capital_atual"),
                    "cautious": decision.get("cautious") or [],
                    "at": decision.get("at"),
                }
            ),
            encoding="utf-8",
        )
    return ok


def send_tape(event: dict) -> bool:
    """Push one profitable Bitcoin round-trip or a dip buy."""
    title = "Bitcoin — compra virtual" if event.get("side") == "BUY" else "Bitcoin — venda em lucro"
    return _post(title, _message(event), ["bitcoin"])
