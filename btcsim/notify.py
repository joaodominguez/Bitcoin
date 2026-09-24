"""Push notifications for virtual portfolio decisions.

Uses ntfy (https://ntfy.sh). The phone subscribes to a private topic and
receives a push each time the automation records a decision. Nothing is sent
when NTFY_TOPIC is empty.
"""

from __future__ import annotations

import os

import requests

ACTION_PT = {"BUY": "Comprar", "SELL": "Vender", "HOLD": "Manter"}


def _message(decision: dict) -> str:
    lines = []
    for action in decision.get("actions") or []:
        weight = float(action.get("weight_after_pct") or 0)
        if weight < 0.5 and action.get("action") != "SELL":
            continue
        label = ACTION_PT.get(action.get("action"), action.get("action"))
        amount = float(action.get("amount") or 0)
        lines.append(f"{label} {action.get('asset')}: {amount:.0f} EUR ({weight:.0f}%)")
    advice = (decision.get("advice") or "").strip()
    body = advice
    if lines:
        body = (advice + "\n\n" if advice else "") + "\n".join(lines)
    return body or "Decisão virtual registada."


def send_decision(decision: dict) -> bool:
    """Send one push. Returns True when ntfy accepts it."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return False
    base = os.environ.get("NTFY_URL", "https://ntfy.sh").rstrip("/")
    payload = {
        "topic": topic,
        "title": "Carteira virtual — nova decisão",
        "message": _message(decision),
        "tags": ["chart"],
        "click": os.environ.get("DASHBOARD_URL", "http://91.99.167.243:8000"),
    }
    response = requests.post(base, json=payload, timeout=20)
    response.raise_for_status()
    return True
