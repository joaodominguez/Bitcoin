from __future__ import annotations

from unittest.mock import MagicMock, patch

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Campaign, Contact, ContactList, ListMember, Message, Suppression
from app.sender import enqueue_campaign, process_campaign_batch, shared_html_template
from app.tokens import parse_unsubscribe_token, unsubscribe_token
from app.unione import UnioneClient


def test_unsubscribe_token_roundtrip():
    tok = unsubscribe_token("a@b.com", 12)
    data = parse_unsubscribe_token(tok)
    assert data["email"] == "a@b.com"
    assert data["cid"] == 12


def test_shared_html_adds_unsub():
    html = shared_html_template("<p>Oi {{name}}</p>")
    assert "{{unsubscribe_url}}" in html


def test_enqueue_and_batch_send():
    with SessionLocal() as session:
        lst = ContactList(name="send-demo")
        session.add(lst)
        session.flush()
        c1 = Contact(email="one@example.com", name="One", status="active")
        c2 = Contact(email="two@example.com", name="Two", status="active")
        c3 = Contact(email="blocked@example.com", name="X", status="active")
        session.add_all([c1, c2, c3])
        session.flush()
        session.add_all([
            ListMember(list_id=lst.id, contact_id=c1.id),
            ListMember(list_id=lst.id, contact_id=c2.id),
            ListMember(list_id=lst.id, contact_id=c3.id),
        ])
        session.add(Suppression(email="blocked@example.com", reason="manual"))
        camp = Campaign(
            name="demo",
            subject="Olá",
            html_body="<p>Hi {{name}}</p>",
            list_id=lst.id,
            status="draft",
        )
        session.add(camp)
        session.flush()
        enqueue_campaign(session, camp)
        assert camp.status == "queued"
        assert camp.total == 2  # suppressed excluded
        session.commit()
        camp_id = camp.id

    client = MagicMock(spec=UnioneClient)
    client.configured = True
    client.send_email.return_value = MagicMock(
        ok=True, job_id="job-1", emails=[], raw={"status": "success"}, error=None
    )

    with SessionLocal() as session:
        camp = session.get(Campaign, camp_id)
        n = process_campaign_batch(session, camp, client=client, batch_size=100)
        session.commit()
        assert n == 2
        assert client.send_email.called
        assert camp.status == "sent"
        assert camp.sent_ok == 2
        sent = session.scalar(
            select(func.count()).select_from(Message).where(
                Message.campaign_id == camp_id, Message.status == "sent"
            )
        )
        assert sent == 2


def test_unione_client_send_payload():
    client = UnioneClient(api_key="k", base_url="https://example.test/v1")
    with patch("app.unione.requests.post") as post:
        post.return_value.ok = True
        post.return_value.json.return_value = {"status": "success", "job_id": "j1", "emails": []}
        from app import config as cfg

        old = cfg.Config.UNIONE_FROM_EMAIL
        cfg.Config.UNIONE_FROM_EMAIL = "from@example.com"
        try:
            result = client.send_email(
                recipients=[{"email": "a@b.com", "substitutions": {"name": "A"}}],
                subject="S",
                html="<b>{{name}}</b>",
            )
        finally:
            cfg.Config.UNIONE_FROM_EMAIL = old
        assert result.ok
        assert result.job_id == "j1"
        args, kwargs = post.call_args
        assert args[0].endswith("/email/send.json")
        assert kwargs["headers"]["X-API-KEY"] == "k"
        body = kwargs["json"]["message"]
        assert body["from_email"] == "from@example.com"
        assert body["recipients"][0]["email"] == "a@b.com"
