from btcsim.notify import _message, material_decision, send_decision


def test_message_lists_moves_in_portuguese():
    text = _message({
        "capital_inicial": 10000,
        "capital_atual": 10450,
        "previsao": 11200,
        "previsao_horizonte": "12 meses",
        "advice": "Mantinha o núcleo em SPY.",
        "actions": [
            {"asset": "SPY", "action": "HOLD", "weight_after_pct": 80, "amount": 8000},
            {"asset": "bitcoin", "action": "SELL", "weight_after_pct": 0, "weight_before_pct": 8, "amount": 0},
            {"asset": "dust", "action": "HOLD", "weight_after_pct": 0.1, "amount": 10},
        ],
    })
    assert "Capital inicial: 10 000 EUR" in text
    assert "Capital atual: 10 450 EUR" in text
    assert "A minha previsão (12 meses): 11 200 EUR" in text
    assert "Mantinha o núcleo em SPY." in text
    assert "Vender bitcoin" in text
    assert "Manter SPY" not in text
    assert "dust" not in text


def test_hold_only_is_not_material():
    assert material_decision({
        "actions": [
            {"asset": "SPY", "action": "HOLD", "weight_before_pct": 80, "weight_after_pct": 80},
        ],
        "capital_atual": 10000,
    }, previous={"capital_atual": 10000, "cautious": []}) is False


def test_buy_with_weight_change_is_material():
    assert material_decision({
        "actions": [
            {"asset": "AAPL", "action": "BUY", "weight_before_pct": 10, "weight_after_pct": 25},
        ],
        "capital_atual": 10000,
    }) is True


def test_send_skipped_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert send_decision({
        "advice": "x",
        "actions": [{"asset": "AAPL", "action": "BUY", "weight_before_pct": 0, "weight_after_pct": 20}],
    }) is False


def test_send_posts_json_when_material(monkeypatch, tmp_path):
    monkeypatch.setenv("NTFY_TOPIC", "topic-teste")
    monkeypatch.setenv("NTFY_URL", "https://ntfy.example")
    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr("btcsim.notify.requests.post", fake_post)
    decision = {
        "advice": "Aumentava AAPL.",
        "actions": [{"asset": "AAPL", "action": "BUY", "weight_before_pct": 0, "weight_after_pct": 20, "amount": 2000}],
        "capital_atual": 10000,
        "at": "2026-09-24T10:00:00+00:00",
    }
    assert send_decision(decision, state_dir=tmp_path) is True
    assert captured["url"] == "https://ntfy.example"
    assert captured["json"]["topic"] == "topic-teste"
    assert "Aumentava AAPL." in captured["json"]["message"]
    # Second identical cycle should not notify.
    assert send_decision({
        "advice": "Mantinha.",
        "actions": [{"asset": "AAPL", "action": "HOLD", "weight_before_pct": 20, "weight_after_pct": 20}],
        "capital_atual": 10000,
        "cautious": [],
        "at": "2026-09-24T11:00:00+00:00",
    }, state_dir=tmp_path) is False
