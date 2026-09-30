from __future__ import annotations

import hashlib
import logging
import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import Config
from .models import Campaign, Contact, ListMember, Message, Suppression, utcnow
from .tokens import unsubscribe_token
from .unione import UnioneClient, UnioneError

log = logging.getLogger("mailmkt.sender")


def _suppressed_emails(session: Session) -> set[str]:
    return set(session.scalars(select(Suppression.email)).all())


def eligible_contacts(session: Session, list_id: int | None) -> list[Contact]:
    """Active contacts not on suppression list (optionally filtered by list)."""
    suppressed = _suppressed_emails(session)
    stmt = select(Contact).where(Contact.status == "active")
    if list_id:
        stmt = stmt.join(ListMember, ListMember.contact_id == Contact.id).where(
            ListMember.list_id == list_id
        )
    contacts = []
    for c in session.scalars(stmt).unique().all():
        if c.email.lower() in suppressed:
            continue
        contacts.append(c)
    return contacts


def enqueue_campaign(session: Session, campaign: Campaign) -> Campaign:
    if campaign.status not in ("draft", "paused", "failed"):
        raise ValueError(f"Campanha em estado {campaign.status} não pode ser enfileirada")

    contacts = eligible_contacts(session, campaign.list_id)
    existing = {
        m.contact_id: m
        for m in session.scalars(
            select(Message).where(Message.campaign_id == campaign.id)
        ).all()
    }
    created = 0
    for contact in contacts:
        if contact.id in existing:
            msg = existing[contact.id]
            if msg.status in ("failed", "pending"):
                msg.status = "pending"
                msg.error = None
            continue
        session.add(
            Message(
                campaign_id=campaign.id,
                contact_id=contact.id,
                email=contact.email.lower(),
                status="pending",
            )
        )
        created += 1

    session.flush()
    campaign.total = session.scalar(
        select(func.count()).select_from(Message).where(Message.campaign_id == campaign.id)
    ) or 0
    campaign.status = "queued"
    campaign.started_at = campaign.started_at or utcnow()
    campaign.error = None
    session.flush()
    log.info("campaign %s queued: %s messages (%s new)", campaign.id, campaign.total, created)
    return campaign


def shared_html_template(html: str) -> str:
    """Ensure unsubscribe placeholder exists for UniOne substitutions."""
    if "{{unsubscribe_url}}" not in html:
        html = (
            html
            + '<hr style="border:none;border-top:1px solid #ddd;margin:24px 0">'
            + '<p style="font-size:12px;color:#666">Se não quiseres receber estes emails, '
            + '<a href="{{unsubscribe_url}}">cancela a subscrição</a>.</p>'
        )
    return html


