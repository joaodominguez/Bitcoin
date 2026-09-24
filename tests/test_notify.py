from btcsim.notify import _message, send_decision


def test_message_lists_positions_in_portuguese():
    text = _message({
        "capital_inicial": 10000,
        "capital_atual": 10450,
        "previsao": 11200,
        "previsao_horizonte": "12 meses",
        "advice": "Mantinha o núcleo em SPY.",
        "actions": [
            {"asset": "SPY", "action": "HOLD", "weight_after_pct": 80, "amount": 8000},
            {"asset": "bitcoin", "action": "SELL", "weight_after_pct": 0, "amount": 0},
            {"asset": "dust", "action": "HOLD", "weight_after_pct": 0.1, "amount": 10},
        ],
    })
    assert "Capital inicial: 10 000 EUR" in text
    assert "Capital atual: 10 450 EUR" in text
    assert "A minha previsão (12 meses): 11 200 EUR" in text
    assert "Mantinha o núcleo em SPY." in text
    assert "Manter SPY: 8000 EUR (80%)" in text
    assert "Vender bitcoin" in text
    assert "dust" not in text


def test_send_skipped_without_topic(monkeypatch):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    assert send_decision({"advice": "x", "actions": []}) is False


def test_send_posts_json(monkeypatch):
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
    assert send_decision({"advice": "Olá", "actions": []}) is True
    assert captured["url"] == "https://ntfy.example"
    assert captured["json"]["topic"] == "topic-teste"
    assert captured["json"]["message"] == "Olá"