def process_campaign_batch(
    session: Session,
    campaign: Campaign,
    *,
    client: UnioneClient | None = None,
    batch_size: int | None = None,
) -> int:
    """Send up to batch_size pending messages. Returns number attempted."""
    client = client or UnioneClient()
    if not client.configured:
        campaign.status = "failed"
        campaign.error = "UniOne não configurada (UNIONE_API_KEY / UNIONE_FROM_EMAIL)"
        return 0

    batch_size = batch_size or Config.UNIONE_BATCH_SIZE
    pending = session.scalars(
        select(Message)
        .where(Message.campaign_id == campaign.id, Message.status == "pending")
        .order_by(Message.id)
        .limit(batch_size)
    ).all()
    if not pending:
        _finalize_if_done(session, campaign)
        return 0

    campaign.status = "sending"
    session.flush()

    suppressed = _suppressed_emails(session)
    to_send: list[Message] = []
    for msg in pending:
        contact = session.get(Contact, msg.contact_id)
        if not contact or contact.status != "active" or msg.email.lower() in suppressed:
            msg.status = "skipped"
            campaign.skipped = (campaign.skipped or 0) + 1
            continue
        to_send.append(msg)

    if not to_send:
        _finalize_if_done(session, campaign)
        return 0

    recipients = []
    msg_by_email: dict[str, Message] = {}
    for msg in to_send:
        contact = session.get(Contact, msg.contact_id)
        assert contact is not None
        token = unsubscribe_token(contact.email, contact.id)
        unsub = f"{Config.PUBLIC_BASE_URL}/u/{token}"
        recipients.append(
            {
                "email": contact.email,
                "substitutions": {
                    "to_name": (contact.name or contact.email)[:78],
                    "name": contact.name or "",
                    "email": contact.email,
                    "unsubscribe_url": unsub,
                },
                "metadata": {
                    "campaign_id": str(campaign.id),
                    "message_id": str(msg.id),
                    "contact_id": str(contact.id),
                },
            }
        )
        msg_by_email[contact.email.lower()] = msg

    idem = hashlib.sha1(
        f"{campaign.id}:{to_send[0].id}:{to_send[-1].id}".encode()
    ).hexdigest()[:40]

    result = client.send_email(
        recipients=recipients,
        subject=campaign.subject,
        html=shared_html_template(campaign.html_body),
        plaintext=campaign.text_body,
        from_email=campaign.from_email or Config.UNIONE_FROM_EMAIL,
        from_name=campaign.from_name or Config.UNIONE_FROM_NAME,
        campaign_id=campaign.id,
        idempotence_key=idem,
        tags=["mail-mkt", f"c{campaign.id}"][:4],
    )

    now = utcnow()
    if not result.ok:
        for msg in to_send:
            msg.status = "failed"
            msg.error = (result.error or "send failed")[:500]
            campaign.sent_fail = (campaign.sent_fail or 0) + 1
        campaign.error = result.error
        session.flush()
        return len(to_send)

    by_result = {}
    for item in result.emails:
        if isinstance(item, dict) and item.get("email"):
            by_result[str(item["email"]).lower()] = item

    for email, msg in msg_by_email.items():
        item = by_result.get(email)
        failed = bool(item and item.get("status") in ("error", "failed", "rejected"))
        if failed:
            msg.status = "failed"
            msg.error = str(item.get("message") or item)[:500]
            campaign.sent_fail = (campaign.sent_fail or 0) + 1
        else:
            msg.status = "sent"
            msg.unione_job_id = result.job_id
            if item:
                msg.unione_email_id = str(item.get("id") or item.get("email_id") or "") or None
            msg.sent_at = now
            campaign.sent_ok = (campaign.sent_ok or 0) + 1

    campaign.error = None
    session.flush()
    _finalize_if_done(session, campaign)
    return len(to_send)


def _finalize_if_done(session: Session, campaign: Campaign) -> None:
    pending = session.scalar(
        select(func.count()).select_from(Message).where(
            Message.campaign_id == campaign.id, Message.status == "pending"
        )
    ) or 0
    if pending == 0 and campaign.status in ("sending", "queued"):
        campaign.status = "sent"
        campaign.finished_at = utcnow()
        campaign.sent_ok = session.scalar(
            select(func.count()).select_from(Message).where(
                Message.campaign_id == campaign.id, Message.status == "sent"
            )
        ) or 0
        campaign.sent_fail = session.scalar(
            select(func.count()).select_from(Message).where(
                Message.campaign_id == campaign.id, Message.status == "failed"
            )
        ) or 0
        campaign.skipped = session.scalar(
            select(func.count()).select_from(Message).where(
                Message.campaign_id == campaign.id, Message.status == "skipped"
            )
        ) or 0


def process_next_batch(session: Session, client: UnioneClient | None = None) -> int:
    campaign = session.scalar(
        select(Campaign)
        .where(Campaign.status.in_(("queued", "sending")))
        .order_by(Campaign.id)
        .limit(1)
    )
    if not campaign:
        return 0
    return process_campaign_batch(session, campaign, client=client)


def send_test_email(
    *,
    to_email: str,
    subject: str,
    html: str,
    client: UnioneClient | None = None,
) -> dict:
    client = client or UnioneClient()
    token = unsubscribe_token(to_email)
    unsub = f"{Config.PUBLIC_BASE_URL}/u/{token}"
    result = client.send_email(
        recipients=[{
            "email": to_email,
            "substitutions": {
                "to_name": "Teste",
                "name": "Teste",
                "email": to_email,
                "unsubscribe_url": unsub,
            },
        }],
        subject=subject,
        html=shared_html_template(html),
        tags=["mail-mkt", "test"],
        idempotence_key=hashlib.sha1(
            f"test:{to_email}:{time.time()}".encode()
        ).hexdigest()[:40],
    )
    if not result.ok:
        raise UnioneError(result.error or "Falha no envio de teste")
    return {"job_id": result.job_id, "raw": result.raw}
